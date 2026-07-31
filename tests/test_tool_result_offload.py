"""Oversized tool results must not stay in the conversation.

The agent loop re-sends every prior tool result to the model on each of up to
16 steps, so one `ui_list_controls` (UIA_MAX_NODES = 4000 nodes) could dominate
the prompt for a whole turn. Measured latency: p50 1.65s, p90 19.65s, max 100s.
"""

import tempfile
import unittest
from pathlib import Path

from app.services.agent import tool_result_store as trs


def _big(lines=400):
    return "\n".join(f'Control {i}: Button "Option {i}" enabled=True' for i in range(lines))


class ShouldOffloadTests(unittest.TestCase):
    def test_small_results_stay_inline(self):
        self.assertFalse(trs.should_offload("ui_list_controls", "two controls"))

    def test_large_results_are_offloaded(self):
        self.assertTrue(trs.should_offload("ui_list_controls", _big()))

    def test_read_file_is_exempt(self):
        """Otherwise recovering an offloaded result would offload it again."""
        self.assertFalse(trs.should_offload("read_file", _big()))

    def test_errors_stay_inline_however_long(self):
        """The agent needs the failure text verbatim to adapt."""
        self.assertFalse(trs.should_offload("ui_do", "ERROR: " + _big()))

    def test_empty_output_is_not_offloaded(self):
        self.assertFalse(trs.should_offload("ui_do", ""))

    def test_threshold_of_zero_disables_offloading(self):
        saved = trs.threshold
        trs.threshold = lambda: 0
        try:
            self.assertFalse(trs.should_offload("ui_list_controls", _big()))
        finally:
            trs.threshold = saved


class OffloadTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._saved = trs.TOOL_RESULT_DIR
        trs.TOOL_RESULT_DIR = Path(self._dir.name)

    def tearDown(self):
        trs.TOOL_RESULT_DIR = self._saved
        self._dir.cleanup()

    def test_full_text_is_written_to_disk_unchanged(self):
        big = _big()
        path = trs.offload("ui_list_controls", big, "exec1", "act1")
        self.assertIsNotNone(path)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), big)

    def test_replacement_is_dramatically_smaller(self):
        big = _big()
        replacement = trs.maybe_offload("ui_list_controls", big, "exec1", "act1")
        self.assertLess(len(replacement), len(big) / 10)

    def test_replacement_keeps_the_first_lines_and_the_path(self):
        big = _big()
        replacement = trs.maybe_offload("ui_list_controls", big, "exec1", "act1")
        self.assertIn('Control 0: Button "Option 0"', replacement)
        self.assertIn("read_file", replacement)
        # The path must be present and must actually resolve, or the agent
        # cannot recover the rest of the output.
        written = [p for p in Path(self._dir.name).rglob("*.txt")]
        self.assertEqual(len(written), 1)
        self.assertIn(written[0].name, replacement)

    def test_replacement_states_how_much_was_omitted(self):
        replacement = trs.maybe_offload("ui_list_controls", _big(), "exec1", "act1")
        self.assertIn("more line(s) omitted", replacement)
        self.assertIn("400 lines", replacement)

    def test_ids_with_path_separators_cannot_escape_the_directory(self):
        path = trs.offload("ui_do", _big(), "../../etc", "../../passwd")
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).resolve().is_relative_to(
            Path(self._dir.name).resolve()))

    def test_an_unwritable_directory_falls_back_to_inline(self):
        trs.TOOL_RESULT_DIR = Path(self._dir.name) / "f.txt"
        trs.TOOL_RESULT_DIR.write_text("not a directory", encoding="utf-8")
        big = _big()
        self.assertEqual(trs.maybe_offload("ui_list_controls", big, "e", "a"), big)

    def test_two_actions_in_one_execution_do_not_collide(self):
        first = trs.offload("ui_list_controls", _big(10), "exec1", "act1")
        second = trs.offload("ui_list_controls", _big(20), "exec1", "act2")
        self.assertNotEqual(first, second)
        self.assertNotEqual(Path(first).read_text(encoding="utf-8"),
                            Path(second).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
