import os
import sqlite3
import tempfile
import unittest

from app.services import db as dbmod


def _bulk_write(conn, rows=400):
    conn.execute("CREATE TABLE IF NOT EXISTS t(a TEXT)")
    conn.executemany("INSERT INTO t VALUES(?)", [("x" * 200,) for _ in range(rows)])
    conn.commit()


class DbLifecycleTests(unittest.TestCase):
    def setUp(self):
        dbmod._reset_for_tests()
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "t.db")

    def tearDown(self):
        # Windows will not delete a file that still has an open handle, so any
        # connection a test left open has to be closed before cleanup.
        dbmod.checkpoint_and_close_all()
        dbmod._reset_for_tests()
        self._dir.cleanup()

    def test_pragmas_are_applied(self):
        conn = dbmod.open_db(self.path, label="t")
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0],
                         dbmod.DB_WAL_AUTOCHECKPOINT_PAGES)
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0],
                         dbmod.DB_BUSY_TIMEOUT_MS)

    def test_shutdown_truncates_wal_and_closes(self):
        conn = dbmod.open_db(self.path, label="t")
        _bulk_write(conn)
        self.assertTrue(os.path.exists(self.path + "-wal"))
        self.assertGreater(os.path.getsize(self.path + "-wal"), 0)

        summary = dbmod.checkpoint_and_close_all()

        self.assertEqual(summary["closed"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertFalse(os.path.exists(self.path + "-wal"), "-wal must be gone")
        self.assertFalse(os.path.exists(self.path + "-shm"), "-shm must be gone")
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_data_survives_the_checkpoint(self):
        conn = dbmod.open_db(self.path, label="t")
        _bulk_write(conn, rows=25)
        dbmod.checkpoint_and_close_all()

        reopened = sqlite3.connect(self.path)
        try:
            self.assertEqual(reopened.execute("SELECT COUNT(*) FROM t").fetchone()[0], 25)
        finally:
            reopened.close()

    def test_two_connections_to_one_file_both_close(self):
        """memory.db really is opened twice (memory_service + context_engine)."""
        first = dbmod.open_db(self.path, label="memory")
        second = dbmod.open_db(self.path, label="context_aliases")
        _bulk_write(first)

        summary = dbmod.checkpoint_and_close_all()

        self.assertEqual(summary["closed"], 2)
        self.assertFalse(os.path.exists(self.path + "-wal"))
        for conn in (first, second):
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1")

    def test_registry_is_emptied_so_shutdown_is_idempotent(self):
        dbmod.open_db(self.path, label="t")
        self.assertEqual(len(dbmod.tracked()), 1)
        dbmod.checkpoint_and_close_all()
        self.assertEqual(dbmod.tracked(), [])
        self.assertEqual(dbmod.checkpoint_and_close_all(),
                         {"checkpointed": 0, "closed": 0, "failed": 0})

    def test_in_memory_database_is_tracked_but_has_no_wal(self):
        conn = dbmod.open_db(":memory:", label="fallback")
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "memory")
        self.assertEqual(dbmod.checkpoint_and_close_all()["closed"], 1)

    def test_unregister_leaves_connection_open(self):
        conn = dbmod.open_db(self.path, label="t")
        dbmod.unregister(conn)
        self.assertEqual(dbmod.tracked(), [])
        dbmod.checkpoint_and_close_all()
        conn.execute("SELECT 1")  # still usable -- we asked to be left alone
        conn.close()

    def test_a_broken_connection_does_not_stop_the_rest(self):
        good = dbmod.open_db(self.path, label="good")
        broken = sqlite3.connect(os.path.join(self._dir.name, "b.db"))
        broken.close()  # already closed -> every later call raises
        dbmod.register(broken, label="broken", db_path=os.path.join(self._dir.name, "b.db"))

        summary = dbmod.checkpoint_and_close_all()

        self.assertGreaterEqual(summary["failed"], 1)
        self.assertGreaterEqual(summary["closed"], 1)
        with self.assertRaises(sqlite3.ProgrammingError):
            good.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
