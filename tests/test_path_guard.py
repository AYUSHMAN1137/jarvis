"""The path guard stops the *model* from writing somewhere it never meant to.

Not a security boundary -- the user can delete anything through Explorer. It
exists because the model picks these paths, and one hallucinated argument used
to be enough to delete out of C:\\Windows or overwrite part of JARVIS itself.
"""

import os
import tempfile
import unittest
from pathlib import Path

from app.services.agent.tools.file_tools import (
    _guard_path,
    create_folder,
    delete_file,
    move_path,
    unzip_file,
    write_file,
    zip_files,
)


class GuardTests(unittest.TestCase):
    def test_system_root_is_blocked(self):
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        blocked = _guard_path(os.path.join(system_root, "System32", "x.dll"), "delete")
        self.assertTrue(blocked.startswith("ERROR"))

    def test_program_files_is_blocked(self):
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        self.assertTrue(_guard_path(os.path.join(program_files, "a", "b.exe"),
                                    "write to").startswith("ERROR"))

    def test_a_drive_root_is_blocked(self):
        self.assertTrue(_guard_path("C:\\", "delete").startswith("ERROR"))

    def test_jarvis_own_source_tree_is_blocked(self):
        from config import BASE_DIR
        self.assertTrue(_guard_path(str(Path(BASE_DIR) / "config.py"),
                                    "write to").startswith("ERROR"))

    def test_the_users_own_files_are_allowed(self):
        target = os.path.join(os.path.expanduser("~"), "Desktop", "notes.txt")
        self.assertEqual(_guard_path(target, "write to"), "")

    def test_a_temp_path_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_guard_path(os.path.join(d, "f.txt"), "write to"), "")

    def test_an_empty_path_is_not_treated_as_a_violation(self):
        self.assertEqual(_guard_path("", "delete"), "")

    def test_the_message_says_what_to_do_instead(self):
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        message = _guard_path(os.path.join(system_root, "x"), "delete")
        self.assertIn("File Explorer", message)


class GuardedToolTests(unittest.TestCase):
    """Every mutating file tool must consult the guard, not just delete_file."""

    def setUp(self):
        self.system_root = os.environ.get("SystemRoot", r"C:\Windows")
        self.blocked_target = os.path.join(self.system_root, "jarvis_test_should_not_exist")

    def test_write_file_refuses(self):
        result = write_file(self.blocked_target + ".txt", "data")
        self.assertTrue(result.startswith("ERROR"))
        self.assertFalse(os.path.exists(self.blocked_target + ".txt"))

    def test_create_folder_refuses(self):
        result = create_folder(self.blocked_target)
        self.assertTrue(result.startswith("ERROR"))
        self.assertFalse(os.path.isdir(self.blocked_target))

    def test_zip_files_refuses_a_protected_destination(self):
        with tempfile.TemporaryDirectory() as d:
            source = os.path.join(d, "a.txt")
            Path(source).write_text("x", encoding="utf-8")
            result = zip_files(source, self.blocked_target + ".zip")
            self.assertTrue(result.startswith("ERROR"))

    def test_unzip_refuses_a_protected_destination(self):
        import zipfile
        with tempfile.TemporaryDirectory() as d:
            archive = os.path.join(d, "a.zip")
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("f.txt", "x")
            result = unzip_file(archive, self.blocked_target)
            self.assertTrue(result.startswith("ERROR"))

    def test_move_path_refuses_a_protected_source_or_destination(self):
        with tempfile.TemporaryDirectory() as d:
            source = os.path.join(d, "a.txt")
            Path(source).write_text("x", encoding="utf-8")
            self.assertTrue(move_path(source, self.blocked_target).startswith("ERROR"))
            self.assertTrue(os.path.exists(source), "source must be untouched")

    def test_delete_file_still_reports_a_missing_file_first(self):
        """Guard order must not turn 'not found' into a confusing refusal."""
        result = delete_file(os.path.join(self.system_root, "definitely_absent.xyz"))
        self.assertIn("is not a file", result)

    def test_allowed_paths_still_work_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "sub", "note.txt")
            self.assertFalse(write_file(target, "hello").startswith("ERROR"))
            self.assertEqual(Path(target).read_text(encoding="utf-8"), "hello")
            self.assertFalse(delete_file(target).startswith("ERROR"))


class ZipSlipTests(unittest.TestCase):
    def test_an_entry_escaping_the_destination_is_rejected(self):
        import zipfile
        with tempfile.TemporaryDirectory() as d:
            archive = os.path.join(d, "evil.zip")
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("../escaped.txt", "pwned")
            result = unzip_file(archive, os.path.join(d, "out"))
            self.assertTrue(result.startswith("ERROR"))
            self.assertFalse(os.path.exists(os.path.join(d, "escaped.txt")))

    def test_a_normal_archive_extracts(self):
        import zipfile
        with tempfile.TemporaryDirectory() as d:
            archive = os.path.join(d, "ok.zip")
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("inner/f.txt", "data")
            out = os.path.join(d, "out")
            self.assertFalse(unzip_file(archive, out).startswith("ERROR"))
            self.assertEqual(
                Path(out, "inner", "f.txt").read_text(encoding="utf-8"), "data")


if __name__ == "__main__":
    unittest.main()
