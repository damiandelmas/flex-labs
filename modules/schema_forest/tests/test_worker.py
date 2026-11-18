from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import zipfile
import xml.etree.ElementTree as ET

import pytest

from modules.schema_forest.compile import worker


SIG_A = "a" * 64
SIG_B = "b" * 64
SIG_C = "c" * 64
OCCURRENCE_ID = "1" * 64
XLSX_OCCURRENCE_ID = "2" * 64


def _json_line(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_line(value) + "\n", encoding="utf-8")


def _compress_lines(path: Path, lines: list[str]) -> None:
    plain = path.with_suffix("")
    plain.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    subprocess.run(
        ["zstd", "-q", "-f", str(plain), "-o", str(path)],
        check=True,
    )
    plain.unlink()


def _occurrence(
    *,
    content_sha: str,
    resource_name: str | None,
    route: str,
    signature: str,
    source_capture: str,
    status: str,
    depth: int,
    tika_error: str | None = None,
) -> dict:
    return {
        "content_sha256": content_sha,
        "content_type": "application/octet-stream",
        "embedded_depth": depth,
        "occurrence_id": OCCURRENCE_ID,
        "resource_name": resource_name,
        "route": route,
        "schema_signature": signature,
        "source_capture": source_capture,
        "status": status,
        "tika_error": tika_error,
    }


def _seal(capture: Path, streams: dict[str, list[str]], *, totals: dict | None = None) -> None:
    schemas = capture / ".schemas"
    schemas.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    for filename, lines in streams.items():
        target = schemas / filename
        _compress_lines(target, lines)
        artifacts[filename] = {"bytes": target.stat().st_size, "sha256": _sha(target)}

    tool_lock = capture / ".vendor" / "schema-tools.lock.json"
    tika_manifest = capture / ".tika" / "manifest.json"
    _write_json(tool_lock, {"tools": []})
    _write_json(tika_manifest, {"tika": "fixture"})
    manifest = {
        "manifest_version": worker.MANIFEST_VERSION,
        "tool_lock_sha256": _sha(tool_lock),
        "tika_manifest_sha256": _sha(tika_manifest),
        "totals": totals
        or {
            "tika_occurrences": len(streams["structures.jsonl.zst"]),
            "physical_occurrences": 2,
            "unique_parse_tasks": 0,
            "executed_tasks": 0,
            "schema_signatures": len(streams["schemas.jsonl.zst"]),
            "failures": len(streams["failures.jsonl.zst"]),
        },
        "artifacts": artifacts,
    }
    _write_json(capture / ".schemas" / "manifest.json", manifest)
    mirror = capture / ".receipts" / "schema-build.json"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(capture / ".schemas" / "manifest.json", mirror)


def _write_xlsx(path: Path) -> None:
    ns_main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ns_rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    ns_pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    ET.register_namespace("", ns_main)
    ET.register_namespace("r", ns_rel)

    root = path.parent
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
              <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
              <Default Extension="xml" ContentType="application/xml"/>
              <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
              <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
              <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
            </Types>
            """.strip(),
        )
        zf.writestr(
            "_rels/.rels",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <Relationships xmlns="{ns_pkg}">
              <Relationship Id="rId1" Type="{ns_rel}/officeDocument" Target="xl/workbook.xml"/>
            </Relationships>
            """.strip(),
        )
        zf.writestr(
            "xl/workbook.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <workbook xmlns="{ns_main}" xmlns:r="{ns_rel}">
              <sheets>
                <sheet name="Sheet1" sheetId="1" r:id="rId1"/>
              </sheets>
            </workbook>
            """.strip(),
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <Relationships xmlns="{ns_pkg}">
              <Relationship Id="rId1" Type="{ns_rel}/worksheet" Target="worksheets/sheet1.xml"/>
              <Relationship Id="rId2" Type="{ns_rel}/sharedStrings" Target="sharedStrings.xml"/>
            </Relationships>
            """.strip(),
        )
        zf.writestr(
            "xl/sharedStrings.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <sst xmlns="{ns_main}" count="1" uniqueCount="1">
              <si><t>Alpha</t></si>
            </sst>
            """.strip(),
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <worksheet xmlns="{ns_main}" xmlns:r="{ns_rel}">
              <sheetData>
                <row r="1">
                  <c r="A1" t="s"><v>0</v></c>
                  <c r="B1" t="inlineStr"><is><t>Beta</t></is></c>
                  <c r="C1" s="7"><f>1+1</f><v>2</v></c>
                  <c r="D1"/>
                  <c r="E1"><v>3.5</v></c>
                </row>
              </sheetData>
            </worksheet>
            """.strip(),
        )


