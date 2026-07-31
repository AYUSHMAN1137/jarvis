"""Conversation History UI -- service-layer behaviour.

Every test runs against a temp chats directory. The user's real chat files in
data/chats_data/ are never read, written, or deleted here.
"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models import ChatMessage
from app.services import chat_service as cs_module
from app.services.chat_service import ChatService, DEFAULT_CHAT_TITLE, derive_title


class HistoryTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.chats_dir = Path(self._tmp.name)
        self._patcher = patch.object(cs_module, "CHATS_DATA_DIR", self.chats_dir)
        self._patcher.start()
        self.service = ChatService(None)

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()

    def write_legacy(self, session_id, messages, mtime=None):
        """A pre-M-history chat file: only {session_id, messages}."""
        path = self.chats_dir / f"chat_{session_id.replace('-', '')}.json"
        path.write_text(
            json.dumps({"session_id": session_id, "messages": messages}),
            encoding="utf-8",
        )
        if mtime is not None:
            import os
            os.utime(path, (mtime, mtime))
        return path

    def seed_session(self, session_id, pairs):
        self.service.sessions[session_id] = []
        for role, content in pairs:
            self.service.add_message(session_id, role, content)


class TitleDerivationTests(unittest.TestCase):
    def test_uses_first_user_message(self):
        msgs = [ChatMessage(role="user", content="Open Bluetooth settings"),
                ChatMessage(role="assistant", content="Done.")]
        self.assertEqual(derive_title(msgs), "Open Bluetooth settings")

    def test_skips_leading_assistant_message(self):
        msgs = [ChatMessage(role="assistant", content="Good morning."),
                ChatMessage(role="user", content="wifi band karo")]
        self.assertEqual(derive_title(msgs), "wifi band karo")

    def test_collapses_whitespace_and_newlines(self):
        msgs = [ChatMessage(role="user", content="  write\n\nan  essay\ton  AI  ")]
        self.assertEqual(derive_title(msgs), "write an essay on AI")

    def test_blank_and_empty_fall_back(self):
        self.assertEqual(derive_title([]), DEFAULT_CHAT_TITLE)
        self.assertEqual(derive_title([ChatMessage(role="user", content="   ")]), DEFAULT_CHAT_TITLE)

    def test_unicode_and_hinglish_survive(self):
        msgs = [ChatMessage(role="user", content="मुझे याद दिलाना — call mummy")]
        self.assertEqual(derive_title(msgs), "मुझे याद दिलाना — call mummy")

    def test_long_message_is_truncated_with_ellipsis(self):
        long_text = "word " * 200
        title = derive_title([ChatMessage(role="user", content=long_text)])
        self.assertLessEqual(len(title), cs_module.HISTORY_TITLE_MAX_CHARS + 1)
        self.assertTrue(title.endswith("…"))


class SaveAndLoadTests(HistoryTestBase):
    def test_new_save_writes_full_metadata(self):
        self.seed_session("s1", [("user", "Open YouTube"), ("assistant", "Opening.")])
        self.service.save_chat_session("s1")

        data = json.loads((self.chats_dir / "chat_s1.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], cs_module.CHAT_SCHEMA_VERSION)
        self.assertEqual(data["session_id"], "s1")
        self.assertEqual(data["title"], "Open YouTube")
        self.assertEqual(data["message_count"], 2)
        self.assertTrue(data["created_at"])
        self.assertTrue(data["updated_at"])
        self.assertEqual(len(data["messages"]), 2)

    def test_legacy_file_loads_and_lists_with_derived_metadata(self):
        self.write_legacy("abc", [{"role": "user", "content": "hello there"}])
        self.assertTrue(self.service.load_session_from_disk("abc"))
        self.assertEqual(len(self.service.sessions["abc"]), 1)

        listing = self.service.list_conversations()
        self.assertEqual(listing["total"], 1)
        item = listing["conversations"][0]
        self.assertEqual(item["session_id"], "abc")
        self.assertEqual(item["title"], "hello there")
        self.assertTrue(item["created_at"])
        self.assertTrue(item["updated_at"])

    def test_created_at_survives_resaves(self):
        self.seed_session("s1", [("user", "first")])
        self.service.save_chat_session("s1")
        created = json.loads((self.chats_dir / "chat_s1.json").read_text(encoding="utf-8"))["created_at"]

        self.service.add_message("s1", "assistant", "reply")
        self.service.save_chat_session("s1")
        after = json.loads((self.chats_dir / "chat_s1.json").read_text(encoding="utf-8"))
        self.assertEqual(after["created_at"], created)

    def test_derived_title_tracks_first_user_message(self):
        self.seed_session("s1", [("user", "open spotify")])
        self.service.save_chat_session("s1")
        self.service.add_message("s1", "assistant", "Opening Spotify.")
        self.service.save_chat_session("s1")
        data = json.loads((self.chats_dir / "chat_s1.json").read_text(encoding="utf-8"))
        self.assertEqual(data["title"], "open spotify")
        self.assertFalse(data["title_is_custom"])

    def test_atomic_write_leaves_no_temp_files(self):
        self.seed_session("s1", [("user", "hi")])
        self.service.save_chat_session("s1")
        self.assertEqual(list(self.chats_dir.glob("*.tmp*")), [])

    def test_failed_replace_leaves_previous_file_intact(self):
        self.seed_session("s1", [("user", "original message")])
        self.service.save_chat_session("s1")
        before = (self.chats_dir / "chat_s1.json").read_text(encoding="utf-8")

        self.service.add_message("s1", "user", "second message")
        with patch.object(cs_module.os, "replace", side_effect=OSError("locked")):
            self.service.save_chat_session("s1")

        after = (self.chats_dir / "chat_s1.json").read_text(encoding="utf-8")
        self.assertEqual(before, after)
        self.assertEqual(list(self.chats_dir.glob("*.tmp*")), [])


class ListingTests(HistoryTestBase):
    def _make(self, session_id, first_msg, updated_at):
        path = self.chats_dir / f"chat_{session_id}.json"
        path.write_text(json.dumps({
            "schema_version": 2,
            "session_id": session_id,
            "title": first_msg,
            "created_at": updated_at,
            "updated_at": updated_at,
            "message_count": 2,
            "messages": [
                {"role": "user", "content": first_msg},
                {"role": "assistant", "content": f"reply to {first_msg}"},
            ],
        }), encoding="utf-8")

    def test_sorted_newest_first(self):
        self._make("a", "oldest", "2026-07-01T10:00:00+05:30")
        self._make("b", "newest", "2026-07-29T10:00:00+05:30")
        self._make("c", "middle", "2026-07-15T10:00:00+05:30")
        titles = [c["title"] for c in self.service.list_conversations()["conversations"]]
        self.assertEqual(titles, ["newest", "middle", "oldest"])

    def test_preview_is_last_message_not_full_transcript(self):
        self._make("a", "open notepad", "2026-07-29T10:00:00+05:30")
        item = self.service.list_conversations()["conversations"][0]
        self.assertEqual(item["preview"], "reply to open notepad")
        self.assertNotIn("messages", item)

    def test_search_matches_title_case_insensitively(self):
        self._make("a", "Open Bluetooth", "2026-07-29T10:00:00+05:30")
        self._make("b", "Play music", "2026-07-28T10:00:00+05:30")
        res = self.service.list_conversations(query="bluetooth")
        self.assertEqual(len(res["conversations"]), 1)
        self.assertEqual(res["conversations"][0]["session_id"], "a")

    def test_search_matches_message_content(self):
        self._make("a", "hello", "2026-07-29T10:00:00+05:30")
        res = self.service.list_conversations(query="reply to hello")
        self.assertEqual(len(res["conversations"]), 1)

    def test_search_with_no_match_returns_empty(self):
        self._make("a", "hello", "2026-07-29T10:00:00+05:30")
        self.assertEqual(self.service.list_conversations(query="zzzz")["conversations"], [])

    def test_cursor_pagination_is_stable_and_complete(self):
        for i in range(5):
            self._make(f"s{i}", f"chat {i}", f"2026-07-2{i}T10:00:00+05:30")

        page1 = self.service.list_conversations(limit=2)
        self.assertEqual(len(page1["conversations"]), 2)
        self.assertIsNotNone(page1["next_cursor"])

        page2 = self.service.list_conversations(limit=2, cursor=page1["next_cursor"])
        page3 = self.service.list_conversations(limit=2, cursor=page2["next_cursor"])
        self.assertIsNone(page3["next_cursor"])

        seen = [c["session_id"] for p in (page1, page2, page3) for c in p["conversations"]]
        self.assertEqual(len(seen), 5)
        self.assertEqual(len(set(seen)), 5)

    def test_malformed_json_is_skipped_not_fatal(self):
        self._make("good", "valid chat", "2026-07-29T10:00:00+05:30")
        (self.chats_dir / "chat_broken.json").write_text("{not json at all", encoding="utf-8")
        res = self.service.list_conversations()
        self.assertEqual(len(res["conversations"]), 1)
        self.assertEqual(res["conversations"][0]["session_id"], "good")

    def test_empty_and_non_object_files_are_skipped(self):
        (self.chats_dir / "chat_empty.json").write_text(
            json.dumps({"session_id": "x", "messages": []}), encoding="utf-8")
        (self.chats_dir / "chat_list.json").write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(self.service.list_conversations()["conversations"], [])

    def test_session_id_comes_from_contents_not_filename(self):
        # Filenames strip dashes, so a UUID is not recoverable from the name.
        real_id = "3f2b9c10-1111-2222-3333-444455556666"
        self.write_legacy(real_id, [{"role": "user", "content": "hi"}])
        item = self.service.list_conversations()["conversations"][0]
        self.assertEqual(item["session_id"], real_id)


class RetrieveTests(HistoryTestBase):
    def test_get_conversation_reads_from_disk(self):
        self.seed_session("s1", [("user", "hi"), ("assistant", "hello")])
        self.service.save_chat_session("s1")
        self.service.sessions.clear()

        conv = self.service.get_conversation("s1")
        self.assertIsNotNone(conv)
        self.assertEqual(conv["message_count"], 2)
        self.assertEqual(conv["messages"][0]["content"], "hi")

    def test_missing_conversation_returns_none(self):
        self.assertIsNone(self.service.get_conversation("does-not-exist"))


class RenameTests(HistoryTestBase):
    def test_rename_persists_and_survives_later_saves(self):
        self.seed_session("s1", [("user", "open spotify")])
        self.service.save_chat_session("s1")

        summary = self.service.rename_conversation("s1", "Music session")
        self.assertEqual(summary["title"], "Music session")

        # A later turn must NOT revert the title to the derived one.
        self.service.add_message("s1", "assistant", "Opening.")
        self.service.save_chat_session("s1")
        data = json.loads((self.chats_dir / "chat_s1.json").read_text(encoding="utf-8"))
        self.assertEqual(data["title"], "Music session")
        self.assertTrue(data["title_is_custom"])

    def test_rename_survives_restart(self):
        self.seed_session("s1", [("user", "open spotify")])
        self.service.save_chat_session("s1")
        self.service.rename_conversation("s1", "Music session")

        fresh = ChatService(None)          # simulates a server restart
        fresh.load_session_from_disk("s1")
        fresh.add_message("s1", "user", "next turn")
        fresh.save_chat_session("s1")

        data = json.loads((self.chats_dir / "chat_s1.json").read_text(encoding="utf-8"))
        self.assertEqual(data["title"], "Music session")

    def test_rename_rejects_empty_title(self):
        self.seed_session("s1", [("user", "hi")])
        self.service.save_chat_session("s1")
        with self.assertRaises(ValueError):
            self.service.rename_conversation("s1", "   ")

    def test_rename_truncates_oversized_title(self):
        self.seed_session("s1", [("user", "hi")])
        self.service.save_chat_session("s1")
        summary = self.service.rename_conversation("s1", "x" * 500)
        self.assertLessEqual(len(summary["title"]), cs_module.HISTORY_TITLE_MAX_CHARS + 1)

    def test_rename_missing_conversation_returns_none(self):
        self.assertIsNone(self.service.rename_conversation("nope", "Title"))


class DeleteTests(HistoryTestBase):
    def test_delete_removes_only_the_target(self):
        self.seed_session("keep", [("user", "keep me")])
        self.seed_session("drop", [("user", "delete me")])
        self.service.save_chat_session("keep")
        self.service.save_chat_session("drop")

        self.assertTrue(self.service.delete_conversation("drop"))
        self.assertFalse((self.chats_dir / "chat_drop.json").exists())
        self.assertTrue((self.chats_dir / "chat_keep.json").exists())

        remaining = [c["session_id"] for c in self.service.list_conversations()["conversations"]]
        self.assertEqual(remaining, ["keep"])

    def test_delete_clears_all_in_memory_state(self):
        self.seed_session("s1", [("user", "hi")])
        self.service.save_chat_session("s1")
        self.service._pending_confirmations["s1"] = {"tool": "delete_file"}
        self.service._last_goals["s1"] = {"goal": "x"}

        self.service.delete_conversation("s1")
        self.assertNotIn("s1", self.service.sessions)
        self.assertNotIn("s1", self.service._session_meta)
        self.assertNotIn("s1", self.service._pending_confirmations)
        self.assertNotIn("s1", self.service._last_goals)

    def test_delete_missing_conversation_returns_false(self):
        self.assertFalse(self.service.delete_conversation("never-existed"))


class StartupBriefIsNeverPersistedTests(HistoryTestBase):
    """The daily startup brief builds an internal prompt and stores it as a
    'user' message so the reply can stream. That prompt is not something the
    user typed, so the session must never reach disk or the history sidebar."""

    def setUp(self):
        super().setUp()
        self.service.realtime_service = SimpleNamespace(
            stream_response=lambda **kw: iter(["Good morning, Ayush. ", "All clear."])
        )
        for name in ("dbg", "get_next_key_pair"):
            p = patch.object(cs_module, name, MagicMock(return_value=(0, 0)))
            p.start()
            self.addCleanup(p.stop)

    def run_brief(self):
        sid = self.service.get_or_create_session(None)
        chunks = [c for c in self.service.process_startup_brief_stream(sid) if isinstance(c, str)]
        return sid, "".join(chunks)

    def test_brief_streams_but_writes_no_chat_file(self):
        sid, text = self.run_brief()
        self.assertEqual(text, "Good morning, Ayush. All clear.")
        self.assertIn(sid, self.service._transient_sessions)
        self.assertEqual(list(self.chats_dir.glob("*.json")), [])

    def test_brief_never_appears_in_the_conversation_list(self):
        self.run_brief()
        # A real conversation alongside it still lists normally.
        self.seed_session("real", [("user", "wifi band karo")])
        self.service.save_chat_session("real")

        listed = self.service.list_conversations()["conversations"]
        self.assertEqual([c["session_id"] for c in listed], ["real"])
        titles = [c["title"] for c in listed]
        self.assertNotIn("Please search the current weather", " ".join(titles))

    def test_brief_session_is_not_fetchable_as_a_conversation(self):
        sid, _ = self.run_brief()
        self.assertIsNone(self.service.get_conversation(sid))

    def test_cached_brief_is_also_not_persisted(self):
        self.service._startup_brief_cache = {
            "date": time.strftime("%Y-%m-%d"), "text": "Cached brief.",
        }
        sid, text = self.run_brief()
        self.assertEqual(text, "Cached brief.")
        self.assertEqual(list(self.chats_dir.glob("*.json")), [])
        self.assertIsNone(self.service.get_conversation(sid))

    def test_shutdown_save_pass_still_skips_the_brief(self):
        sid, _ = self.run_brief()
        for session_id in list(self.service.sessions.keys()):
            self.service.save_chat_session(session_id)
        self.assertEqual(list(self.chats_dir.glob("*.json")), [])


class SessionIdValidationTests(HistoryTestBase):
    def test_path_traversal_and_separators_are_rejected(self):
        for bad in ["../../etc/passwd", "a/b", "a\\b", "..", "", "   ", "x" * 300, "a\0b"]:
            self.assertFalse(self.service.validate_session_id(bad), f"should reject {bad!r}")

    def test_normal_ids_are_accepted(self):
        for good in ["3f2b9c10-1111-2222-3333-444455556666", "smoke1785076320"]:
            self.assertTrue(self.service.validate_session_id(good))

    def test_traversal_id_cannot_escape_the_chats_directory(self):
        # validate_session_id gates the routes; _session_path must also be safe.
        path = self.service._session_path("..-..-evil")
        self.assertEqual(path.parent.resolve(), self.chats_dir.resolve())


if __name__ == "__main__":
    unittest.main()
