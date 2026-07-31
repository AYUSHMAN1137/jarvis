"""M13 §4.7 -- replay of session 94bf07c2, turn by turn.

The evidence session, and what it did:

| # | User                     | Then                          | Must now         |
|---|--------------------------|-------------------------------|------------------|
| 1 | Open YouTube.            | acted, verdict UNKNOWN        | acts             |
| 2 | Play ishq song.          | said "Done.", ZERO tool calls | acts or admits   |
| 3 | It's not playing.        | general -> small talk         | reaches the agent|
| 4 | It's a Pakistani song.   | general -> unkeepable promise | reaches the agent|
| 5 | Bai Fahim Abdullah.      | general -> unkeepable promise | reaches the agent|
| 6 | Search for it.           | search_google(query="it")     | never gets "it"  |

The agent loop, the LLM and Phase 4 are all fakes. What is under test is the
routing decision: which path the turn takes, and what text that path receives.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app.services.chat_service as cs_module
from app.services.chat_service import ChatService
from app.services.resolver import Resolution, SOURCE_LLM, SOURCE_OFFLINE


class RecordingAgentLoop:
    """Captures every goal handed to the agent, and never calls an LLM."""

    def __init__(self):
        self.goals = []
        self.composed = []
        self.unconfirmed = []

    def run_stream(self, user_message, chat_history=None, key_index=0,
                   confirmed_tools=None, expect_action=False):
        self.goals.append(user_message)
        yield {"_activity": {"event": "agent_started"}}
        yield {"_executed": {"execution_id": "e1", "steps": []}}
        yield {"_final": "acted on it"}

    def _build_state_block(self, *args, **kwargs):
        return ""

    def _compose(self, instruction, fallback):
        self.composed.append(instruction)
        return fallback

    def compose_unconfirmed(self, goal, draft, verdicts):
        # Mirrors the real thing: a NEW sentence stating the limit, not the draft.
        self.unconfirmed.append({"goal": goal, "draft": draft, "verdicts": verdicts})
        return "I ran it but could not confirm it took effect."


class RecordingChatModel:
    """Stands in for GroqService / RealtimeService."""

    def __init__(self, label):
        self.label = label
        self.questions = []

    def stream_response(self, question, chat_history=None, key_start_index=0):
        self.questions.append(question)
        yield f"[{self.label}] answer"


class ScriptedResolver:
    """Returns a prepared Resolution per turn, in order."""

    def __init__(self, resolutions):
        self.resolutions = list(resolutions)
        self.seen = []
        self.last_provider_event = None

    def resolve(self, utterance, **kwargs):
        self.seen.append((utterance, kwargs))
        if not self.resolutions:
            return Resolution(goal=utterance, source=SOURCE_OFFLINE)
        return self.resolutions.pop(0)


def resolution(goal, kind, **kw):
    return Resolution(goal=goal, kind=kind, source=SOURCE_LLM,
                      confidence=0.9, **kw)


class RoutingTestCase(unittest.TestCase):
    """Base class: builds an isolated harness and always tears it down."""

    def harness(self, resolutions):
        built = RoutingHarness(resolutions)
        self.addCleanup(built.close)
        return built


class RoutingHarness:
    def __init__(self, resolutions):
        # A routing test must never write into the owner's real conversations.
        self._tmp = tempfile.TemporaryDirectory()
        self._patcher = patch.object(cs_module, "CHATS_DATA_DIR",
                                     Path(self._tmp.name))
        self._patcher.start()
        self.agent = RecordingAgentLoop()
        self.chat = RecordingChatModel("general")
        self.realtime = RecordingChatModel("realtime")
        self.service = ChatService(self.chat, self.realtime,
                                   agent_loop=self.agent)
        self.resolver = ScriptedResolver(resolutions)
        self.service._understand = (
            lambda session_id, msg, hist, pending=None: self.resolver.resolve(msg))
        # Never touch the real cache or the real verifier from a routing test.
        self.service._try_cache_replay = self._no_cache
        self.service._phase4_ready = lambda: None

    def close(self):
        self._patcher.stop()
        self._tmp.cleanup()

    @staticmethod
    def _no_cache(*_args, **_kwargs):
        return False
        yield  # pragma: no cover - makes this a generator

    def turn(self, message, session_id="s1"):
        self.service.get_or_create_session(session_id)
        chunks = []
        for event in self.service._jarvis_stream_impl(session_id, message):
            if isinstance(event, str):
                chunks.append(event)
        return "".join(chunks)


class EvidenceSessionReplayTests(RoutingTestCase):
    def test_turn_1_open_youtube_reaches_the_agent(self):
        harness = self.harness([resolution("open YouTube in the browser", "action")])
        harness.turn("Open YouTube.")
        self.assertEqual(harness.agent.goals, ["open YouTube in the browser"])
        self.assertEqual(harness.chat.questions, [])

    def test_turn_2_play_a_song_reaches_the_agent(self):
        harness = self.harness([
            resolution("play the song Ishq on YouTube", "action")])
        harness.turn("Play ishq song.")
        self.assertEqual(harness.agent.goals, ["play the song Ishq on YouTube"])

    def test_turn_3_a_complaint_reaches_the_agent_not_small_talk(self):
        harness = self.harness([resolution(
            "play the song Ishq on YouTube (the last attempt did nothing)",
            "action", self_contained=False, refers_to_previous=True)])
        harness.turn("It's not playing.")
        self.assertEqual(len(harness.agent.goals), 1)
        self.assertEqual(harness.chat.questions, [],
                         "a failed action must never be answered with chat")

    def test_turn_6_never_receives_the_literal_pronoun(self):
        harness = self.harness([resolution(
            "search for the song Ishq by Fahim Abdullah", "action",
            self_contained=False, refers_to_previous=True)])
        harness.turn("Search for it.")
        goal = harness.agent.goals[0]
        self.assertNotEqual(goal.strip().lower(), "it")
        self.assertIn("Fahim Abdullah", goal)

    def test_the_whole_session_never_reaches_conversation_mode(self):
        harness = self.harness([
            resolution("open YouTube in the browser", "action"),
            resolution("play the song Ishq on YouTube", "action"),
            resolution("play the song Ishq on YouTube again", "action",
                       self_contained=False, refers_to_previous=True),
            resolution("play the Pakistani song Ishq on YouTube", "action",
                       self_contained=False, refers_to_previous=True),
            resolution("play Ishq by Fahim Abdullah on YouTube", "action",
                       self_contained=False, refers_to_previous=True),
            resolution("search for Ishq by Fahim Abdullah", "action",
                       self_contained=False, refers_to_previous=True),
        ])
        for message in ["Open YouTube.", "Play ishq song.", "It's not playing.",
                        "It's a Pakistani song.", "Bai Fahim Abdullah.",
                        "Search for it."]:
            harness.turn(message)
        self.assertEqual(len(harness.agent.goals), 6)
        self.assertEqual(harness.chat.questions, [])
        self.assertEqual(harness.realtime.questions, [])


class StateGuardTests(RoutingTestCase):
    """§4.2 guard #2: a STATE check, not a language check."""

    def test_an_unverified_last_action_pulls_a_follow_up_to_the_agent(self):
        harness = self.harness([resolution(
            "tell me about the song", "knowledge_question",
            refers_to_previous=True)])
        harness.service._last_goals["s1"] = {
            "goal": "play Ishq on YouTube", "verdict": "UNKNOWN"}
        harness.turn("so what happened")
        self.assertEqual(len(harness.agent.goals), 1,
                         "the recorded verdict alone must reroute this")
        self.assertEqual(harness.chat.questions, [])

    def test_a_verified_last_action_leaves_chat_alone(self):
        harness = self.harness([resolution(
            "tell me about the song Ishq", "knowledge_question",
            refers_to_previous=True)])
        harness.service._last_goals["s1"] = {
            "goal": "play Ishq on YouTube", "verdict": "PASS"}
        harness.turn("tell me about it")
        self.assertEqual(harness.agent.goals, [])
        self.assertEqual(len(harness.chat.questions), 1)

    def test_a_fresh_session_does_not_reroute_chat(self):
        harness = self.harness([resolution(
            "say hello back", "knowledge_question")])
        harness.turn("hey")
        self.assertEqual(harness.agent.goals, [])
        self.assertEqual(len(harness.chat.questions), 1)