def _fixture(tmp_path: Path) -> tuple[Path, dict[str, list[str]]]:
    capture = tmp_path / "capture"
    capture.mkdir(parents=True)
    source_a = capture / "root-a.csv"
    source_b = capture / "root-b.zip"
    source_a.write_bytes(b"name,value\na,1\n")
    source_b.write_bytes(b"fixture archive bytes")
    verification = {
        "receipt_version": "schema-forest.source-verification.v1",
        "files": {
            source_a.name: {
                "expected_sha256": _sha(source_a),
                "verified_sha256": _sha(source_a),
                "size_bytes": source_a.stat().st_size,
                "mtime_ns": source_a.stat().st_mtime_ns,
            },
            source_b.name: {
                "expected_sha256": _sha(source_b),
                "verified_sha256": _sha(source_b),
                "size_bytes": source_b.stat().st_size,
                "mtime_ns": source_b.stat().st_mtime_ns,
            },
        },
    }
    _write_json(capture / ".receipts" / "schema-source-verification.json", verification)

    structures = [
        _occurrence(
            content_sha=_sha(source_a),
            resource_name="root-a.csv",
            route="csv",
            signature=SIG_A,
            source_capture="root-a.csv",
            status="parsed",
            depth=0,
        ),
        _occurrence(
            content_sha="3" * 64,
            resource_name="member-a.csv",
            route="nested",
            signature=SIG_A,
            source_capture="root-b.zip",
            status="covered_by_parent",
            depth=1,
        ),
        _occurrence(
            content_sha="4" * 64,
            resource_name="member-b.csv",
            route="nested",
            signature=SIG_A,
            source_capture="root-b.zip",
            status="covered_by_parent",
            depth=1,
        ),
        _occurrence(
            content_sha=_sha(source_b),
            resource_name=None,
            route="pdf",
            signature=SIG_B,
            source_capture="root-b.zip",
            status="vendor_error",
            depth=0,
            tika_error="boom",
        ),
    ]
    streams = {
        "structures.jsonl.zst": [_json_line(row) for row in structures],
        "schemas.jsonl.zst": [
            _json_line({"schema": {"kind": "tabular.csv"}, "schema_signature": SIG_A}),
            _json_line({"schema": {"kind": "pdf"}, "schema_signature": SIG_B}),
        ],
        "families.jsonl.zst": [
            _json_line(
                {
                    "occurrence_ids": [OCCURRENCE_ID, OCCURRENCE_ID, OCCURRENCE_ID],
                    "schema_signature": SIG_A,
                }
            ),
            _json_line(
                {"occurrence_ids": [OCCURRENCE_ID], "schema_signature": SIG_B}
            ),
        ],
        "failures.jsonl.zst": [
            streams_line for streams_line in (
                _json_line(structures[1]),
                _json_line(structures[3]),
            )
        ],
    }
    _seal(capture, streams)
    return capture, streams


def _xlsx_fixture(tmp_path: Path) -> tuple[Path, dict[str, list[str]], Path]:
    capture, streams = _fixture(tmp_path)
    workbook = capture / "workbook.xlsx"
    _write_xlsx(workbook)
    xlsx_occurrence = {
        "content_sha256": _sha(workbook),
        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "embedded_depth": 0,
        "occurrence_id": XLSX_OCCURRENCE_ID,
        "resource_name": workbook.name,
        "route": "xlsx",
        "schema_signature": SIG_C,
        "source_capture": workbook.name,
        "status": "parsed",
        "tika_error": None,
    }
    row = _json_line(xlsx_occurrence)
    streams["structures.jsonl.zst"].append(row)
    streams["schemas.jsonl.zst"].append(
        _json_line({"schema": {"kind": "xlsx"}, "schema_signature": SIG_C})
    )
    streams["families.jsonl.zst"].append(
        _json_line({"occurrence_ids": [XLSX_OCCURRENCE_ID], "schema_signature": SIG_C})
    )
    _write_json(
        capture / ".receipts" / "schema-source-verification.json",
        {
            "receipt_version": "schema-forest.source-verification.v1",
            "files": {
                "root-a.csv": {
                    "expected_sha256": _sha(capture / "root-a.csv"),
                    "verified_sha256": _sha(capture / "root-a.csv"),
                    "size_bytes": (capture / "root-a.csv").stat().st_size,
                    "mtime_ns": (capture / "root-a.csv").stat().st_mtime_ns,
                },
                "root-b.zip": {
                    "expected_sha256": _sha(capture / "root-b.zip"),
                    "verified_sha256": _sha(capture / "root-b.zip"),
                    "size_bytes": (capture / "root-b.zip").stat().st_size,
                    "mtime_ns": (capture / "root-b.zip").stat().st_mtime_ns,
                },
                "workbook.xlsx": {
                    "expected_sha256": _sha(workbook),
                    "verified_sha256": _sha(workbook),
                    "size_bytes": workbook.stat().st_size,
                    "mtime_ns": workbook.stat().st_mtime_ns,
                },
            },
        },
    )
    _seal(
        capture,
        streams,
        totals={
            "tika_occurrences": len(streams["structures.jsonl.zst"]),
            "physical_occurrences": 3,
            "unique_parse_tasks": 0,
            "executed_tasks": 0,
            "schema_signatures": len(streams["schemas.jsonl.zst"]),
            "failures": len(streams["failures.jsonl.zst"]),
        },
    )
    return capture, streams, workbook


