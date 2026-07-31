"""M13 §3.3 -- an action turn that executed nothing may never report success.

Evidence: session 94bf07c2 turn 2. The user said "Play ishq song."; the model
returned the final text "Done." with zero tool calls, and the loop recorded that
as a completed turn. Nothing had happened.

The guard is deliberately a control-flow branch rather than a line in the system
prompt: a model that ignores prose (and this one did) cannot ignore a branch that
refuses to accept its answer.
"""

import unittest

from app.services.agent.agent_loop import AgentLoop


class FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.type = "function"
        self.function = FakeFunction(name, arguments)


class FakeMessage:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeCompletion:
    def __init__(self, message):
        self.choices = [type("C", (), {"message": message})()]


def make_loop(scripted_messages):
    """An AgentLoop with no providers and a scripted sequence of LLM replies."""
    loop = object.__new__(AgentLoop)
    loop._clients = []
    loop._gemini_clients = []
    loop._zai_clients = []
    loop.last_provider_event = None
    loop._registry = None
    loop._calls = []

    replies = list(scripted_messages)

    def fake_chat_completion(messages, key_index):
        loop._calls.append([dict(m) for m in messages])
        if not replies:
            return FakeCompletion(FakeMessage(content="nothing left to say"))
        return FakeCompletion(replies.pop(0))

    loop._chat_completion = fake_chat_completion
    loop._build_state_block = lambda *a, **k: ""
    return loop


def drain(loop, **kwargs):
    events = list(loop.run_stream("play ishq song", [], **kwargs))
    final = ""
    activity = []
    executed = []
    for event in events:
        if "_final" in event:
            final = event["_final"]
        elif "_activity" in event:
            activity.append(event["_activity"])
        elif "_executed" in event:
            executed = event["_executed"]["steps"]
    return final, activity, executed


class ComposeCompletion:
    def __init__(self, content, finish_reason="stop"):
        message = type("M", (), {"content": content})()
        choice = type("C", (), {"message": message, "finish_reason": finish_reason})()
        self.choices = [choice]


class ComposeClient:
    def __init__(self, completions):
        self._queue = list(completions)
        self.calls = []
        outer = self

        class Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                item = outer._queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

        self.chat = type("Chat", (), {"completions": Completions()})()


def compose_loop(completions):
    loop = object.__new__(AgentLoop)
    loop._clients = []
    loop._zai_clients = []
    loop._reasoning_effort = "none"
    client = ComposeClient(completions)
    loop._gemini_clients = [client]
    return loop, client


class ComposedWordingTests(unittest.TestCase):
    """A truncated admission is worse than a templated one.

    Observed live: the honest reply came back as "I attempted" -- not a sentence,
    and it reads as a success claim, which is the exact failure M13 removes. The
    cause was the same reasoning-model budget trap as the main loop.
    """

    def test_a_complete_sentence_is_used(self):
        loop, _client = compose_loop([
            ComposeCompletion("I opened it but could not confirm it took effect.")])
        self.assertEqual(loop._compose("say something", "FALLBACK"),
                         "I opened it but could not confirm it took effect.")

    def test_a_length_truncated_reply_is_rejected_for_the_plain_wording(self):
        loop, _client = compose_loop([
            ComposeCompletion("I attempted", finish_reason="length")])
        self.assertEqual(loop._compose("say something", "FALLBACK"), "FALLBACK")

    def test_a_two_word_reply_is_rejected_even_when_it_claims_to_be_complete(self):
        loop, _client = compose_loop([ComposeCompletion("I attempted")])
        self.assertEqual(loop._compose("say something", "FALLBACK"), "FALLBACK")

    def test_an_empty_reply_is_rejected(self):
        loop, _client = compose_loop([ComposeCompletion("   ")])
        self.assertEqual(loop._compose("say something", "FALLBACK"), "FALLBACK")

    def test_line_breaks_are_flattened_not_truncated(self):
        loop, _client = compose_loop([
            ComposeCompletion("I opened the page,\nbut could not confirm it.")])
        self.assertEqual(loop._compose("say something", "FALLBACK"),
                         "I opened the page, but could not confirm it.")

    def test_thinking_is_switched_off_for_composed_replies_too(self):
        loop, client = compose_loop([
            ComposeCompletion("A complete honest sentence here.")])
        loop._compose("say something", "FALLBACK")
        self.assertEqual(client.calls[0].get("reasoning_effort"), "none")
        self.assertGreaterEqual(client.calls[0].get("max_tokens", 0), 400)

    def test_a_provider_error_falls_back_rather_than_raising(self):
        loop, _client = compose_loop([RuntimeError("provider down")])
        self.assertEqual(loop._compose("say something", "FALLBACK"), "FALLBACK")

    def test_the_unconfirmed_fallback_never_reads_as_success(self):
        loop, _client = compose_loop([ComposeCompletion("x", finish_reason="length")])
        text = loop.compose_unconfirmed(
            "open example.com", "Opening https://example.com in the browser.",
            [{"tool": "open_website", "verdict": "UNKNOWN", "reason": "no ack"}])
        self.assertIn("could not confirm", text.lower())

    def test_the_no_op_fallback_never_says_done(self):
        loop, _client = compose_loop([ComposeCompletion("x", finish_reason="length")])
        text = loop._no_op_admission("play ishq song", "Done.")
        self.assertNotEqual(text.strip().lower().rstrip("."), "done")
        self.assertIn("wasn't able", text.lower())


