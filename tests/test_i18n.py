"""Translations: Arabic covers every English key with the same placeholders; numbers stay in LTR spans."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from p7 import fmt
from p7.i18n import PLURAL_FORMS, Translator, direction, plural_category, table

EN, AR = table("en"), table("ar")
ARABIC = re.compile(r"[؀-ۿ]")
NOT_ARABIC_BY_DESIGN = {"switch_to_en"}  # the link to English is written in English


def test_every_english_key_exists_in_arabic():
    assert sorted(EN.keys() - AR.keys()) == []
    assert sorted(AR.keys() - EN.keys()) == []


def forms(v) -> list[str]:
    return list(v.values()) if isinstance(v, dict) else [v]


def placeholders(s: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", s))


def test_placeholders_match():
    for k, v in EN.items():
        want = placeholders(forms(v)[-1])
        for s in forms(v) + forms(AR[k]):
            assert placeholders(s) == want, (k, s)


def test_plural_entries_have_every_form_of_their_language():
    plural_keys = {k for k, v in EN.items() if isinstance(v, dict)}
    assert plural_keys == {k for k, v in AR.items() if isinstance(v, dict)}
    assert {"n_unreliable", "events_lower", "unit_h", "sites_lower", "stuck_runs_lower"} <= plural_keys
    for k in plural_keys:
        assert set(EN[k]) == set(PLURAL_FORMS["en"]), k
        assert set(AR[k]) == set(PLURAL_FORMS["ar"]), k


def test_arabic_values_are_arabic_and_not_empty():
    for k, v in AR.items():
        for s in forms(v):
            assert s.strip(), k
            if k not in NOT_ARABIC_BY_DESIGN:
                assert ARABIC.search(s), k


def test_every_key_used_in_code_exists():
    script = Path(__file__).parent.parent / "scripts" / "check_i18n_keys.py"
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_numbers_are_isolated_ltr_runs():
    t = Translator("ar")
    html = str(t.plural("n_unreliable", 1234, n=fmt.n(1234, 0)))
    assert html == '<span class="num" dir="ltr">1,234</span> قراءة غير موثوقة'
    assert str(fmt.n(99.66, 1, "%")) == '<span class="num" dir="ltr">99.7%</span>'
    assert direction("ar") == "rtl" and direction("en") == "ltr"


def test_translator_escapes_arguments():
    t = Translator("en")
    assert "&lt;b&gt;" in str(t.plural("n_unreliable", 5, n="<b>"))
    assert t.month(None) == "All months"
    from datetime import date

    assert Translator("ar").month(date(2026, 8, 1)) == "أغسطس 2026"


@pytest.mark.parametrize("count, en, ar", [
    (0, "other", "zero"), (1, "one", "one"), (2, "other", "two"), (3, "other", "few"), (10, "other", "few"),
    (11, "other", "many"), (99, "other", "many"), (100, "other", "other"), (102, "other", "other"),
    (103, "other", "few"), (111, "other", "many"), ("1,234", "other", "many"),
    # numbers as displayed: "6.0" counts like 6 in Arabic; English "1.0" is plural; fractions are 'other'
    ("6.0", "other", "few"), ("1.0", "other", "one"), ("2.0", "other", "two"), ("2.5", "other", "other"),
    ("–", "other", "other"), (None, "other", "other"),
])
def test_plural_category(count, en, ar):
    assert (plural_category("en", count), plural_category("ar", count)) == (en, ar)


EVENTS = {"en": ["0 events", "1 event", "2 events", "3 events", "11 events"],
          "ar": ["0 حالة", "1 حالة", "2 حالتان", "3 حالات", "11 حالة"]}
HOURS = {"en": ["0 h", "1 h", "2 h", "3 h", "11 h"],
         "ar": ["0 ساعة", "1 ساعة", "2 ساعتان", "3 ساعات", "11 ساعة"]}
READINGS = {"en": ["0 unreliable readings", "1 unreliable reading", "2 unreliable readings", "3 unreliable readings",
                   "11 unreliable readings"],
            "ar": ["0 قراءة غير موثوقة", "1 قراءة غير موثوقة", "2 قراءتان غير موثوقتين", "3 قراءات غير موثوقة",
                   "11 قراءة غير موثوقة"]}


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_plurals_0_1_2_3_11(lang):
    t = Translator(lang)
    counts = [0, 1, 2, 3, 11]
    assert [f"{c} {t.plural_plain('events_lower', c)}" for c in counts] == EVENTS[lang]
    assert [f"{c} {t.plural_plain('unit_h', c)}" for c in counts] == HOURS[lang]
    assert [t.plural_plain("n_unreliable", c, n=c) for c in counts] == READINGS[lang]


def test_plural_follows_the_displayed_number():
    ar = Translator("ar")
    assert ar.plural_plain("unit_h", fmt.hours(360)) == "ساعات"  # "6.0"
    assert ar.plural_plain("unit_h", fmt.hours(150)) == "ساعة"  # "2.5"
    assert ar.plural_plain("unit_h", fmt.hours(3720)) == "ساعة"  # "62.0"
    assert Translator("en").plural_plain("sites_lower", "13") == "sites"
    html = str(ar.plural("n_unreliable", 30, n=fmt.n(30, 0)))
    assert html == '<span class="num" dir="ltr">30</span> قراءة غير موثوقة'


def test_plural_and_plain_keys_are_not_mixed_up():
    t = Translator("en")
    with pytest.raises(TypeError):
        t("events_lower")
    with pytest.raises(TypeError):
        t.plural("completeness", 1)
