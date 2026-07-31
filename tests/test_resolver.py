"""M13 §4.7 -- the understanding layer.

Every case here is one the old hardcoded phrase lists got wrong, or could only
get right by someone adding another string:

  * pronouns and ellipsis ("search for it")
  * Hindi / Hinglish ("YouTube kholo", "kar do")
  * a complaint that an action did not work, phrased in a way no list contained
  * a genuinely ambiguous request, which must ASK rather than guess
  * malformed model output, which must degrade rather than break

No network: the LLM is a fake that returns scripted strings.
"""

import json
import unittest

from app.services.resolver import (
    KIND_ACTION, KIND_KNOWLEDGE_QUESTION, KIND_VISUAL, KIND_WEB_QUESTION,
    Resolver, SOURCE_FALLBACK, SOURCE_LLM, SOURCE_OFFLINE,
)


class FakeCompletions:
    def __init__(self, client):
        self._client = client

    def create(self, **kwargs):
        self._client.calls.append(kwargs)
        if self._client.fail:
            raise RuntimeError("provider down")
        reply = (self._client.replies.pop(0) if self._client.replies
                 else "{\"goal\": \"\", \"kind\": \"knowledge_question\"}")
        message = type("M", (), {"content": reply})()
        choice = type("C", (), {"message": message})()
        return type("R", (), {"choices": [choice]})()


class FakeClient:
    """Minimal stand-in for an OpenAI-style client."""

    def __init__(self, replies=None, fail=False):
        self.replies = list(replies or [])
        self.fail = fail
        self.calls = []
        self.chat = type("Chat", (), {"completions": FakeCompletions(self)})()


def make_resolver(replies=None, fail=False, groq_replies=None):
    gemini = [FakeClient(replies, fail=fail)]
    groq = [FakeClient(groq_replies)] if groq_replies is not None else []
    resolver = Resolver(gemini_clients=gemini, groq_clients=groq,
                        model="fake-model", timeout=3)
    resolver.enabled = True
    return resolver, gemini[0], (groq[0] if groq else None)


def payload(**overrides):
    base = {
        "goal": "open youtube",
        "kind": "action",
        "self_contained": True,
        "refers_to_previous": False,
        "is_confirmation": None,
        "visual_source": None,
        "unresolved": [],
        "confidence": 0.9,
    }
    base.update(overrides)
    return json.dumps(base)


class PronounAndEllipsisTests(unittest.TestCase):
    def test_a_pronoun_is_replaced_by_what_it_refers_to(self):
        """Turn 6 of session 94bf07c2: "Search for it." must never reach a tool
        as the literal word "it" -- that is exactly what happened before."""
        resolver, _client, _ = make_resolver([payload(
            goal="search for the song Ishq by Fahim Abdullah",
            kind="action", self_contained=False, refers_to_previous=True)])
        history = [
            ("play ishq song", "Done."),
            ("it's a pakistani song", "I'll look for it."),
            ("bai Fahim Abdullah", "I'll search for it."),
        ]
        result = resolver.resolve("search for it", chat_history=history)

        self.assertEqual(result.source, SOURCE_LLM)
        self.assertNotEqual(result.goal.strip().lower(), "it")
        self.assertIn("Fahim Abdullah", result.goal)
        self.assertFalse(result.self_contained)
        self.assertTrue(result.refers_to_previous)

    def test_the_conversation_is_actually_sent_to_the_model(self):
        resolver, client, _ = make_resolver([payload()])
        resolver.resolve("search for it",
                         chat_history=[("play ishq song", "Done.")])
        sent = client.calls[0]["messages"][-1]["content"]
        self.assertIn("play ishq song", sent)
        self.assertIn("Conversation so far", sent)

    def test_history_is_capped_to_the_configured_window(self):
        resolver, client, _ = make_resolver([payload()])
        resolver.max_history = 2
        history = [(f"turn {i}", f"reply {i}") for i in range(10)]
        resolver.resolve("and again", chat_history=history)
        sent = client.calls[0]["messages"][-1]["content"]
        self.assertIn("turn 9", sent)
        self.assertNotIn("turn 5", sent)