class KindRoutingTests(RoutingTestCase):
    def test_a_web_question_goes_to_realtime_with_the_resolved_goal(self):
        harness = self.harness([resolution(
            "who is Fahim Abdullah", "web_question", self_contained=False,
            refers_to_previous=True)])
        harness.turn("who is he")
        self.assertEqual(harness.realtime.questions, ["who is Fahim Abdullah"],
                         "search works on the resolved goal, not the pronoun")
        self.assertEqual(harness.agent.goals, [])

    def test_a_knowledge_question_keeps_the_owner_s_own_wording(self):
        harness = self.harness([resolution(
            "explain what a promise is in JavaScript", "knowledge_question")])
        harness.turn("what's a promise")
        self.assertEqual(harness.chat.questions, ["what's a promise"])

    def test_a_camera_request_takes_the_camera_route(self):
        harness = self.harness([resolution(
            "describe what the owner is holding", "visual",
            visual_source="camera")])
        reply = harness.turn("what am I holding")
        self.assertEqual(harness.agent.goals, [])
        self.assertIn("look", reply.lower())

    def test_an_on_screen_question_goes_to_the_agent_to_actually_look(self):
        harness = self.harness([resolution(
            "read the options on the Settings page on screen", "visual",
            visual_source="screen")])
        harness.turn("what options are on this page")
        self.assertEqual(len(harness.agent.goals), 1)
        self.assertEqual(harness.chat.questions, [])

    def test_mixed_runs_the_action_and_then_answers(self):
        harness = self.harness([resolution(
            "generate an image of a neural network and explain machine learning",
            "mixed")])
        harness.turn("what is ML? also draw a neural network")
        self.assertEqual(len(harness.agent.goals), 1)
        self.assertEqual(len(harness.realtime.questions), 1)

    def test_a_mixed_turn_verifies_its_action_before_answering(self):
        """Mixed must not be the one route where an action can fail silently
        while the conversational half reads like everything went fine."""
        harness = self.harness([resolution(
            "open the docs site and explain generators", "mixed")])
        step = {"action_id": "a1", "tool": "open_website", "args": {}}
        harness.agent.run_stream = lambda *a, **k: iter([
            {"_executed": {"execution_id": "e1", "steps": [step]}},
            {"_final": "Opened it."},
        ])
        harness.service._phase4_ready = lambda: type("P", (), {
            "wait_for_verdict": staticmethod(
                lambda action_id, timeout=3.0: {
                    "tool": "open_website", "verdict": "UNKNOWN",
                    "reason": "browser never acknowledged"})
        })()
        reply = harness.turn("open the docs and explain generators")

        self.assertEqual(len(harness.realtime.questions), 1,
                         "the conversational half still runs")
        self.assertIn("could not confirm", reply.lower())
        self.assertTrue(reply.index("could not confirm") <
                        reply.index("[realtime] answer"),
                        "the caveat must lead, not trail the answer")