def test_build_preserves_exact_records_and_minimal_surface(tmp_path: Path) -> None:
    capture, streams = _fixture(tmp_path)
    target = capture / ".derived" / "fixture.db"
    receipt_path = capture / ".receipts" / "fixture-build.json"

    first = worker.build_cell(capture, target, receipt_path=receipt_path)
    first_logical = first["ordered_source_digests"]
    second = worker.build_cell(capture, target, receipt_path=receipt_path)

    assert first_logical == second["ordered_source_digests"]
    assert first["counts"] == {
        "asset_editions": 1,
        "asset_occurrences": 4,
        "asset_schemas": 2,
        "asset_schema_families": 2,
        "asset_failures": 2,
        "asset_native_tables": 0,
    }
    assert json.loads(receipt_path.read_text()) == second
    verified = worker.verify_cell(target)
    assert verified["distinct_occurrence_ids"] == 1
    assert verified["physical_occurrences"] == 2
    assert verified["verified_roots"] == 2

    with sqlite3.connect(target) as db:
        provider_tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name LIKE 'asset_%'"
            )
        }
        assert provider_tables == {
            "asset_editions",
            "asset_occurrences",
            "asset_schemas",
            "asset_schema_families",
            "asset_failures",
            "asset_native_tables",
        }
        assert db.execute(
            "SELECT source_json FROM asset_occurrences WHERE source_ordinal=2"
        ).fetchone()[0] == streams["structures.jsonl.zst"][1]
        assert db.execute(
            "SELECT count(*) FROM asset_occurrences WHERE occurrence_id=?",
            (OCCURRENCE_ID,),
        ).fetchone()[0] == 4
        assert db.execute(
            "SELECT count(*) FROM asset_failures WHERE occurrence_id=?",
            (OCCURRENCE_ID,),
        ).fetchone()[0] == 2
        assert db.execute(
            "SELECT count(*) FROM asset_schema_families f, "
            "json_each(f.source_json,'$.occurrence_ids')"
        ).fetchone()[0] == 4
        assert db.execute(
            "SELECT resource_name,tika_error FROM asset_occurrences "
            "WHERE source_ordinal=4"
        ).fetchone() == (None, "boom")


def test_csv_schema_materializes_complete_rows(tmp_path: Path) -> None:
    capture, streams = _fixture(tmp_path)
    schema = {
        "kind": "tabular.csv",
        "header": [
            {"name": "name", "ordinal": 0},
            {"name": "value", "ordinal": 1},
        ],
    }
    streams["schemas.jsonl.zst"][0] = _json_line(
        {"schema": schema, "schema_signature": SIG_A}
    )
    _seal(capture, streams)
    target = capture / ".derived" / "fixture.db"

    receipt = worker.build_cell(capture, target)

    assert receipt["counts"]["asset_native_tables"] == 1
    assert receipt["cell_sha256"] == "sha256:" + _sha(target)
    with sqlite3.connect(target) as db:
        native = db.execute(
            "SELECT table_name,columns_json,source_rows,sql_rows,"
            "source_digest,sql_digest FROM asset_native_tables"
        ).fetchone()
        assert native is not None
        table_name, columns_json, source_rows, sql_rows, source_digest, sql_digest = native
        assert source_rows == sql_rows == 2
        assert source_digest == sql_digest
        assert json.loads(columns_json) == [
            {
                "ordinal": 0,
                "source_name": "name",
                "sql_name": "name",
                "sqlite_type": "TEXT",
            },
            {
                "ordinal": 1,
                "source_name": "value",
                "sql_name": "value",
                "sqlite_type": "TEXT",
            },
        ]
        rows = db.execute(
            f'SELECT row_ordinal,name,value FROM "{table_name}" ORDER BY row_ordinal'
        ).fetchall()
        assert rows == [(0, "name", "value"), (1, "a", "1")]
        db.execute(f'UPDATE "{table_name}" SET value="2" WHERE row_ordinal=1')
        db.commit()
    with pytest.raises(worker.CaptureError, match="native table digest differs"):
        worker.verify_cell(target)