class HinglishTests(unittest.TestCase):
    def test_hindi_command_is_understood_as_an_action(self):
        resolver, _client, _ = make_resolver([payload(
            goal="open YouTube in the browser", kind="action")])
        result = resolver.resolve("YouTube kholo")
        self.assertEqual(result.kind, KIND_ACTION)
        self.assertIn("YouTube", result.goal)

    def test_hindi_confirmation_is_understood_without_a_word_list(self):
        resolver, _client, _ = make_resolver([payload(
            goal="delete the file", is_confirmation=True,
            kind="action", refers_to_previous=True)])
        result = resolver.resolve(
            "haan kar do",
            confirmation_pending={"tool": "delete_file",
                                  "original_message": "delete old.txt"})
        self.assertIs(result.is_confirmation, True)

    def test_a_refusal_is_understood(self):
        resolver, _client, _ = make_resolver([payload(is_confirmation=False)])
        result = resolver.resolve(
            "nahi rehne do",
            confirmation_pending={"tool": "delete_file",
                                  "original_message": "delete old.txt"})
        self.assertIs(result.is_confirmation, False)

    def test_pending_confirmation_is_declared_in_the_context(self):
        resolver, client, _ = make_resolver([payload()])
        resolver.resolve("yes", confirmation_pending={
            "tool": "shutdown_computer", "original_message": "shut down"})
        sent = client.calls[0]["messages"][-1]["content"]
        self.assertIn("confirmation is PENDING", sent)
        self.assertIn("shutdown_computer", sent)

    def test_no_pending_confirmation_is_also_declared(self):
        resolver, client, _ = make_resolver([payload()])
        resolver.resolve("open youtube")
        sent = client.calls[0]["messages"][-1]["content"]
        self.assertIn("No confirmation is pending", sent)


class ComplaintTests(unittest.TestCase):
    def test_a_complaint_no_phrase_list_contained_is_understood(self):
        """"It's not playing." was not in the retry-complaint list, so turn 3 of
        the evidence session went to small talk. Understanding does not need the
        phrase to have been anticipated."""
        resolver, _client, _ = make_resolver([payload(
            goal="play the song Ishq on YouTube (the previous attempt did nothing)",
            kind="action", self_contained=False, refers_to_previous=True)])
        result = resolver.resolve(
            "it's not playing", chat_history=[("play ishq song", "Done.")])
        self.assertTrue(result.refers_to_previous)
        self.assertEqual(result.kind, KIND_ACTION)

    def test_the_last_action_and_its_verdict_are_given_to_the_model(self):
        resolver, client, _ = make_resolver([payload()])
        resolver.resolve("it's not playing", last_action={
            "tool": "play_on_youtube", "args": {"query": "ishq"},
            "verdict": "UNKNOWN", "reason": "browser never acknowledged"})
        sent = client.calls[0]["messages"][-1]["content"]
        self.assertIn("play_on_youtube", sent)
        self.assertIn("UNKNOWN", sent)
        self.assertIn("browser never acknowledged", sent)


