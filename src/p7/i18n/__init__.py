"""UI and report strings. One JSON file per language; English is the fallback for any missing key.

Numbers are never translated or reordered: templates wrap every number in its own LTR span (see p7.fmt), which
keeps "24.6", "99.5%" and "2026-08-12 13:00" intact inside right-to-left Arabic text.
"""

import json
from functools import cache
from importlib.resources import files

from markupsafe import Markup

LANGS = ("en", "ar")
DEFAULT_LANG = "en"
RTL = {"ar"}


@cache
def table(lang: str) -> dict[str, str]:
    path = files("p7.i18n").joinpath(f"{lang}.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def normalise(lang: str | None) -> str:
    return lang if lang in LANGS and table(lang) else DEFAULT_LANG


def direction(lang: str) -> str:
    return "rtl" if lang in RTL else "ltr"


class Translator:
    def __init__(self, lang: str):
        self.lang = normalise(lang)
        self._t = table(self.lang)
        self._en = table(DEFAULT_LANG)

    def __call__(self, key: str, **kw) -> Markup:
        s = self._t.get(key) or self._en.get(key)
        if s is None:
            raise KeyError(f"missing i18n key: {key}")
        # strings are trusted (our own files); arguments are escaped unless they are Markup (e.g. number spans)
        return Markup(s).format(**kw) if kw else Markup(s)

    def plain(self, key: str, **kw) -> str:
        """Same, as plain text (chart labels, CSV headers, HTTP headers)."""
        s = self._t.get(key) or self._en.get(key)
        if s is None:
            raise KeyError(f"missing i18n key: {key}")
        return s.format(**kw) if kw else s

    def month(self, d) -> str:
        return f"{self.plain(f'month_{d.month}')} {d.year}" if d else self.plain("all_months")
