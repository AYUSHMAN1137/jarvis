import unittest

from app.services.memory_extractor import MemoryExtractor, parse_facts


class FakeResponse:
    def __init__(self, content):
        self.content = content


class FakeLLM:
    """Stands in for ChatGroq. Records calls, replays canned replies."""

    def __init__(self, replies=None, fail=False):
        self.replies = list(replies or [])
        self.fail = fail
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("simulated key failure")
        return FakeResponse(self.replies.pop(0) if self.replies else '{"facts": []}')


class FakeMemory:
    def __init__(self, reject=()):
        self.saved = []
        self.reject = set(reject)

    def remember(self, value, category="general", key=None, source="user"):
        if value in self.reject:
            return "I won't save that - it looks like a password or secret."
        self.saved.append({"value": value, "category": category, "key": key,
                           "source": source})
        return "Got it, I'll remember that."


def _extractor(llms, memory=None, **kw):
    ex = MemoryExtractor(memory=memory or FakeMemory(), llm_factory=lambda: llms)
    for name, value in kw.items():
        setattr(ex, name, value)
    return ex


class ParseFactsTests(unittest.TestCase):
    def test_plain_json(self):
        raw = '{"facts": [{"category": "user", "key": "name", "value": "Ayush"}]}'
        self.assertEqual(parse_facts(raw, 3),
                         [{"category": "user", "key": "name", "value": "Ayush"}])

    def test_json_wrapped_in_reasoning_and_a_fence(self):
        raw = ('Let me think about this turn.\n```json\n'
               '{"facts": [{"category": "preference", "key": "browser", '
               '"value": "prefers Brave"}]}\n```\nDone.')
        facts = parse_facts(raw, 3)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["key"], "browser")

    def test_empty_and_malformed_replies_yield_nothing(self):
        for raw in ("", "no json here", "{broken", '{"facts": "not a list"}', "{}"):
            self.assertEqual(parse_facts(raw, 3), [], raw)

    def test_unknown_category_falls_back_to_general(self):
        raw = '{"facts": [{"category": "nonsense", "key": "x", "value": "v"}]}'
        self.assertEqual(parse_facts(raw, 3)[0]["category"], "general")

    def test_per_turn_cap_is_enforced(self):
        items = ", ".join('{"category":"user","key":"k%d","value":"v%d"}' % (i, i)
                          for i in range(10))
        self.assertEqual(len(parse_facts('{"facts": [%s]}' % items, 3)), 3)

    def test_duplicates_within_one_reply_collapse(self):
        raw = ('{"facts": [{"category":"user","key":"name","value":"Ayush"},'
               '{"category":"user","key":"name","value":"ayush"}]}')
        self.assertEqual(len(parse_facts(raw, 5)), 1)

    def test_empty_or_oversized_values_are_dropped(self):
        raw = ('{"facts": [{"category":"user","key":"a","value":""},'
               '{"category":"user","key":"b","value":"%s"}]}' % ("x" * 500))
        self.assertEqual(parse_facts(raw, 5), [])

    def test_blank_key_becomes_none(self):
        raw = '{"facts": [{"category": "general", "key": "", "value": "something"}]}'
        self.assertIsNone(parse_facts(raw, 3)[0]["key"])


class ShouldConsiderTests(unittest.TestCase):
    def test_short_messages_are_skipped(self):
        ex = _extractor([FakeLLM()], min_chars=12)
        self.assertFalse(ex.should_consider("hi"))
        self.assertFalse(ex.should_consider("   "))
        self.assertTrue(ex.should_consider("I always use Brave as my browser"))

    def test_disabled_extractor_considers_nothing(self):
        ex = _extractor([FakeLLM()], enabled=False)
        self.assertFalse(ex.should_consider("I always use Brave as my browser"))


class ExtractNowTests(unittest.TestCase):
    def test_happy_path(self):
        llm = FakeLLM(['{"facts": [{"category":"user","key":"city","value":"lives in Delhi"}]}'])
        facts = _extractor([llm]).extract_now("I live in Delhi", "Noted.")
        self.assertEqual(facts[0]["value"], "lives in Delhi")
        self.assertEqual(len(llm.calls), 1)

    def test_no_llm_configured_returns_nothing(self):
        self.assertEqual(_extractor([]).extract_now("I live in Delhi"), [])

    def test_a_dead_key_falls_through_to_the_next(self):
        dead = FakeLLM(fail=True)
        alive = FakeLLM(['{"facts": [{"category":"user","key":"name","value":"Ayush"}]}'])
        facts = _extractor([dead, alive]).extract_now("my name is Ayush")
        self.assertEqual(len(facts), 1)

    def test_all_keys_failing_is_not_an_error(self):
        ex = _extractor([FakeLLM(fail=True), FakeLLM(fail=True)])
        self.assertEqual(ex.extract_now("some durable statement here"), [])

    def test_keys_rotate_across_calls(self):
        first, second = FakeLLM(), FakeLLM()
        ex = _extractor([first, second])
        ex.extract_now("a durable statement here")
        ex.extract_now("another durable statement")
        self.assertEqual(len(first.calls), 1)
        self.assertEqual(len(second.calls), 1)


class SaveTests(unittest.TestCase):
    def test_facts_are_written_to_memory(self):
        memory = FakeMemory()
        ex = _extractor([FakeLLM()], memory=memory)
        saved = ex._save([{"category": "user", "key": "name", "value": "Ayush"}])
        self.assertEqual(saved, 1)
        self.assertEqual(memory.saved[0]["source"], "auto-llm")

    def test_a_rejected_secret_is_not_counted_as_saved(self):
        memory = FakeMemory(reject={"my api key is sk-123"})
        ex = _extractor([FakeLLM()], memory=memory)
        saved = ex._save([{"category": "user", "key": "k", "value": "my api key is sk-123"}])
        self.assertEqual(saved, 0)
        self.assertEqual(memory.saved, [])


class SubmitTests(unittest.TestCase):
    def test_submit_extracts_and_saves_in_the_background(self):
        memory = FakeMemory()
        llm = FakeLLM(['{"facts": [{"category":"user","key":"name","value":"Ayush"}]}'])
        ex = _extractor([llm], memory=memory)

        self.assertTrue(ex.submit("my name is Ayush and I use Brave", "Noted."))
        ex.drain(timeout=5)

        self.assertEqual(memory.saved[0]["value"], "Ayush")
        self.assertEqual(ex.stats["saved"], 1)

    def test_submit_on_a_short_message_is_a_no_op(self):
        ex = _extractor([FakeLLM()])
        self.assertFalse(ex.submit("hi"))
        self.assertEqual(ex.stats["queued"], 0)

    def test_a_worker_error_does_not_kill_the_worker(self):
        memory = FakeMemory()
        ex = _extractor([FakeLLM(fail=True)], memory=memory)
        ex.submit("a durable statement about me", "ok")
        ex.drain(timeout=5)

        ex._llms = [FakeLLM(['{"facts": [{"category":"user","key":"n","value":"Ayush"}]}'])]
        ex.submit("another durable statement", "ok")
        ex.drain(timeout=5)

        self.assertEqual(memory.saved[0]["value"], "Ayush")


if __name__ == "__main__":
    unittest.main()
