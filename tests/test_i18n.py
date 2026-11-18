"""Translations: Arabic covers every English key with the same placeholders; numbers stay in LTR spans."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from p7 import fmt
from p7.i18n import PLURAL_FORMS, Translator, direction, plural_category, table
from p7.ingest.messages import CODES, Msg

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


# --- upload log messages: code + parameters, rendered through i18n ---

SRC = Path(__file__).parent.parent / "src" / "p7"
SAMPLE_PARAMS = {"count": 600, "limit": 500, "error": "KeyError", "site": "ZZZ-01", "file_site": "RUH-01",
                 "file_month": "2026-05", "cover_site": "JED-01", "cover_month": "2026-05", "month": "2026-06",
                 "row": 3, "n": 2, "upload_id": 12, "value": "abc", "time": "10:30:00", "minutes": 60,
                 "temperature": "x", "humidity": "y", "date": "2026-09-01", "timestamp": "2026-08-03 10:00",
                 "rows": "2, 3"}
# Latin words an Arabic message may contain: format and sheet names the user sees in Excel, header names, examples
LATIN_OK = {"zip", "xlsx", "Excel", "Office", "Open", "XML", "Info", "Site", "code", "Month", "Date", "Time",
            "Temperature", "Humidity", "RUH", "DD", "MM", "YYYY", "a", "b", "C"}


def test_every_upload_message_code_exists_in_en_and_ar():
    for code, params in CODES.items():
        for lang, tbl in (("en", EN), ("ar", AR)):
            entry = tbl.get(f"upload_msg_{code}")
            assert entry, f"upload_msg_{code} missing in {lang}.json"
            for s in forms(entry):
                assert placeholders(s) == set(params), (lang, code, s)
    for tbl in (EN, AR):
        assert {k.removeprefix("upload_msg_") for k in tbl if k.startswith("upload_msg_")} == set(CODES)


def test_every_message_code_used_by_ingestion_is_defined():
    text = "\n".join(p.read_text() for p in (SRC / "ingest").glob("*.py"))
    used = set(re.findall(r"""(?:FileRejected|\bmsg)\(\s*"([a-z_]+)\"""", text))
    assert used == set(CODES)  # every code is used, and nothing is raised without one
    # no free-text warnings or rejections are left: every FileRejected( starts with a code
    for call in re.findall(r"FileRejected\(([^)]{0,40})", text):
        assert re.match(r'"[a-z_]+"$|"[a-z_]+",|Exception$|pre_rejected\.code', call), call
    assert not re.search(r"""warnings(?:\.append\(|\s*\+?=\s*\[)\s*f?["']""", text)
    # every rejected row carries a coded message, not free text
    assert not re.search(r"""Reject\([^()]*,\s*"[a-z_]+",\s*f?["']""", text)


@pytest.mark.parametrize("code", sorted(CODES))
def test_upload_messages_render_in_arabic(code):
    m = Msg(code, {p: SAMPLE_PARAMS[p] for p in CODES[code]})
    html = str(Translator("ar").msg(m))
    assert html == str(Translator("ar").msg(m.to_json()))  # same from the JSON stored in uploads
    text = re.sub(r'<span class="num" dir="ltr">[^<]*</span>', "", html)
    assert ARABIC.search(text)
    assert set(re.findall(r"[A-Za-z]+", text)) <= LATIN_OK, code
    for v in m.params.values():  # parameters are isolated LTR runs
        assert f'<span class="num" dir="ltr">{v}</span>' in html
    assert str(m) == Translator("en").msg_plain(m) and not ARABIC.search(str(m))


def test_legacy_plain_text_messages_are_escaped():
    assert str(Translator("ar").msg("old <b>warning</b>")) == "old &lt;b&gt;warning&lt;/b&gt;"


def test_csv_messages_keep_parameters_left_to_right_in_arabic():
    m = Msg("row_duplicate_conflict", {"timestamp": "2026-08-03 10:00", "rows": "2, 3"})
    assert Translator("ar").msg_plain(m, ltr_marks=True) == \
        "\u200e2026-08-03 10:00\u200e موجود في الصفوف \u200e2, 3\u200e بقيم مختلفة"
    assert Translator("en").msg_plain(m, ltr_marks=True) == \
        "2026-08-03 10:00 appears in rows 2, 3 with different values"
