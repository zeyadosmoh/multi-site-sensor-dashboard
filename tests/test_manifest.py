"""End to end on the full synthetic dataset: generate (seed 7) -> ingest all 78 files -> compare with the
generator's manifest.json (ground truth for every planted problem). Then re-ingest: nothing changes."""

import hashlib
from pathlib import Path

import pytest

from p7.generate.files import generate
from p7.ingest.pipeline import ingest_paths
from p7.verify import compare_with_manifest


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    generate(out, 7)
    return out


def test_generator_is_deterministic(dataset, tmp_path):
    generate(tmp_path, 7)
    a = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (dataset / "xlsx").iterdir()}
    b = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path / "xlsx").iterdir()}
    assert len(a) == 78 and a == b
    assert (dataset / "manifest.json").read_text() == (tmp_path / "manifest.json").read_text()


def test_every_file_carries_the_synthetic_label(dataset):
    from openpyxl import load_workbook

    for p in sorted((dataset / "xlsx").iterdir())[:13]:
        wb = load_workbook(p, read_only=True)
        cells = [c for ws in wb.worksheets for row in ws.iter_rows(max_row=4, values_only=True) for c in row]
        assert any(isinstance(c, str) and "SYNTHETIC" in c for c in cells), p.name
        assert "SYNTHETIC" in wb.properties.title


def test_ingestion_matches_manifest_exactly(conn, dataset):
    results = ingest_paths(conn, [dataset / "xlsx"])
    assert len(results) == 78 and {r.status for r in results} == {"loaded"}
    r = compare_with_manifest(conn, dataset / "manifest.json")
    assert [f for f in r["files"] if not f["ok"]] == []
    assert r["readings_stored"] == r["readings_expected"] == 57109
    assert r["rejects_expected"] == {"bad_date": 1, "bad_time": 1, "duplicate_conflict": 6, "duplicate_exact": 5,
                                     "humidity_impossible": 2, "missing_value": 1, "non_numeric": 1,
                                     "outside_file_month": 2, "temp_impossible": 2}
    assert r["stuck_detected"] == r["stuck_expected"]                # exactly one run, exact start/end
    assert all(o["readings_in_window"] == 0 for o in r["outages"])
    ev = {e["kind"]: e for e in r["events"]}
    assert ev["ac_failure"]["detected_events"] == 1 and ev["ac_failure"]["detected_readings"] >= 50
    assert ev["water_leak"]["detected_readings"] == 9
    assert ev["heat_wave"]["detected_events"] == 3                   # three hot afternoons

    # file-level variations were handled (and said so)
    by_name = {f["filename"]: f for f in r["files"]}
    assert by_name["sensor_export_final (2).xlsx"]["detected_by"] == "cover_sheet"
    # warnings are stored as code + parameters (rendered in the viewer's language)
    assert {"code": "dates_dmy", "params": {}} in by_name["JED-02_2026-07.xlsx"]["warnings"]
    assert {"code": "dates_serial", "params": {}} in by_name["MAK-01_2026-10.xlsx"]["warnings"]
    assert {"code": "header_row", "params": {"row": 3, "n": 2}} in by_name["RUH-02_2026-06.xlsx"]["warnings"]
    assert {"code": "cover_sheet", "params": {}} in by_name["sensor_export_final (2).xlsx"]["warnings"]

    # idempotent: the whole folder again changes nothing
    q = "SELECT count(*) AS n, sum(temperature) AS t, max(updated_at) AS u FROM readings"
    snapshot = conn.execute(q).fetchone()
    again = ingest_paths(conn, [dataset / "xlsx"])
    assert {x.status for x in again} == {"duplicate_file"}
    assert conn.execute("SELECT count(*) AS n, sum(temperature) AS t, max(updated_at) AS u FROM readings"
                        ).fetchone() == snapshot


def test_committed_sample_zip_matches_generator(dataset):
    """data/sample/sensors_2026-08.zip is what `p7 generate --sample-month 2026-08` writes."""
    import zipfile

    sample = Path(__file__).parent.parent / "data" / "sample" / "sensors_2026-08.zip"
    with zipfile.ZipFile(sample) as z:
        for name in z.namelist():
            assert hashlib.sha256(z.read(name)).hexdigest() == \
                hashlib.sha256((dataset / "xlsx" / name).read_bytes()).hexdigest(), name
        assert len(z.namelist()) == 13
