"""List i18n keys used in templates/code (literal t("...") calls + known dynamic prefixes) that are missing."""
import json
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent / "src" / "p7"
en = json.loads((root / "i18n" / "en.json").read_text())
used = set()
for p in list(root.rglob("*.html")) + list(root.rglob("*.j2")) + list(root.rglob("*.py")):
    used |= set(re.findall(r"""\bt(?:\.plain)?\(\s*["']([a-z0-9_]+)["']""", p.read_text()))
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
print("missing:", missing)
print("unused:", unused)
sys.exit(1 if missing else 0)
