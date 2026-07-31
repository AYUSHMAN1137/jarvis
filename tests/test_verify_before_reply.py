"""M13 §3.4 -- verify before speaking, retry once, then admit.

Verification runs on the event bus, so the verdict used to land ~4s AFTER the
reply had already streamed: JARVIS said "done", and the correction could only
arrive as a late bubble. The turn now waits, boundedly, for the truth.

Everything here uses fakes: no browser, no bus, no network, no clock sleeping
beyond a few hundred milliseconds.
"""

import unittest

from app.services.chat_service import ChatService


def run_gen(generator):
    """Drain a generator, returning (events, return_value)."""
    events = []
    while True:
        try:
            events.append(next(generator))
        except StopIteration as stop:
            return events, stop.value


class FakePhase4:
    def __init__(self, verdicts):
        # action_id -> list of verdict payloads, consumed in order so a retry can
        # produce a different outcome from the first attempt.
        self._verdicts = {k: list(v) for k, v in verdicts.items()}
        self.waits = []

    def wait_for_verdict(self, action_id, timeout=3.0):
        self.waits.append((action_id, timeout))
        queue = self._verdicts.get(action_id) or []
        if not queue:
            return None
        return queue.pop(0)


class FakeAgentLoop:
    """Scriptable stand-in for the real loop's streaming contract."""

    def __init__(self, passes=None):
        self.passes = list(passes or [])
        self.calls = []
        self.composed = []

    def run_stream(self, user_message, chat_history=None, key_index=0,
                   confirmed_tools=None, expect_action=False):
        self.calls.append(user_message)
        if not self.passes:
            plan = {"final": "nothing more to do", "executed": []}
        else:
            plan = self.passes.pop(0)
        yield {"_activity": {"event": "agent_started"}}
        yield {"_executed": {"execution_id": "exec", "steps": plan["executed"]}}
        yield {"_final": plan["final"]}

    def compose_unconfirmed(self, goal, draft, verdicts):
        self.composed.append({"goal": goal, "draft": draft, "verdicts": verdicts})
        return "I ran it but could not confirm it took effect."


def make_service(agent_loop):
    service = ChatService(groq_service=None, agent_loop=agent_loop)
    service.sessions["s1"] = []
    return service


STEP_OPEN = {"action_id": "a1", "tool": "open_website",
             "args": {"target": "youtube"}}
STEP_WRITE = {"action_id": "a2", "tool": "write_file",
              "args": {"path": "desktop/x.txt"}}


