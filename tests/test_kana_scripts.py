"""The kana script selector must actually select scripts.

Regression guard for a silent-fallback bug: the settings layer spoke
"hiragana"/"katakana"/"mixed" while kana.sample() only understood
"hira"/"kata"/"both" — every choice quietly became "both", so the
Hiragana-only and Katakana-only options never worked for anyone.
"""
import random

from kanjire import kana
from kanjire.game import menuconfig as mc

HIRA = set("ぁあぃいぅうぇえぉおかがきぎくぐけげこごさざしじすずせぜそぞ"
           "ただちぢっつづてでとどなにぬねのはばぱひびぴふぶぷへべぺほぼぽ"
           "まみむめもゃやゅゆょよらりるれろゎわゐゑをんゔゕゖー")
KATA = set("ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾ"
           "タダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポ"
           "マミムメモャヤュユョヨラリルレロヮワヰヱヲンヴヵヶー")


def _script_of(text: str) -> str:
    chars = set(text) - {"ー"}
    if chars <= HIRA:
        return "hira"
    if chars <= KATA:
        return "kata"
    return "mixed"


def _check(script_value: str, expect: str) -> None:
    words = kana.sample(8, length=2, script=script_value,
                        rng=random.Random(7))
    assert words, "sampler returned nothing"
    for w in words:
        assert _script_of(w.expression) == expect, (
            script_value, w.expression)
        assert _script_of(w.reading) == expect, (script_value, w.reading)


def test_native_values_select_their_script():
    _check("hira", "hira")
    _check("kata", "kata")


def test_settings_layer_long_names_are_aliased():
    # These are the values the Kivy UI persisted for months.
    _check("hiragana", "hira")
    _check("katakana", "kata")


def test_both_pairs_hiragana_with_katakana():
    for w in kana.sample(8, length=2, script="both", rng=random.Random(7)):
        assert _script_of(w.expression) == "hira"
        assert _script_of(w.reading) == "kata"
    # "mixed" (the old Kivy long name) means the same thing.
    for w in kana.sample(4, length=1, script="mixed", rng=random.Random(3)):
        assert _script_of(w.expression) == "hira"
        assert _script_of(w.reading) == "kata"


def test_normalized_settings_canonicalises_kana_script():
    assert mc.normalized_settings({"kana_script": "hiragana"})[
        "kana_script"] == "hira"
    assert mc.normalized_settings({"kana_script": "katakana"})[
        "kana_script"] == "kata"
    assert mc.normalized_settings({"kana_script": "mixed"})[
        "kana_script"] == "both"
    assert mc.normalized_settings({"kana_script": "kata"})[
        "kana_script"] == "kata"
    # Unknown junk → default ("both", the historical effective behaviour).
    assert mc.normalized_settings({"kana_script": "nope"})[
        "kana_script"] == "both"
    assert mc.normalized_settings({})["kana_script"] == "both"