class ClarificationRoutingTests(RoutingTestCase):
    def test_an_unresolved_turn_asks_instead_of_acting(self):
        harness = self.harness([resolution(
            "send an email", "action", unresolved=["who it should go to"])])
        reply = harness.turn("send that email")
        self.assertEqual(harness.agent.goals, [],
                         "guessing a recipient is worse than asking")
        self.assertIn("who it should go to", reply)


class OfflineRoutingTests(RoutingTestCase):
    def test_no_reasoning_engine_and_no_cache_is_an_honest_error(self):
        harness = self.harness([Resolution(goal="open youtube",
                                             source=SOURCE_OFFLINE)])
        reply = harness.turn("open youtube")
        self.assertEqual(harness.agent.goals, [])
        self.assertEqual(harness.chat.questions, [])
        self.assertIn("reasoning engine", reply.lower())

    def test_a_cache_replay_speaks_from_what_the_tool_reported(self):
        """A promoted entry stores tool+args, not a sentence, so a hit used to
        reply "Done." for everything. Use the tool's own words instead."""
        service = self.harness([]).service

        class Result:
            def __init__(self, observation):
                self.observation = observation

        self.assertEqual(
            service._reply_from_results([Result("Opening https://example.com.")], True),
            "Opening https://example.com.")
        self.assertEqual(
            service._reply_from_results([Result("ERROR: nope")], True), "Done.")
        self.assertEqual(
            service._reply_from_results([Result("x" * 900)], True), "Done.")
        self.assertIn("couldn't",
                      service._reply_from_results([Result("fine")], False))

    def test_a_verified_cache_entry_still_replays_with_no_llm(self):
        harness = self.harness([Resolution(goal="open youtube",
                                             source=SOURCE_OFFLINE)])
        served = []

        def fake_replay(session_id, command, t0):
            served.append(command)
            harness.service.sessions[session_id][-1].content = "Opening YouTube."
            yield "Opening YouTube."
            return True

        harness.service._try_cache_replay = fake_replay
        reply = harness.turn("open youtube")
        self.assertEqual(served, ["open youtube"])
        self.assertEqual(reply, "Opening YouTube.")