class VerifyBeforeReplyTests(unittest.TestCase):
    def _settle(self, service, phase4, draft="Opening YouTube.",
                executed=(STEP_OPEN,)):
        service._phase4_ready = lambda: phase4
        gen = service._settle_before_reply(
            "s1", "open youtube", [], draft, list(executed), chat_idx=0)
        return run_gen(gen)

    def test_pass_keeps_the_confident_reply(self):
        loop = FakeAgentLoop()
        service = make_service(loop)
        phase4 = FakePhase4({"a1": [{"verdict": "PASS", "tool": "open_website",
                                     "reason": "browser accepted the action"}]})
        _events, text = self._settle(service, phase4)

        self.assertEqual(text, "Opening YouTube.")
        self.assertEqual(loop.calls, [], "a PASS must not trigger a retry")
        self.assertEqual(loop.composed, [])

    def test_fail_triggers_exactly_one_retry(self):
        loop = FakeAgentLoop([
            {"final": "Opened YouTube.", "executed": [STEP_OPEN]},
        ])
        service = make_service(loop)
        phase4 = FakePhase4({"a1": [
            {"verdict": "FAIL", "tool": "open_website",
             "reason": "browser rejected the action"},
            {"verdict": "PASS", "tool": "open_website",
             "reason": "browser accepted the action"},
        ]})
        events, text = self._settle(service, phase4)

        self.assertEqual(len(loop.calls), 1, "exactly one retry, never two")
        self.assertIn("browser rejected", loop.calls[0],
                      "the retry must carry the failure reason into the goal")
        self.assertEqual(text, "Opened YouTube.")
        self.assertIn("retrying", [e.get("_activity", {}).get("event") for e in events])

    def test_fail_twice_admits_instead_of_claiming_success(self):
        loop = FakeAgentLoop([
            {"final": "Opened YouTube.", "executed": [STEP_OPEN]},
        ])
        service = make_service(loop)
        phase4 = FakePhase4({"a1": [
            {"verdict": "FAIL", "tool": "open_website", "reason": "rejected"},
            {"verdict": "FAIL", "tool": "open_website", "reason": "rejected again"},
        ]})
        _events, text = self._settle(service, phase4)

        self.assertEqual(len(loop.composed), 1)
        self.assertEqual(text, "I ran it but could not confirm it took effect.")

    def test_unknown_states_the_limit_and_does_not_retry(self):
        loop = FakeAgentLoop()
        service = make_service(loop)
        phase4 = FakePhase4({"a1": [{"verdict": "UNKNOWN", "tool": "open_website",
                                     "reason": "browser never acknowledged"}]})
        _events, text = self._settle(service, phase4)

        self.assertEqual(loop.calls, [], "UNKNOWN is not a failure to retry")
        self.assertEqual(text, "I ran it but could not confirm it took effect.")

    def test_timeout_is_treated_as_unknown_and_never_hangs(self):
        loop = FakeAgentLoop()
        service = make_service(loop)
        phase4 = FakePhase4({})  # every wait returns None
        _events, text = self._settle(service, phase4)

        self.assertEqual(text, "I ran it but could not confirm it took effect.")
        self.assertEqual(loop.calls, [])

    def test_the_wait_budget_is_per_turn_not_per_action(self):
        loop = FakeAgentLoop()
        service = make_service(loop)
        phase4 = FakePhase4({
            "a1": [{"verdict": "PASS", "tool": "open_website"}],
            "a2": [{"verdict": "PASS", "tool": "open_website"}],
        })
        second = dict(STEP_OPEN, action_id="a2")
        self._settle(service, phase4, executed=(STEP_OPEN, second))

        timeouts = [t for _a, t in phase4.waits]
        self.assertEqual(len(timeouts), 2)
        self.assertLessEqual(timeouts[1], timeouts[0],
                             "the second action shares the remaining budget")

    def test_an_unsafe_action_is_never_retried_automatically(self):
        loop = FakeAgentLoop([{"final": "written", "executed": [STEP_WRITE]}])
        service = make_service(loop)
        phase4 = FakePhase4({"a2": [
            {"verdict": "FAIL", "tool": "write_file", "reason": "file not found"},
        ]})
        service._phase4_ready = lambda: phase4
        gen = service._settle_before_reply(
            "s1", "save a note", [], "Saved it.", [STEP_WRITE], chat_idx=0)
        _events, text = run_gen(gen)

        self.assertEqual(loop.calls, [],
                         "writing twice is a real duplicate effect, never auto-retried")
        self.assertEqual(text, "I ran it but could not confirm it took effect.")

    def test_the_verdict_is_remembered_for_the_next_turn(self):
        loop = FakeAgentLoop()
        service = make_service(loop)
        phase4 = FakePhase4({"a1": [{"verdict": "UNKNOWN", "tool": "open_website"}]})
        self._settle(service, phase4)

        self.assertEqual(service._last_goals["s1"]["verdict"], "UNKNOWN")

    def test_disabled_flag_restores_the_old_behaviour(self):
        import config as _cfg
        saved = _cfg.VERIFY_BEFORE_REPLY
        _cfg.VERIFY_BEFORE_REPLY = False
        self.addCleanup(lambda: setattr(_cfg, "VERIFY_BEFORE_REPLY", saved))

        loop = FakeAgentLoop()
        service = make_service(loop)
        phase4 = FakePhase4({"a1": [{"verdict": "FAIL", "tool": "open_website"}]})
        _events, text = self._settle(service, phase4)

        self.assertEqual(text, "Opening YouTube.")
        self.assertEqual(phase4.waits, [])

    def test_no_phase4_means_no_wait_and_no_change(self):
        loop = FakeAgentLoop()
        service = make_service(loop)
        service._phase4_ready = lambda: None
        gen = service._settle_before_reply(
            "s1", "open youtube", [], "Opening YouTube.", [STEP_OPEN], chat_idx=0)
        _events, text = run_gen(gen)
        self.assertEqual(text, "Opening YouTube.")


if __name__ == "__main__":
    unittest.main()
