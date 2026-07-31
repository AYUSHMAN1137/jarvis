"""Signature matching must be permissive about phrasing and strict about meaning.

A cache hit EXECUTES something, so a false positive performs the wrong action on
the user's machine. These cases lock in both halves of that contract.
"""

from app.services.agent.cache.signature import (
    command_signature, match_command, signatures_match,
)


def matches(a: str, b: str) -> bool:
    return signatures_match(command_signature(a), command_signature(b))


class TestSamePhrasingDifferentWords:
    """Paraphrases of the same request must match -- this is why the cache
    previously scored zero hits across a whole session."""

    def test_hindi_english_mix(self):
        assert matches("turn off the bluetooth", "bluetooth off karo")

    def test_politeness_ignored(self):
        assert matches("please turn on the wifi", "wifi on")

    def test_extra_verb_is_compatible(self):
        assert matches("check for update", "so click on check update")

    def test_word_order(self):
        assert matches("set volume to 30", "volume 30 kar do")

    def test_trailing_punctuation(self):
        assert matches("open notepad", "open notepad.")


class TestMeaningMustNotBlur:
    """The dangerous direction. Each of these would run the wrong action."""

    def test_open_is_not_close(self):
        assert not matches("open notepad", "close notepad")

    def test_on_is_not_off(self):
        assert not matches("turn on bluetooth", "turn off bluetooth")

    def test_different_numbers(self):
        assert not matches("set volume to 30", "set volume to 70")

    def test_different_subject(self):
        assert not matches("turn off bluetooth", "turn off wifi")

    def test_different_app(self):
        assert not matches("open notepad", "open calculator")

    def test_empty_command(self):
        assert not matches("", "open notepad")


class TestMatchCommand:
    def test_picks_the_right_candidate(self):
        stored = ["turn off the bluetooth", "turn on the bluetooth",
                  "set volume to 30", "open notepad"]
        found = match_command("bluetooth band karo", stored)
        assert found is not None
        assert found[0] == "turn off the bluetooth"

    def test_returns_none_when_nothing_fits(self):
        stored = ["open notepad", "set volume to 30"]
        assert match_command("send an email to my boss", stored) is None

    def test_never_crosses_on_off(self):
        stored = ["turn on the bluetooth"]
        assert match_command("turn off the bluetooth", stored) is None


class TestSignatureParts:
    def test_state_extracted(self):
        assert command_signature("turn off night light").state == "off"
        assert command_signature("turn on night light").state == "on"

    def test_numbers_extracted(self):
        assert command_signature("set brightness to 70").numbers == (70.0,)

    def test_filler_dropped(self):
        assert command_signature("please open the notepad for me").core == frozenset({"notepad"})