def test_csv_verification_requires_expected_table_and_provenance(tmp_path: Path) -> None:
    capture, streams = _fixture(tmp_path)
    streams["schemas.jsonl.zst"][0] = _json_line(
        {
            "schema": {
                "kind": "tabular.csv",
                "header": [
                    {"name": "name", "ordinal": 0},
                    {"name": "value", "ordinal": 1},
                ],
            },
            "schema_signature": SIG_A,
        }
    )
    _seal(capture, streams)
    target = capture / ".derived" / "fixture.db"
    worker.build_cell(capture, target)

    with sqlite3.connect(target) as db:
        table_name = db.execute(
            "SELECT table_name FROM asset_native_tables"
        ).fetchone()[0]
        db.execute(f'UPDATE "{table_name}" SET occurrence_id=? WHERE row_ordinal=1',
                   ("2" * 64,))
        db.commit()
    with pytest.raises(
        worker.CaptureError, match="native table occurrence provenance differs"
    ):
        worker.verify_cell(target)

    worker.build_cell(capture, target)
    with sqlite3.connect(target) as db:
        table_name = db.execute(
            "SELECT table_name FROM asset_native_tables"
        ).fetchone()[0]
        db.execute(f'DROP TABLE "{table_name}"')
        db.execute("DELETE FROM asset_native_tables")
        db.commit()
    with pytest.raises(
        worker.CaptureError, match="native table set differs from source schemas"
    ):
        worker.verify_cell(target)


