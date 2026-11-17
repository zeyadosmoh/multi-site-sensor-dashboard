"""Check i18n coverage: every key used in templates/code exists in en.json, and every en key exists in ar.json."""
import json
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent / "src" / "p7"
en = json.loads((root / "i18n" / "en.json").read_text())
used = set()
for p in list(root.rglob("*.html")) + list(root.rglob("*.j2")) + list(root.rglob("*.py")):
    used |= set(re.findall(r"""\bt(?:\.plain|\.plural|\.plural_plain)?\(\s*["']([a-z0-9_]+)["']""", p.read_text()))
dynamic = {"status_": ["green", "amber", "red"],
           "reason_": ["low_completeness", "long_out_of_range", "stuck_sensor", "incomplete", "out_of_range"],
           "kind_": ["warehouse", "branch"], "event_": ["temperature", "humidity", "both"],
           "reject_": ["bad_date", "bad_time", "missing_value", "non_numeric", "temp_impossible", "humidity_impossible",
                       "outside_file_month", "duplicate_exact", "duplicate_conflict"],
           "upload_": ["loaded", "duplicate_file", "rejected_file", "failed", "processing"],
           "report_": ["fresh", "stale", "missing"], "role_": ["admin", "management"],
           "lang_name_": ["en", "ar"], "switch_to_": ["en", "ar"], "error_": ["400", "403", "404", "413", "other"],
           "month_": [str(i) for i in range(1, 13)]}
for prefix, vals in dynamic.items():
    used |= {prefix + v for v in vals}
used = {k for k in used if not k.endswith("_")}
# CSV export column labels in web/views.py are passed through a variable
used |= set(re.findall(r"\(\"[a-z_]+\", \"([a-z_]+)\", (?:None|\d)\)", (root / "web" / "views.py").read_text()))
missing = sorted(used - en.keys())
unused = sorted(en.keys() - used)
ar = json.loads((root / "i18n" / "ar.json").read_text()) if (root / "i18n" / "ar.json").exists() else {}
ar_missing = sorted(en.keys() - ar.keys()) if ar else []
# plural entries (JSON objects) need every CLDR form of their language, and the same kind of entry in both files
FORMS = {"en": {"one", "other"}, "ar": {"zero", "one", "two", "few", "many", "other"}}
bad_plurals = []
for lang, d in (("en", en), ("ar", ar)):
    for k, v in d.items():
        if isinstance(v, dict) and set(v) != FORMS[lang]:
            bad_plurals.append(f"{lang}:{k} forms {sorted(v)}")
        if k in en and k in ar and isinstance(en[k], dict) != isinstance(ar[k], dict):
            bad_plurals.append(f"{lang}:{k} plural in one file only")
print("missing in en.json:", missing)
print("unused in en.json:", unused)
print("en keys missing in ar.json:", ar_missing)
print("incomplete plural entries:", sorted(set(bad_plurals)))
sys.exit(1 if missing or ar_missing or bad_plurals else 0)
