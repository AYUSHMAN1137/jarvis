"""Regression gate for Rule #10 -- no hardcoded language guessing.

M13 deleted every list that tried to *guess what a sentence meant* from the words
in it. Those lists shared one failure mode: each miss needed another string, and
a miss was silent. "It's not playing" was not in the retry-complaint list, so a
failed action was answered with small talk.

This file fails CI if any of them comes back. It deliberately asserts on the
absence of NAMES as well as on shapes, because the cheapest way to regress is to
paste the old list back under its old name.

What is explicitly ALLOWED to stay, and why:
  * `_SETTINGS_URIS` (system_info_tools) -- `ms-settings:` deep links are
    Windows' own addresses. Removing an address makes JARVIS worse without
    making it smarter.
  * `detect_reference` (context_engine) -- ONE owner of reference detection,
    used to decide "I don't know what you mean, ask", never to decide a route.
  * `_IRREVERSIBLE` / `_IDEMPOTENT_SAFE` (learner) -- tool names, not language.
"""

import ast
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"


def source(relative: str) -> str:
    path = APP / relative
    return path.read_text(encoding="utf-8") if path.exists() else ""


class DeletedSymbolTests(unittest.TestCase):
    """The named offenders must not exist anywhere under app/."""

    FORBIDDEN = [
        # (symbol, what it used to guess)
        ("try_direct_web_command", "which web tool a sentence meant, by regex"),
        ("_OPEN_VERBS", "verbs that mean 'open'"),
        ("_PLAY_VERBS", "verbs that mean 'play'"),
        ("_SEARCH_VERBS", "verbs that mean 'search'"),
        ("_SITE_MAP", "site names -> URLs"),
        ("_APP_ALIASES", "app names -> launch commands"),
        ("_CLOSE_PRONOUNS", "words that mean 'the thing I just opened'"),
        ("_is_affirmative", "words that mean yes"),
        ("_is_negative", "words that mean no"),
        ("_is_retry_complaint", "phrases that mean 'that did not work'"),
        ("_needs_screen_look", "phrases that mean 'look at my screen'"),
        ("_rule_based_primary", "keyword routing without an LLM"),
        ("action_signals", "substrings that mean an action was requested"),
    ]

    def test_none_of_the_deleted_symbols_exist(self):
        offenders = []
        for path in APP.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(text, filename=str(path))
            defined = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    defined.add(node.name)
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    defined.add(node.id)
            for symbol, what in self.FORBIDDEN:
                if symbol in defined:
                    offenders.append(f"{path.relative_to(REPO)} defines {symbol} "
                                     f"(guessed: {what})")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_brain_service_is_deleted_not_left_dead(self):
        self.assertFalse((APP / "services" / "brain_service.py").exists())

    def test_nothing_imports_the_brain_service(self):
        offenders = [str(path.relative_to(REPO)) for path in APP.rglob("*.py")
                     if "brain_service import" in
                     path.read_text(encoding="utf-8", errors="replace")]
        self.assertEqual(offenders, [])


class NoPhraseListTests(unittest.TestCase):
    """Shape check: the routing layer must not test the utterance for phrases.

    A phrase list reads as a big tuple/set of natural-language strings compared
    against the message. This looks for that shape in the files that decide
    routing, so a *new* list under a *new* name is caught too.
    """

    ROUTING_FILES = ["services/chat_service.py", "services/resolver.py"]

    @staticmethod
    def _looks_like_a_phrase(value: str) -> bool:
        text = value.strip()
        # Multi-word natural language, not an identifier, key, URL or format.
        return (" " in text and len(text) > 3 and text.lower() == text
                and not text.startswith(("http", "ms-settings", "{", "[", "<", "-"))
                and "_" not in text and "%" not in text and ":" not in text)

    def test_routing_code_has_no_natural_language_collections(self):
        offenders = []
        for relative in self.ROUTING_FILES:
            text = source(relative)
            if not text:
                continue
            tree = ast.parse(text, filename=relative)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Tuple, ast.Set, ast.List)):
                    continue
                phrases = [element.value for element in node.elts
                           if isinstance(element, ast.Constant)
                           and isinstance(element.value, str)
                           and self._looks_like_a_phrase(element.value)]
                if len(phrases) >= 3:
                    offenders.append(
                        f"{relative}:{node.lineno} holds {len(phrases)} "
                        f"natural-language phrases, e.g. {phrases[:3]}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_resolver_prompt_is_instructions_not_a_match_list(self):
        """The prompt contains English on purpose -- it INSTRUCTS a model. What
        matters is that no code compares the utterance against it."""
        text = source("services/resolver.py")
        self.assertIn("_SYSTEM_PROMPT", text)
        for banned in ("_SYSTEM_PROMPT in ", " in _SYSTEM_PROMPT",
                       "startswith(_SYSTEM_PROMPT"):
            self.assertNotIn(banned, text)


class AllowedFactualDataTests(unittest.TestCase):
    """The kept data is Windows' own vocabulary, and must NOT be deleted."""

    def test_windows_settings_uris_are_kept(self):
        text = source("services/agent/tools/system_info_tools.py")
        self.assertIn("_SETTINGS_URIS", text)
        self.assertIn("ms-settings:", text)

    def test_reference_detection_still_has_exactly_one_owner(self):
        owners = [path.relative_to(REPO).as_posix() for path in APP.rglob("*.py")
                  if "def detect_reference" in
                  path.read_text(encoding="utf-8", errors="replace")]
        self.assertEqual(owners, ["app/services/context/context_engine.py"],
                         f"reference detection must not be duplicated: {owners}")


class ReplacementsExistTests(unittest.TestCase):
    """Deleting is only half the job -- the understanding layer must be wired in."""

    def test_the_resolver_is_the_routing_entry_point(self):
        text = source("services/chat_service.py")
        self.assertIn("from app.services.resolver import", text)
        self.assertIn("_understand(", text)

    def test_app_launching_asks_windows_instead_of_a_table(self):
        text = source("services/agent/tools/desktop_tools.py")
        self.assertIn("_resolve_launch_target", text)
        self.assertIn("_find_app_shortcut", text)
        self.assertIn("shutil.which", text)

    def test_url_normalisation_is_mechanical_only(self):
        """`_normalize_url` may reason about schemes and hostnames, never about
        which brand a word refers to.

        Scoped to the function body on purpose: a tool *description* naming
        example sites is prompt guidance for the model, not a matcher.
        """
        text = source("services/agent/tools/web_tools.py")
        self.assertIn("_normalize_url", text)
        tree = ast.parse(text, filename="web_tools.py")
        body = ""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_normalize_url":
                body = ast.get_source_segment(text, node) or ""
        self.assertTrue(body, "_normalize_url not found")
        func = ast.parse(body.strip()).body[0]
        statements = func.body
        if ast.get_docstring(func) is not None:
            statements = statements[1:]  # a docstring may name what was removed
        literals = [n.value.lower() for statement in statements
                    for n in ast.walk(statement)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        for site in ("facebook", "instagram", "netflix", "linkedin", "twitter",
                     "reddit", "spotify", "amazon", "wikipedia"):
            self.assertNotIn(
                site, " ".join(literals),
                f"'{site}' is a hardcoded site name, not a URL rule")

    def test_the_no_op_guard_is_control_flow_not_a_prompt_line(self):
        text = source("services/agent/agent_loop.py")
        self.assertIn("expect_action", text)
        self.assertIn("_NO_OP_NUDGE", text)
        self.assertIn("no_op_rejected", text)


if __name__ == "__main__":
    unittest.main()