class ClarificationTests(unittest.TestCase):
    def test_unresolved_means_ask_instead_of_guessing(self):
        resolver, _client, _ = make_resolver([payload(
            goal="send an email", unresolved=["who the email should go to"])])
        result = resolver.resolve("send that email")
        self.assertTrue(result.needs_clarification)
        self.assertEqual(result.unresolved, ["who the email should go to"])

    def test_an_empty_unresolved_list_does_not_block_the_turn(self):
        resolver, _client, _ = make_resolver([payload(unresolved=[])])
        self.assertFalse(resolver.resolve("open youtube").needs_clarification)

    def test_a_vague_question_is_answered_not_interrogated(self):
        """Guessing is dangerous before an ACTION, merely unhelpful before a
        search. "who won the match last night" gets searched, not questioned."""
        resolver, _client, _ = make_resolver([payload(
            goal="find out who won the match last night", kind="web_question",
            unresolved=["which sport"])])
        result = resolver.resolve("who won the match last night")
        self.assertTrue(result.unresolved)
        self.assertFalse(result.needs_clarification)

    def test_a_vague_action_still_asks(self):
        resolver, _client, _ = make_resolver([payload(
            goal="delete the file", kind="action", unresolved=["which file"])])
        self.assertTrue(resolver.resolve("delete it").needs_clarification)

    def test_a_single_string_unresolved_is_accepted(self):
        resolver, _client, _ = make_resolver([payload(unresolved="which file")])
        self.assertEqual(resolver.resolve("open it").unresolved, ["which file"])

    def test_unresolved_is_capped(self):
        resolver, _client, _ = make_resolver([payload(
            unresolved=["a", "b", "c", "d", "e", "f"])])
        self.assertEqual(len(resolver.resolve("do stuff").unresolved), 4)


class KindTests(unittest.TestCase):
    def test_web_question_is_recognised(self):
        resolver, _client, _ = make_resolver([payload(
            goal="who is Fahim Abdullah", kind="web_question")])
        result = resolver.resolve("who is he")
        self.assertEqual(result.kind, KIND_WEB_QUESTION)
        self.assertFalse(result.needs_action)

    def test_knowledge_question_is_recognised(self):
        resolver, _client, _ = make_resolver([payload(
            goal="say hello back", kind="knowledge_question")])
        self.assertEqual(resolver.resolve("hey").kind, KIND_KNOWLEDGE_QUESTION)

    def test_visual_carries_which_surface_to_look_at(self):
        resolver, _client, _ = make_resolver([payload(
            goal="describe what the owner is holding up",
            kind="visual", visual_source="camera")])
        result = resolver.resolve("what am I holding")
        self.assertEqual(result.kind, KIND_VISUAL)
        self.assertEqual(result.visual_source, "camera")
        self.assertTrue(result.needs_action)

    def test_a_screen_question_is_visual_but_not_the_camera(self):
        resolver, _client, _ = make_resolver([payload(
            goal="read the options on the Settings page currently on screen",
            kind="visual", visual_source="screen")])
        result = resolver.resolve("what options are on this page")
        self.assertEqual(result.visual_source, "screen")

    def test_an_unrecognised_kind_prefers_the_agent_over_chat(self):
        """Chat cannot act and is free to promise things, so an unknown kind must
        never quietly become chat."""
        resolver, _client, _ = make_resolver([payload(kind="banana")])
        self.assertEqual(resolver.resolve("do the thing").kind, KIND_ACTION)

    def test_a_bogus_visual_source_is_dropped(self):
        resolver, _client, _ = make_resolver([payload(
            kind="visual", visual_source="telescope")])
        self.assertIsNone(resolver.resolve("look").visual_source)


