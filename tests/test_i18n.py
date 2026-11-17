"""Translations: Arabic covers every English key with the same placeholders; numbers stay in LTR spans."""

import re
import subprocess
import sys
from pathlib import Path

from p7 import fmt
from p7.i18n import Translator, direction, table

EN, AR = table("en"), table("ar")
ARABIC = re.compile(r"[؀-ۿ]")
NOT_ARABIC_BY_DESIGN = {"switch_to_en"}  # the link to English is written in English


def test_every_english_key_exists_in_arabic():
    assert sorted(EN.keys() - AR.keys()) == []
    assert sorted(AR.keys() - EN.keys()) == []


def test_placeholders_match():
    for k, v in EN.items():
        assert set(re.findall(r"\{(\w+)\}", v)) == set(re.findall(r"\{(\w+)\}", AR[k])), k


def test_arabic_values_are_arabic_and_not_empty():
    for k, v in AR.items():
        assert v.strip(), k
        if k not in NOT_ARABIC_BY_DESIGN:
            assert ARABIC.search(v), k


def test_every_key_used_in_code_exists():
    script = Path(__file__).parent.parent / "scripts" / "check_i18n_keys.py"
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_numbers_are_isolated_ltr_runs():
    t = Translator("ar")
    html = str(t("n_unreliable", n=fmt.n(1234, 0)))
    assert html == '<span class="num" dir="ltr">1,234</span> قراءة غير موثوقة'
    assert str(fmt.n(99.66, 1, "%")) == '<span class="num" dir="ltr">99.7%</span>'
    assert direction("ar") == "rtl" and direction("en") == "ltr"


def test_translator_escapes_arguments():
    t = Translator("en")
    assert "&lt;b&gt;" in str(t("n_unreliable", n="<b>"))
    assert t.month(None) == "All months"
    from datetime import date

    assert Translator("ar").month(date(2026, 8, 1)) == "أغسطس 2026"