class ConfirmationRoutingTests(RoutingTestCase):
    def _pending(self, harness):
        harness.service._pending_confirmations["s1"] = {
            "tool": "delete_file", "arguments": {"path": "old.txt"},
            "action_id": "a1", "original_message": "delete old.txt",
        }

    def test_a_yes_runs_exactly_the_approved_action(self):
        harness = self.harness([resolution(
            "delete old.txt", "action", is_confirmation=True,
            refers_to_previous=True)])
        self._pending(harness)
        ran = []
        harness.service._run_confirmed_action = (
            lambda session_id, pending, t0: iter([ran.append(pending) or "Done."]))
        harness.turn("haan kar do")
        self.assertEqual(len(ran), 1)
        self.assertEqual(ran[0]["tool"], "delete_file")
        self.assertNotIn("s1", harness.service._pending_confirmations)

    def test_neither_yes_nor_no_keeps_the_action_pending_and_asks_again(self):
        harness = self.harness([resolution(
            "what's the weather", "web_question", is_confirmation=None)])
        self._pending(harness)
        reply = harness.turn("what's the weather like")
        self.assertIn("s1", harness.service._pending_confirmations,
                      "a pending action must not be silently dropped")
        self.assertIn("delete_file", reply)
        self.assertEqual(harness.realtime.questions, [])

    def test_a_no_cancels_and_lets_the_turn_continue(self):
        harness = self.harness([resolution(
            "say ok", "knowledge_question", is_confirmation=False)])
        self._pending(harness)
        harness.turn("nahi rehne do")
        self.assertNotIn("s1", harness.service._pending_confirmations)
        self.assertEqual(len(harness.chat.questions), 1)


class TransparencyTests(RoutingTestCase):
    """§4.6 -- the owner sees what was understood every turn."""

    def test_the_understood_goal_is_emitted_as_an_activity_event(self):
        harness = self.harness([resolution(
            "play Ishq by Fahim Abdullah on YouTube", "action")])
        harness.service.get_or_create_session("s1")
        events = [e for e in harness.service._jarvis_stream_impl("s1", "play it")
                  if isinstance(e, dict)]
        understood = [e["_activity"] for e in events
                      if e.get("_activity", {}).get("event") == "understood"]
        self.assertEqual(len(understood), 1)
        self.assertEqual(understood[0]["goal"],
                         "play Ishq by Fahim Abdullah on YouTube")
        self.assertIn("Understood:", understood[0]["message"])

    def test_the_decision_event_still_speaks_the_frontend_s_route_vocabulary(self):
        """web/script.js keys orb states, route colours and the search starter
        sound off `decision.query_type`. M13 changed the routing vocabulary, so
        this field must keep carrying the old ROUTE word, with `kind` alongside."""
        cases = {
            "action": "task",
            "mixed": "mixed",
            "visual": "task",
            "web_question": "realtime",
            "knowledge_question": "general",
        }
        for kind, expected_route in cases.items():
            harness = self.harness([resolution("do the thing", kind)])
            harness.service.get_or_create_session("s1")
            events = [e for e in harness.service._jarvis_stream_impl("s1", "go")
                      if isinstance(e, dict)]
            decisions = [e["_activity"] for e in events
                         if e.get("_activity", {}).get("event") == "decision"]
            self.assertEqual(len(decisions), 1, kind)
            self.assertEqual(decisions[0]["query_type"], expected_route, kind)
            self.assertEqual(decisions[0]["kind"], kind)

    def test_an_untrusted_fallback_is_not_shown_as_understood(self):
        harness = self.harness([Resolution(goal="open youtube",
                                             kind="action", source="fallback")])
        harness.service.get_or_create_session("s1")
        events = [e for e in harness.service._jarvis_stream_impl("s1", "open youtube")
                  if isinstance(e, dict)]
        self.assertEqual(
            [e for e in events if e.get("_activity", {}).get("event") == "understood"],
            [])


class CacheEligibilityWiringTests(RoutingTestCase):
    def test_self_contained_is_handed_to_the_cache_before_anything_runs(self):
        harness = self.harness([resolution(
            "close the Spotify window", "action", self_contained=False)])
        noted = []
        import app.services.agent.cache.coordinator as cache_mod
        saved = cache_mod.get_phase6

        class FakePhase6:
            def note_eligibility(self, command, self_contained):
                noted.append((command, self_contained))

        cache_mod.get_phase6 = lambda: FakePhase6()
        self.addCleanup(lambda: setattr(cache_mod, "get_phase6", saved))
        harness.turn("close it")

        self.assertTrue(noted)
        self.assertTrue(all(flag is False for _cmd, flag in noted))
        commands = {cmd for cmd, _flag in noted}
        self.assertIn("close it", commands,
                      "the ORIGINAL utterance must be marked ineligible too")


if __name__ == "__main__":
    unittest.main()
