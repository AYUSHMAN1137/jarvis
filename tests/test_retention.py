import os
import sqlite3
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from app.services import maintenance


def _touch(path: Path, size: int = 100, age_days: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if age_days:
        when = time.time() - age_days * 86400
        os.utime(path, (when, when))
    return path


class PruneDebugLogsTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self._saved = maintenance.DEBUG_LOG_DIR
        maintenance.DEBUG_LOG_DIR = self.root

    def tearDown(self):
        maintenance.DEBUG_LOG_DIR = self._saved
        self._dir.cleanup()

    def test_old_session_logs_go_and_recent_ones_stay(self):
        _touch(self.root / "session_old.log", age_days=30)
        _touch(self.root / "session_new.log", age_days=1)

        result = maintenance.prune_debug_logs(days=7)

        self.assertEqual(result["removed"], 1)
        self.assertFalse((self.root / "session_old.log").exists())
        self.assertTrue((self.root / "session_new.log").exists())

    def test_persistent_logs_are_never_deleted(self):
        _touch(self.root / "server.log", age_days=400)
        _touch(self.root / "trace.log", age_days=400)

        self.assertEqual(maintenance.prune_debug_logs(days=7)["removed"], 0)
        self.assertTrue((self.root / "server.log").exists())
        self.assertTrue((self.root / "trace.log").exists())

    def test_zero_days_disables_the_job(self):
        _touch(self.root / "session_old.log", age_days=400)
        self.assertEqual(maintenance.prune_debug_logs(days=0)["removed"], 0)
        self.assertTrue((self.root / "session_old.log").exists())


class PruneVoiceCacheTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self._saved = maintenance.VOICE_CACHE_DIR
        maintenance.VOICE_CACHE_DIR = self.root

    def tearDown(self):
        maintenance.VOICE_CACHE_DIR = self._saved
        self._dir.cleanup()

    def test_evicts_least_recently_used_over_the_count_cap(self):
        for i in range(10):
            _touch(self.root / f"clip{i}.mp3", size=10, age_days=i)

        result = maintenance.prune_voice_cache(max_files=4, max_mb=0)

        self.assertEqual(result["removed"], 6)
        self.assertTrue((self.root / "clip0.mp3").exists())   # newest kept
        self.assertFalse((self.root / "clip9.mp3").exists())  # oldest evicted

    def test_size_cap_is_enforced(self):
        for i in range(5):
            _touch(self.root / f"clip{i}.mp3", size=600 * 1024, age_days=i)

        result = maintenance.prune_voice_cache(max_files=0, max_mb=1)

        self.assertEqual(result["kept"], 1)
        self.assertEqual(result["removed"], 4)

    def test_empty_directory_is_fine(self):
        self.assertEqual(maintenance.prune_voice_cache(max_files=5)["removed"], 0)


class ArchiveActionsTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.db = Path(self._dir.name) / "memory.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE actions(id INTEGER PRIMARY KEY, tool TEXT, "
                     "created_at TEXT)")
        old = (datetime.now() - timedelta(days=200)).isoformat(timespec="seconds")
        recent = datetime.now().isoformat(timespec="seconds")
        conn.executemany("INSERT INTO actions(tool, created_at) VALUES(?,?)",
                         [("old_tool", old), ("old_tool", old), ("new_tool", recent)])
        conn.commit()
        conn.close()

    def tearDown(self):
        self._dir.cleanup()

    def test_old_rows_move_to_archive_and_are_not_destroyed(self):
        result = maintenance.archive_old_actions(days=90, db_path=self.db)

        self.assertEqual(result["archived"], 2)
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0], 1)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM actions_archive").fetchone()[0], 2)
        finally:
            conn.close()

    def test_running_twice_archives_nothing_extra(self):
        maintenance.archive_old_actions(days=90, db_path=self.db)
        self.assertEqual(
            maintenance.archive_old_actions(days=90, db_path=self.db)["archived"], 0)

    def test_missing_database_is_not_an_error(self):
        missing = Path(self._dir.name) / "nope.db"
        self.assertEqual(maintenance.archive_old_actions(days=90, db_path=missing),
                         {"archived": 0})


class BackupTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.source = self.root / "src.db"
        conn = sqlite3.connect(self.source)
        conn.execute("CREATE TABLE t(a TEXT)")
        conn.executemany("INSERT INTO t VALUES(?)", [("row",)] * 12)
        conn.commit()
        conn.close()
        self._saved_dbs = maintenance._all_databases
        self._saved_flag = maintenance.BACKUP_ENABLED
        maintenance._all_databases = lambda: [self.source]
        maintenance.BACKUP_ENABLED = True

    def tearDown(self):
        maintenance._all_databases = self._saved_dbs
        maintenance.BACKUP_ENABLED = self._saved_flag
        self._dir.cleanup()

    def test_backup_is_a_readable_copy(self):
        target = self.root / "backups"

        result = maintenance.backup_databases(target_dir=target, keep_days=7)

        self.assertEqual(result["backed_up"], 1)
        self.assertEqual(result["failed"], [])
        copy = target / date.today().isoformat() / "src.db"
        self.assertTrue(copy.exists())
        conn = sqlite3.connect(copy)
        try:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM t").fetchone()[0], 12)
        finally:
            conn.close()

    def test_old_backup_folders_are_pruned_and_others_left_alone(self):
        target = self.root / "backups"
        stale = target / (date.today() - timedelta(days=30)).isoformat()
        stale.mkdir(parents=True)
        (stale / "old.db").write_bytes(b"x")
        unrelated = target / "notes"
        unrelated.mkdir(parents=True)

        result = maintenance.backup_databases(target_dir=target, keep_days=7)

        self.assertEqual(result["pruned_days"], 1)
        self.assertFalse(stale.exists())
        self.assertTrue(unrelated.exists(), "non-dated folders must be left alone")

    def test_disabled_flag_skips_everything(self):
        maintenance.BACKUP_ENABLED = False
        result = maintenance.backup_databases(target_dir=self.root / "b")
        self.assertEqual(result["backed_up"], 0)
        self.assertFalse((self.root / "b").exists())


class RunAllTests(unittest.TestCase):
    def test_one_failing_job_does_not_stop_the_others(self):
        def boom():
            raise RuntimeError("simulated failure")

        saved = maintenance.prune_debug_logs
        maintenance.prune_debug_logs = boom
        try:
            results = maintenance.run_startup_maintenance()
        finally:
            maintenance.prune_debug_logs = saved

        self.assertIn("error", results["debug_logs"])
        self.assertIn("voice_cache", results)
        self.assertIn("backup", results)


if __name__ == "__main__":
    unittest.main()