class NoOpGuardTests(unittest.TestCase):
    def test_text_only_action_turn_is_nudged(self):
        loop = make_loop([FakeMessage(content="Done."),
                          FakeMessage(content="Done.")])
        final, activity, executed = drain(loop, expect_action=True)

        events = [a.get("event") for a in activity]
        self.assertIn("no_op_rejected", events,
                      "a text-only reply on an action turn must be rejected")
        self.assertEqual(executed, [])
        # The nudge must actually reach the model.
        self.assertGreaterEqual(len(loop._calls), 2)
        second_pass = loop._calls[1]
        self.assertTrue(
            any(m.get("role") == "system" and "did not call any tool" in m.get("content", "")
                for m in second_pass),
            "the nudge must be appended as a system turn on the retry")

    def test_a_stubborn_model_gets_an_honest_failure_not_done(self):
        loop = make_loop([FakeMessage(content="Done."),
                          FakeMessage(content="Done.")])
        final, _activity, executed = drain(loop, expect_action=True)

        self.assertEqual(executed, [])
        self.assertNotEqual(final.strip().lower().rstrip("."), "done")
        self.assertTrue(final.strip(), "the turn must still say something")

    def test_only_one_nudge_is_spent(self):
        loop = make_loop([FakeMessage(content="Done.")] * 5)
        drain(loop, expect_action=True)
        # 1 first attempt + 1 nudged attempt. Not an unbounded scold loop.
        self.assertEqual(len(loop._calls), 2)

    def test_a_conversational_turn_is_left_alone(self):
        loop = make_loop([FakeMessage(content="Ishq is a song by Fahim Abdullah.")])
        final, activity, executed = drain(loop, expect_action=False)

        self.assertEqual(final, "Ishq is a song by Fahim Abdullah.")
        self.assertNotIn("no_op_rejected", [a.get("event") for a in activity])
        self.assertEqual(executed, [])
        self.assertEqual(len(loop._calls), 1)

    def test_an_action_turn_that_did_act_may_finish_normally(self):
        """Once a tool has run, a text-only final message is legitimate."""
        loop = make_loop([
            FakeMessage(tool_calls=[FakeToolCall("c1", "get_datetime", "{}")]),
            FakeMessage(content="It is just past four."),
        ])
        final, activity, executed = drain(loop, expect_action=True)

        self.assertNotIn("no_op_rejected", [a.get("event") for a in activity])
        self.assertEqual(final, "It is just past four.")
        self.assertEqual([s["tool"] for s in executed], ["get_datetime"])

    def test_an_empty_completion_is_a_provider_failure_not_an_answer(self):
        """A reasoning model that spends its whole budget thinking returns
        content='' with no tool calls. Accepting that as the final answer is how
        a turn reports nothing having done nothing -- seen live on "what's my
        battery level", where Gemini did exactly this twice."""
        loop = object.__new__(AgentLoop)
        loop._clients = []
        loop._gemini_clients = []
        loop._zai_clients = []
        self.assertFalse(loop._is_usable(FakeCompletion(FakeMessage(content=""))))
        self.assertFalse(loop._is_usable(FakeCompletion(FakeMessage(content="   "))))
        self.assertTrue(loop._is_usable(FakeCompletion(FakeMessage(content="hi"))))
        self.assertTrue(loop._is_usable(FakeCompletion(FakeMessage(
            tool_calls=[FakeToolCall("c1", "get_datetime", "{}")]))))

    def test_a_broken_completion_object_is_not_usable(self):
        loop = object.__new__(AgentLoop)
        self.assertFalse(loop._is_usable(object()))

    def test_executed_steps_are_reported_for_verification(self):
        loop = make_loop([
            FakeMessage(tool_calls=[FakeToolCall("c1", "get_datetime", "{}")]),
            FakeMessage(content="Right now it's afternoon."),
        ])
        _final, _activity, executed = drain(loop, expect_action=True)
        self.assertEqual(len(executed), 1)
        self.assertTrue(executed[0]["action_id"])
        self.assertIn("args", executed[0])


if __name__ == "__main__":
    unittest.main()
