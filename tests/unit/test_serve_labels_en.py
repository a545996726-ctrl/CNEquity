"""The dashboard's server-side display text exists in English too.

Operation titles, summaries, form labels, confirmations and the settings page
come from Python in Chinese; each payload adds a ``*_en`` sibling from
``cnequity.serve.labels_en``. A new operation or setting without an entry
would show Chinese on the English page, so it fails here instead.
"""

from __future__ import annotations

import re

from cnequity.serve.labels_en import EN, en
from cnequity.serve.ops import catalog, settings

_CJK = re.compile(r"[一-鿿]")


def _display_strings() -> list[str]:
    found: list[str] = []
    for spec in catalog.OPS:
        found += [spec.title, spec.summary, spec.group, spec.heavy_text, spec.needs_reason]
        for param in spec.params:
            found += [param.label, param.help, param.empty_reason]
    found.append(catalog.STANDARD_ACK["text"])
    found += [
        settings.HOME_NOTE,
        settings.NOTE_CHANGED,
        settings.NOTE_UNCHANGED,
        settings.ACKNOWLEDGEMENT,
        settings.PUSH2_ENV_NOTE,
        "开",
        "关",
    ]
    found += [title for _, title in settings.GROUPS]
    for spec in settings.SETTINGS:
        found += [spec.label, spec.help, *(label for _, label in spec.choices)]
    return [text for text in found if text and _CJK.search(text)]


def test_every_display_string_has_english():
    missing = sorted({text for text in _display_strings() if text not in EN})
    assert missing == []


def test_english_entries_are_english():
    chinese = {key: value for key, value in EN.items() if _CJK.search(value)}
    assert chinese == {}


def test_no_stale_entries():
    """An entry whose Chinese left the code is dead weight and hides renames."""
    stale = sorted(set(EN) - set(_display_strings()))
    assert stale == []


def test_cards_carry_english_siblings():
    cards = catalog.describe(None, setup=True)
    assert cards
    for card in cards:
        for key in ("title", "summary", "group"):
            assert card[f"{key}_en"] == en(card[key])
            assert not _CJK.search(card[f"{key}_en"] or ""), (card["id"], key)
        for param in card["params"]:
            assert not _CJK.search(param["label_en"] or ""), (card["id"], param["name"])
