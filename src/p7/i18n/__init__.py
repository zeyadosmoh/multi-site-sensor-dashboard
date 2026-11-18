"""UI and report strings. One JSON file per language; English is the fallback for any missing key.

Numbers are never translated or reordered: templates wrap every number in its own LTR span (see p7.fmt), which
keeps "24.6", "99.5%" and "2026-08-12 13:00" intact inside right-to-left Arabic text.

Counted phrases ("1 event", "6 ساعات") are plural entries: a JSON object keyed by CLDR plural category instead of
a string. English uses one/other; Arabic uses zero/one/two/few/many/other. Use `t.plural(key, count, ...)`.
"""

import json
from decimal import Decimal, InvalidOperation
from functools import cache
from importlib.resources import files

from markupsafe import Markup, escape

from p7 import fmt

LANGS = ("en", "ar")
DEFAULT_LANG = "en"
RTL = {"ar"}


PLURAL_FORMS = {"en": ("one", "other"), "ar": ("zero", "one", "two", "few", "many", "other")}


@cache
def table(lang: str) -> dict[str, str | dict[str, str]]:
    path = files("p7.i18n").joinpath(f"{lang}.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def normalise(lang: str | None) -> str:
    return lang if lang in LANGS and table(lang) else DEFAULT_LANG


def direction(lang: str) -> str:
    return "rtl" if lang in RTL else "ltr"


def plural_category(lang: str, count) -> str:
    """CLDR cardinal plural category of a number as it is displayed: 3, Decimal("6.0"), "1,234", "2.5".
    Anything that is not a number (None, the en dash) is 'other'."""
    if count is None or isinstance(count, bool):
        return "other"
    try:
        d = Decimal(str(count).replace(",", "").strip())
    except InvalidOperation:
        return "other"
    if not d.is_finite():
        return "other"
    d = abs(d)
    whole = d == d.to_integral_value()
    if lang == "ar":
        # CLDR ar: n = 0 zero; n = 1 one; n = 2 two; n % 100 = 3..10 few; n % 100 = 11..99 many; else other.
        # n is the numeric value, so "6.0" is few like 6; a fraction such as 2.5 is other.
        if not whole:
            return "other"
        n = int(d)
        if n in (0, 1, 2):
            return ("zero", "one", "two")[n]
        if 3 <= n % 100 <= 10:
            return "few"
        if 11 <= n % 100 <= 99:
            return "many"
        return "other"
    # CLDR en: one = integer 1 with no visible decimals ("1 event", but "1.0 hours")
    return "one" if d == 1 and d.as_tuple().exponent >= 0 else "other"


class Translator:
    def __init__(self, lang: str):
        self.lang = normalise(lang)
        self._t = table(self.lang)
        self._en = table(DEFAULT_LANG)

    def _entry(self, key: str):
        s = self._t.get(key) or self._en.get(key)
        if s is None:
            raise KeyError(f"missing i18n key: {key}")
        return s

    def _string(self, key: str) -> str:
        s = self._entry(key)
        if isinstance(s, dict):
            raise TypeError(f"i18n key {key} has plural forms; use t.plural({key!r}, count, ...)")
        return s

    def _form(self, key: str, count) -> str:
        forms = self._t.get(key) or self._en.get(key)
        if forms is None:
            raise KeyError(f"missing i18n key: {key}")
        if not isinstance(forms, dict):
            raise TypeError(f"i18n key {key} has no plural forms; use t({key!r})")
        lang = self.lang if key in self._t else DEFAULT_LANG
        return forms.get(plural_category(lang, count)) or forms["other"]

    def __call__(self, key: str, **kw) -> Markup:
        s = self._string(key)
        # strings are trusted (our own files); arguments are escaped unless they are Markup (e.g. number spans)
        return Markup(s).format(**kw) if kw else Markup(s)

    def plain(self, key: str, **kw) -> str:
        """Same, as plain text (chart labels, CSV headers, HTTP headers)."""
        s = self._string(key)
        return s.format(**kw) if kw else s

    def plural(self, key: str, count, **kw) -> Markup:
        """The form of a plural entry that fits `count` (a number, or a number as displayed, e.g. "6.0").
        Pass the displayed number as a placeholder (n=ni(count)) when the phrase contains it."""
        s = self._form(key, count)
        return Markup(s).format(**kw) if kw else Markup(s)

    def plural_plain(self, key: str, count, **kw) -> str:
        s = self._form(key, count)
        return s.format(**kw) if kw else s

    def _msg_parts(self, m) -> tuple[str, dict]:
        code, params = (m.code, m.params) if hasattr(m, "code") else (m["code"], m.get("params") or {})
        return f"upload_msg_{code}", params

    def msg(self, m) -> Markup:
        """An upload log message (p7.ingest.messages.Msg, or its stored JSON {"code", "params"}) in this language.
        Parameters are site codes, months, row numbers, file names: each is its own LTR span. A plain string is
        an upload logged before message codes existed and is shown as it is."""
        if m is None:
            return Markup("")
        if isinstance(m, str):
            return escape(m)
        key, params = self._msg_parts(m)
        kw = {k: fmt.n(str(v)) for k, v in params.items()}
        if isinstance(self._entry(key), dict):
            return self.plural(key, params["n"], **kw)
        return self(key, **kw)

    def msg_plain(self, m, ltr_marks: bool = False) -> str:
        """Plain text. ltr_marks (for CSV cells): in a right-to-left language each parameter is wrapped in
        left-to-right marks, so "-60", "2026-08-01 10:00" or "2, 3" keep their order inside the Arabic text."""
        if isinstance(m, str):
            return m
        key, params = self._msg_parts(m)
        if ltr_marks and direction(self.lang) == "rtl":
            params = {k: f"\u200e{v}\u200e" for k, v in params.items()}
        if isinstance(self._entry(key), dict):
            return self.plural_plain(key, params["n"], **params)
        return self.plain(key, **params)

    def month(self, d) -> str:
        return f"{self.plain(f'month_{d.month}')} {d.year}" if d else self.plain("all_months")