def test_xlsx_sheet_cells_materialize_exact_records(tmp_path: Path) -> None:
    capture, streams, workbook = _xlsx_fixture(tmp_path)
    target = capture / ".derived" / "fixture.db"

    receipt = worker.build_cell(capture, target)

    assert receipt["counts"]["asset_native_tables"] == 1
    assert receipt["cell_sha256"] == "sha256:" + _sha(target)
    with sqlite3.connect(target) as db:
        native = db.execute(
            "SELECT table_name,source_rows,sql_rows,source_digest,sql_digest,columns_json "
            "FROM asset_native_tables WHERE component='xlsx.cells'"
        ).fetchone()
        assert native is not None
        table_name, source_rows, sql_rows, source_digest, sql_digest, columns_json = native
        assert source_rows == sql_rows == 5
        assert source_digest == sql_digest
        assert json.loads(columns_json) == [
            {"sql_name": "sheet_ordinal", "sqlite_type": "INTEGER"},
            {"sql_name": "sheet_name", "sqlite_type": "TEXT"},
            {"sql_name": "sheet_state", "sqlite_type": "TEXT"},
            {"sql_name": "sheet_rel_id", "sqlite_type": "TEXT"},
            {"sql_name": "cell_ref", "sqlite_type": "TEXT"},
            {"sql_name": "row_index", "sqlite_type": "INTEGER"},
            {"sql_name": "column_index", "sqlite_type": "INTEGER"},
            {"sql_name": "data_type", "sqlite_type": "TEXT"},
            {"sql_name": "style_id", "sqlite_type": "TEXT"},
            {"sql_name": "raw_value", "sqlite_type": "TEXT"},
            {"sql_name": "formula", "sqlite_type": "TEXT"},
            {"sql_name": "inline_text", "sqlite_type": "TEXT"},
            {"sql_name": "shared_text", "sqlite_type": "TEXT"},
        ]
        rows = db.execute(
            f'SELECT cell_ordinal,cell_ref,data_type,raw_value,formula,inline_text,shared_text '
            f'FROM "{table_name}" ORDER BY cell_ordinal'
        ).fetchall()
        assert rows == [
            (1, "A1", "s", "0", None, None, "Alpha"),
            (2, "B1", "inlineStr", None, None, "Beta", None),
            (3, "C1", None, "2", "1+1", None, None),
            (4, "D1", None, None, None, None, None),
            (5, "E1", None, "3.5", None, None, None),
        ]
        assert db.execute(
            f"SELECT count(*) FROM \"{table_name}\" WHERE cell_ref='D1' AND raw_value IS NULL"
        ).fetchone()[0] == 1
        assert db.execute(
            f"SELECT count(*) FROM \"{table_name}\" WHERE cell_ref='F1'"
        ).fetchone()[0] == 0
    assert worker.verify_cell(target)["asset_native_tables"] == 1


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("bad_count", "population differs"),
        ("duplicate_schema", "duplicate schema signature"),
        ("dangling_schema", "FOREIGN KEY constraint failed"),
        ("malformed_json", "invalid UTF-8 JSON"),
        ("path_escape", "escapes root"),
        ("root_evidence_mismatch", "recovery evidence differs"),
    ],
)
def test_invalid_capture_fails_closed(
    tmp_path: Path, mutation: str, match: str
) -> None:
    capture, streams = _fixture(tmp_path)
    target = capture / ".derived" / "fixture.db"
    worker.build_cell(capture, target)
    last_good = target.read_bytes()

    if mutation == "bad_count":
        manifest = json.loads((capture / ".schemas" / "manifest.json").read_text())
        manifest["totals"]["tika_occurrences"] = 5
        _write_json(capture / ".schemas" / "manifest.json", manifest)
        shutil.copyfile(
            capture / ".schemas" / "manifest.json",
            capture / ".receipts" / "schema-build.json",
        )
    elif mutation == "duplicate_schema":
        streams["schemas.jsonl.zst"].append(streams["schemas.jsonl.zst"][0])
        _seal(capture, streams)
    elif mutation == "dangling_schema":
        row = json.loads(streams["structures.jsonl.zst"][0])
        row["schema_signature"] = "c" * 64
        streams["structures.jsonl.zst"][0] = _json_line(row)
        _seal(capture, streams)
    elif mutation == "malformed_json":
        streams["structures.jsonl.zst"][0] = "{not-json}"
        _seal(capture, streams)
    elif mutation == "path_escape":
        receipt_path = capture / ".receipts" / "schema-source-verification.json"
        receipt = json.loads(receipt_path.read_text())
        descriptor = receipt["files"].pop("root-a.csv")
        receipt["files"]["../outside.csv"] = descriptor
        _write_json(receipt_path, receipt)
    else:
        row = json.loads(streams["structures.jsonl.zst"][0])
        row["source_capture"] = "wrong-root.csv"
        streams["structures.jsonl.zst"][0] = _json_line(row)
        _seal(capture, streams)

    with pytest.raises(worker.CaptureError, match=match):
        worker.build_cell(capture, target)
    assert target.read_bytes() == last_good


def test_tampered_declared_stream_and_source_preserve_last_good(tmp_path: Path) -> None:
    capture, _ = _fixture(tmp_path)
    target = capture / ".derived" / "fixture.db"
    worker.build_cell(capture, target)
    last_good = target.read_bytes()

    stream = capture / ".schemas" / "structures.jsonl.zst"
    stream.write_bytes(stream.read_bytes() + b"tamper")
    with pytest.raises(worker.CaptureError, match="size mismatch"):
        worker.build_cell(capture, target)
    assert target.read_bytes() == last_good

    capture, _ = _fixture(tmp_path / "second")
    target = capture / ".derived" / "fixture.db"
    worker.build_cell(capture, target)
    last_good = target.read_bytes()
    (capture / "root-a.csv").write_bytes(b"tampered")
    with pytest.raises(worker.CaptureError, match="size mismatch"):
        worker.build_cell(capture, target)
    assert target.read_bytes() == last_good


def test_registration_is_static_and_external(tmp_path: Path, monkeypatch) -> None:
    capture, _ = _fixture(tmp_path)
    target = capture / ".derived" / "fixture.db"
    worker.build_cell(capture, target)
    observed = {}

    def fake_register_cell(**kwargs):
        observed.update(kwargs)
        return kwargs["name"]

    monkeypatch.setattr("flex.registry.register_cell", fake_register_cell)
    assert worker.register_cell_path("fixture", target) == "fixture"
    assert observed["path"] == target.resolve()
    assert observed["cell_type"] == "schema_forest"
    assert observed["lifecycle"] == "static"
    assert observed["allow_external"] is True