class ParsingTests(unittest.TestCase):
    def test_a_json_fence_is_tolerated(self):
        resolver, _client, _ = make_resolver(["```json\n" + payload() + "\n```"])
        self.assertEqual(resolver.resolve("open youtube").source, SOURCE_LLM)

    def test_a_think_block_is_stripped(self):
        resolver, _client, _ = make_resolver(
            ["<think>hmm, they want youtube</think>" + payload()])
        self.assertEqual(resolver.resolve("open youtube").source, SOURCE_LLM)

    def test_prose_around_the_json_is_tolerated(self):
        resolver, _client, _ = make_resolver(
            ["Sure, here you go: " + payload() + " Hope that helps!"])
        self.assertEqual(resolver.resolve("open youtube").source, SOURCE_LLM)

    def test_malformed_json_is_re_asked_exactly_once(self):
        resolver, client, _ = make_resolver(["not json at all", payload()])
        result = resolver.resolve("open youtube")
        self.assertEqual(result.source, SOURCE_LLM)
        self.assertEqual(len(client.calls), 2)
        repair = client.calls[1]["messages"][-1]["content"]
        self.assertIn("ONLY the JSON object", repair)

    def test_still_malformed_falls_back_to_the_raw_utterance(self):
        resolver, _client, _ = make_resolver(["nope", "still nope"])
        result = resolver.resolve("open youtube")
        self.assertEqual(result.source, SOURCE_FALLBACK)
        self.assertEqual(result.goal, "open youtube")
        self.assertEqual(result.kind, KIND_ACTION)
        self.assertTrue(result.ok, "a fallback still routes; it just is not trusted")
        self.assertFalse(result.understood)

    def test_a_missing_goal_falls_back_to_the_utterance(self):
        resolver, _client, _ = make_resolver([json.dumps({"kind": "action"})])
        self.assertEqual(resolver.resolve("open youtube").goal, "open youtube")

    def test_odd_confidence_values_are_clamped(self):
        resolver, _client, _ = make_resolver([payload(confidence="banana")])
        self.assertEqual(resolver.resolve("open youtube").confidence, 0.0)
        resolver2, _c, _ = make_resolver([payload(confidence=7)])
        self.assertEqual(resolver2.resolve("open youtube").confidence, 1.0)

    def test_string_booleans_are_coerced(self):
        resolver, _client, _ = make_resolver([payload(
            self_contained="false", refers_to_previous="true",
            is_confirmation="yes")])
        result = resolver.resolve("do it")
        self.assertFalse(result.self_contained)
        self.assertTrue(result.refers_to_previous)
        self.assertIs(result.is_confirmation, True)


class DegradationTests(unittest.TestCase):
    def test_key_cycling_is_capped_so_an_outage_cannot_stall_every_turn(self):
        """A provider-wide 503 must not cost ~4s per key across 10 keys. That is
        what made the old brain take 14-22s to route a message."""
        clients = [FakeClient(fail=True) for _ in range(10)]
        resolver = Resolver(gemini_clients=clients,
                            groq_clients=[FakeClient([payload()])],
                            model="fake-model", timeout=1)
        resolver.max_failover_keys = 3
        result = resolver.resolve("open youtube")
        self.assertEqual(result.provider, "groq")
        tried = sum(1 for client in clients if client.calls)
        self.assertLessEqual(tried, 3)

    def test_gemini_failure_falls_over_to_groq_and_is_never_raced(self):
        resolver, gemini, groq = make_resolver(
            replies=None, fail=True, groq_replies=[payload()])
        result = resolver.resolve("open youtube")
        self.assertEqual(result.source, SOURCE_LLM)
        self.assertEqual(result.provider, "groq")
        # Gemini was tried FIRST and exhausted before Groq was touched.
        self.assertTrue(gemini.calls)
        self.assertTrue(groq.calls)

    def test_every_provider_down_is_reported_honestly_not_guessed(self):
        resolver = Resolver(gemini_clients=[FakeClient(fail=True)],
                            groq_clients=[FakeClient(fail=True)],
                            model="fake-model", timeout=1)
        result = resolver.resolve("open youtube")
        self.assertEqual(result.source, SOURCE_OFFLINE)
        self.assertFalse(result.ok)

    def test_no_clients_at_all_is_offline(self):
        resolver = Resolver(gemini_clients=[], groq_clients=[])
        self.assertFalse(resolver.available)
        self.assertEqual(resolver.resolve("open youtube").source, SOURCE_OFFLINE)

    def test_disabled_resolver_is_offline_not_a_guess(self):
        resolver, _client, _ = make_resolver([payload()])
        resolver.enabled = False
        self.assertEqual(resolver.resolve("open youtube").source, SOURCE_OFFLINE)

    def test_empty_input_never_calls_the_model(self):
        resolver, client, _ = make_resolver([payload()])
        result = resolver.resolve("   ")
        self.assertEqual(client.calls, [])
        self.assertEqual(result.kind, KIND_KNOWLEDGE_QUESTION)


if __name__ == "__main__":
    unittest.main()
