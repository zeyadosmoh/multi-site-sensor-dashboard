"""Upload log messages (file-level errors and warnings) as a code plus parameters.

They are rendered through i18n (key `upload_msg_<code>`), so the upload log reads in the viewer's language. The
uploads table stores the code and parameters: `warnings` is a JSON list of {"code", "params"}, and file errors use
`error_code` / `error_params`. The `error` column keeps the English text for the CLI and for psql.
Parameter values are identifiers, numbers and dates (site codes, months, row numbers): never translated.
"""

from dataclasses import dataclass, field

# code -> parameter names. A test checks every code has an English and an Arabic text with these placeholders.
CODES: dict[str, tuple[str, ...]] = {
    # file-level errors (status rejected_file, or failed for internal_error)
    "zip_invalid": (),
    "zip_too_many_files": ("count", "limit"),
    "zip_too_large": (),
    "zip_member_too_large": (),
    "zip_nested": (),
    "not_xlsx": (),
    "xlsx_invalid": (),
    "xlsx_no_workbook": (),
    "xlsx_macro": (),
    "xlsx_too_large": (),
    "xlsx_unreadable": ("error",),
    "no_header": (),
    "unknown_site": ("site",),
    "site_month_conflict": ("file_site", "file_month", "cover_site", "cover_month"),
    "site_month_unknown": (),
    "wrong_month": ("month",),
    "internal_error": ("error",),
    # warnings (the file still loads)
    "header_row": ("row", "n"),
    "cover_sheet": (),
    "empty_rows": ("n",),
    "dates_dmy": (),
    "dates_mdy": (),
    "dates_assumed_dmy": (),
    "dates_mixed": (),
    "dates_serial": (),
    "duplicate_file": ("upload_id",),
}


@dataclass(frozen=True)
class Msg:
    code: str
    params: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.code not in CODES:
            raise ValueError(f"unknown upload message code: {self.code}")
        if set(self.params) != set(CODES[self.code]):
            raise ValueError(f"{self.code} takes {CODES[self.code]}, got {sorted(self.params)}")

    def to_json(self) -> dict:
        return {"code": self.code, "params": self.params}

    def __str__(self) -> str:
        from p7.i18n import Translator

        return Translator("en").msg_plain(self)


def msg(code: str, **params) -> Msg:
    return Msg(code, params)
