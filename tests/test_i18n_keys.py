"""Guards on the translation tables.

Duplicate keys in a dict literal are silent in Python — the later one simply
wins — so a key re-added in a new section quietly changes the text of an old
screen. That happened to ``BTN_BACK``; this catches the next one.
"""
from __future__ import annotations

import ast
from pathlib import Path

from kanjire.i18n import STRINGS, SUPPORTED

_I18N = Path(__file__).resolve().parent.parent / "kanjire" / "i18n.py"


def _literal_keys() -> dict[str, list[str]]:
    """Every key as written in the source, duplicates included."""
    tree = ast.parse(_I18N.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        # STRINGS carries a type annotation, so it's an AnnAssign.
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        else:
            continue
        if not any(getattr(t, "id", None) == "STRINGS" for t in targets):
            continue
        out: dict[str, list[str]] = {}
        for loc_node, table in zip(node.value.keys, node.value.values):
            out[loc_node.value] = [k.value for k in table.keys]
        return out
    raise AssertionError("STRINGS assignment not found in i18n.py")


def test_no_duplicate_keys_within_a_locale():
    for locale, keys in _literal_keys().items():
        seen, dupes = set(), []
        for k in keys:
            if k in seen:
                dupes.append(k)
            seen.add(k)
        assert not dupes, f"{locale} defines these twice: {sorted(set(dupes))}"


def test_every_locale_is_declared_and_populated():
    assert set(STRINGS) == set(SUPPORTED)
    for locale, table in STRINGS.items():
        assert table, f"{locale} is empty"


def test_french_has_no_keys_english_lacks():
    """English is the fallback table, so a French-only key can never resolve
    for an English player — it means the English string was forgotten."""
    extra = set(STRINGS["fr"]) - set(STRINGS["en"])
    assert not extra, f"French-only keys: {sorted(extra)}"


def test_format_placeholders_match_between_locales():
    import re

    def slots(text):
        return set(re.findall(r"\{(\w+)", text))

    mismatched = {}
    for key, en in STRINGS["en"].items():
        fr = STRINGS["fr"].get(key)
        if fr is not None and slots(en) != slots(fr):
            mismatched[key] = (sorted(slots(en)), sorted(slots(fr)))
    assert not mismatched, f"placeholder mismatch: {mismatched}"
