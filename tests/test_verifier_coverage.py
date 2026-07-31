"""Every registered tool must resolve to a verifier family.

Before this was enforced, 43 of 75 tools fell through `classify_family` to
None. A tool with no family can only ever produce UNKNOWN, and UNKNOWN never
promotes to the command cache, never becomes a skill, and never becomes a
habit -- so the cache sat at 6 entries with 2 lifetime hits while the agent
silently learned nothing.

This test is the guard that stops that hole reopening when a tool is added.
"""

import unittest

from app.services.agent.checker.checker import (
    KNOWN_FAMILIES,
    classify_family,
    declared_family,
)
from app.services.agent.tool_registry import registry
from app.services.agent.tools import load_all_tools

load_all_tools()

# Families whose verifier can return PASS or FAIL. "none" is deliberately
# excluded: it means "honestly unverifiable", not "not thought about".
VERIFYING_FAMILIES = KNOWN_FAMILIES - {"none"}


class VerifierCoverageTests(unittest.TestCase):
    def test_every_tool_resolves_to_a_known_family(self):
        unclassified = [n for n in registry.names() if classify_family(n) is None]
        self.assertEqual(
            unclassified, [],
            f"{len(unclassified)} tool(s) have no verifier family. Add "
            f'verification={{"family": ...}} to their @tool decorator: {unclassified}')

    def test_every_tool_declares_its_family_explicitly(self):
        """Name heuristics are a fallback, not the contract."""
        undeclared = [n for n in registry.names() if declared_family(n) is None]
        self.assertEqual(
            undeclared, [],
            f"{len(undeclared)} tool(s) rely on name guessing: {undeclared}")

    def test_declared_families_are_all_known(self):
        for name in registry.names():
            family = (registry.get(name).verification or {}).get("family")
            self.assertIn(family, KNOWN_FAMILIES,
                          f"{name} declares unknown family {family!r}")

    def test_most_tools_are_actually_verifiable(self):
        """`none` is a deliberate escape hatch, so keep it rare."""
        names = registry.names()
        by_design = [n for n in names if classify_family(n) == "none"]
        self.assertLess(len(by_design), len(names) * 0.25,
                        f"too many tools opted out of verification: {by_design}")

    def test_dangerous_tools_are_not_silently_marked_verifiable(self):
        """A dangerous tool must either be genuinely checkable or admit it isn't."""
        for name in registry.names():
            spec = registry.get(name)
            if not spec.dangerous:
                continue
            family = classify_family(name)
            self.assertIn(family, VERIFYING_FAMILIES | {"none"},
                          f"dangerous tool {name} has family {family!r}")

    def test_declared_family_overrides_the_name_heuristic(self):
        """`ui_list_controls` reads like a UI action but is really a query."""
        self.assertEqual(classify_family("ui_list_controls"), "query")
        self.assertEqual(classify_family("list_open_windows"), "query")
        self.assertEqual(classify_family("list_wifi_networks"), "query")
        self.assertEqual(classify_family("get_clipboard"), "query")

    def test_unknown_tool_names_still_return_none(self):
        self.assertIsNone(classify_family("definitely_not_a_tool"))
        self.assertIsNone(declared_family("definitely_not_a_tool"))

    def test_time_varying_query_tools_opt_out_of_caching(self):
        """A cached 'battery status' answer would be stale by definition."""
        for name in ("battery_status", "get_datetime", "list_processes",
                     "gmail_inbox", "read_file"):
            verification = registry.get(name).verification or {}
            self.assertIs(verification.get("cacheable"), False,
                          f"{name} should declare cacheable=False")


if __name__ == "__main__":
    unittest.main()
