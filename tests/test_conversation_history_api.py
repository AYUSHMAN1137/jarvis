"""Conversation History route contracts.

Mounts only the chat router against a temp chats directory, so no real service
boot and no contact with the user's real chat files.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.core.state as state
from app.api.chat import router
from app.services import chat_service as cs_module
from app.services.chat_service import ChatService


class HistoryApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.chats_dir = Path(self._tmp.name)
        self._patcher = patch.object(cs_module, "CHATS_DATA_DIR", self.chats_dir)
        self._patcher.start()

        self.service = ChatService(None)
        self._prev_service = state.chat_service
        state.chat_service = self.service

        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        state.chat_service = self._prev_service
        self._patcher.stop()
        self._tmp.cleanup()

    def seed(self, session_id, pairs):
        self.service.sessions[session_id] = []
        for role, content in pairs:
            self.service.add_message(session_id, role, content)
        self.service.save_chat_session(session_id)

    # ---------- list ----------
    def test_list_returns_summaries_without_transcripts(self):
        self.seed("s1", [("user", "open youtube"), ("assistant", "Opening.")])
        res = self.client.get("/chat/history")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["total"], 1)
        item = body["conversations"][0]
        self.assertEqual(item["title"], "open youtube")
        self.assertNotIn("messages", item)

    def test_list_is_empty_when_no_chats(self):
        body = self.client.get("/chat/history").json()
        self.assertEqual(body["conversations"], [])
        self.assertIsNone(body["next_cursor"])

    def test_list_search_filters(self):
        self.seed("s1", [("user", "open bluetooth")])
        self.seed("s2", [("user", "play music")])
        body = self.client.get("/chat/history", params={"query": "BLUETOOTH"}).json()
        self.assertEqual(len(body["conversations"]), 1)

    def test_list_limit_is_bounded(self):
        self.assertEqual(self.client.get("/chat/history", params={"limit": 0}).status_code, 422)
        self.assertEqual(self.client.get("/chat/history", params={"limit": 100000}).status_code, 422)

    def test_list_pagination_cursor(self):
        for i in range(3):
            self.seed(f"s{i}", [("user", f"chat {i}")])
        page1 = self.client.get("/chat/history", params={"limit": 2}).json()
        self.assertEqual(len(page1["conversations"]), 2)
        page2 = self.client.get("/chat/history",
                                params={"limit": 2, "cursor": page1["next_cursor"]}).json()
        self.assertEqual(len(page2["conversations"]), 1)
        self.assertIsNone(page2["next_cursor"])

    # ---------- retrieve ----------
    def test_get_returns_full_conversation(self):
        self.seed("s1", [("user", "hi"), ("assistant", "hello")])
        body = self.client.get("/chat/history/s1").json()
        self.assertEqual(body["session_id"], "s1")
        self.assertEqual(len(body["messages"]), 2)
        self.assertEqual(body["messages"][1]["content"], "hello")

    def test_get_missing_returns_404_not_empty_history(self):
        res = self.client.get("/chat/history/nope")
        self.assertEqual(res.status_code, 404)

    def test_get_reads_from_disk_after_restart(self):
        self.seed("s1", [("user", "persisted")])
        self.service.sessions.clear()
        body = self.client.get("/chat/history/s1").json()
        self.assertEqual(body["messages"][0]["content"], "persisted")

    def test_malformed_session_ids_never_reach_the_filesystem(self):
        """Traversal attempts are refused somewhere in the chain -- by URL path
        normalisation (405), by routing (404), or by validate_session_id (400).
        What matters is that none of them return a conversation."""
        bad_ids = [
            "..",            # normalised away by the URL layer
            "a%2Fb",         # encoded forward slash
            "a%5Cb",         # encoded backslash
            "%2E%2E%2Fx",    # encoded ../
            "a%00b",         # null byte
            "x" * 300,       # over the length cap
        ]
        for bad in bad_ids:
            res = self.client.get(f"/chat/history/{bad}")
            self.assertIn(res.status_code, (400, 404, 405), f"{bad!r} -> {res.status_code}")

    def test_delete_rejects_malformed_session_id(self):
        for bad in ["a%5Cb", "a%00b", "x" * 300]:
            res = self.client.delete(f"/chat/history/{bad}")
            self.assertEqual(res.status_code, 400, f"{bad!r} -> {res.status_code}")

    # ---------- rename ----------
    def test_rename_updates_title(self):
        self.seed("s1", [("user", "open spotify")])
        res = self.client.patch("/chat/history/s1", json={"title": "Music session"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["title"], "Music session")
        self.assertEqual(self.client.get("/chat/history/s1").json()["title"], "Music session")

    def test_rename_rejects_empty_title(self):
        self.seed("s1", [("user", "hi")])
        self.assertEqual(self.client.patch("/chat/history/s1", json={"title": ""}).status_code, 422)
        self.assertEqual(self.client.patch("/chat/history/s1", json={"title": "  "}).status_code, 400)

    def test_rename_rejects_oversized_title(self):
        self.seed("s1", [("user", "hi")])
        res = self.client.patch("/chat/history/s1", json={"title": "x" * 5000})
        self.assertEqual(res.status_code, 422)

    def test_rename_missing_returns_404(self):
        res = self.client.patch("/chat/history/nope", json={"title": "Whatever"})
        self.assertEqual(res.status_code, 404)

    # ---------- delete ----------
    def test_delete_removes_conversation(self):
        self.seed("s1", [("user", "delete me")])
        res = self.client.delete("/chat/history/s1")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["deleted"])
        self.assertEqual(self.client.get("/chat/history/s1").status_code, 404)

    def test_delete_missing_returns_404(self):
        self.assertEqual(self.client.delete("/chat/history/nope").status_code, 404)

    def test_delete_affects_only_the_target(self):
        self.seed("keep", [("user", "keep me")])
        self.seed("drop", [("user", "drop me")])
        self.client.delete("/chat/history/drop")
        remaining = self.client.get("/chat/history").json()["conversations"]
        self.assertEqual([c["session_id"] for c in remaining], ["keep"])

    # ---------- error hygiene ----------
    def test_errors_do_not_leak_filesystem_paths(self):
        self.seed("s1", [("user", "hi")])
        with patch.object(self.service, "list_conversations", side_effect=OSError(str(self.chats_dir))):
            res = self.client.get("/chat/history")
        self.assertEqual(res.status_code, 500)
        self.assertNotIn(str(self.chats_dir), json.dumps(res.json()))


class ConversationDeepLinkTests(unittest.TestCase):
    """/jarvis/c/<id> is a frontend route. A hard refresh or a pasted link must
    get the app shell back, not a 404 from the static mount."""

    def setUp(self):
        from app.api.dashboard import router as dashboard_router
        app = FastAPI()
        app.include_router(dashboard_router)
        self.client = TestClient(app)

    def test_deep_link_serves_the_app_shell(self):
        for path in ("/jarvis/c/3f2b9c10-1111-2222-3333-444455556666",
                     "/app/c/3f2b9c10-1111-2222-3333-444455556666"):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 200, path)
            self.assertIn("text/html", res.headers["content-type"])
            # <base href> is what keeps style.css / script.js resolving from the
            # deeper path -- without it the page loads unstyled and dead.
            self.assertIn('<base href="/jarvis/">', res.text)

    def test_unknown_session_id_still_gets_the_shell(self):
        # The frontend decides the conversation is gone; the server must not
        # guess, because a 404 here would break Back/Forward navigation.
        res = self.client.get("/jarvis/c/does-not-exist")
        self.assertEqual(res.status_code, 200)

    def test_session_id_is_never_used_as_a_path(self):
        # Traversal in the id must not escape web/ -- the handler ignores the id
        # entirely, so this can only ever return index.html or 404.
        res = self.client.get("/jarvis/c/..%2f..%2fconfig.py")
        self.assertIn(res.status_code, (200, 404))
        if res.status_code == 200:
            self.assertIn("<base href=", res.text)
            self.assertNotIn("GROQ_API_KEY", res.text)


if __name__ == "__main__":
    unittest.main()
