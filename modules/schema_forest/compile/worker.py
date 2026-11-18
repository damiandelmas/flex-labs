#!/usr/bin/env python3
"""Sealed schema-forest.v1 capture -> one static native Flex SQLite cell."""

from __future__ import annotations

import argparse
import csv
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from typing import Any, Iterator
import zipfile
import posixpath
import xml.etree.ElementTree as ET

from flex.envelope import install_cell_core, validate_cell_core


FORMAT = "schema-forest-native-flex/v1"
BUILD_RECEIPT_SCHEMA = "schema-forest-native-flex-build/v1"
MANIFEST_VERSION = "schema-forest.v1"
MODULE_ROOT = Path(__file__).resolve().parent.parent
ORIENT_PATH = MODULE_ROOT / "stock" / "presets" / "orient.sql"
INSTRUCTIONS_PATH = MODULE_ROOT / "stock" / "instructions.md"
TRANSFORMATION_PATHS = (
    "install.py",
    "compile/worker.py",
    "stock/presets/orient.sql",
    "stock/instructions.md",
)
STREAMS = {
    "structures.jsonl.zst": "asset_occurrences",
    "schemas.jsonl.zst": "asset_schemas",
    "families.jsonl.zst": "asset_schema_families",
    "failures.jsonl.zst": "asset_failures",
}
FORBIDDEN_RELATIONS = frozenset(
    {
        "_raw_sources",
        "_raw_chunks",
        "_edges_tree",
        "_edges_reference",
        "chunks",
        "chunks_fts",
        "asset_parse_tasks",
        "asset_artifacts",
    }
)


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE asset_editions(
  edition_id TEXT PRIMARY KEY,
  capture_root TEXT NOT NULL,
  manifest_version TEXT NOT NULL,
  manifest_path TEXT NOT NULL,
  manifest_sha256 TEXT NOT NULL,
  manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
  verification_path TEXT NOT NULL,
  verification_sha256 TEXT NOT NULL,
  verification_json TEXT NOT NULL CHECK(json_valid(verification_json)),
  tika_occurrences INTEGER NOT NULL,
  physical_occurrences INTEGER NOT NULL,
  unique_parse_tasks INTEGER NOT NULL,
  executed_tasks INTEGER NOT NULL,
  schema_signatures INTEGER NOT NULL,
  failures INTEGER NOT NULL,
  tool_lock_sha256 TEXT NOT NULL,
  tika_manifest_sha256 TEXT NOT NULL,
  transformation_sha256 TEXT NOT NULL
);
CREATE TABLE asset_schemas(
  schema_signature TEXT PRIMARY KEY CHECK(length(schema_signature)=64),
  source_ordinal INTEGER NOT NULL UNIQUE CHECK(source_ordinal>=1),
  kind TEXT NOT NULL,
  source_json TEXT NOT NULL CHECK(json_valid(source_json))
);
CREATE TABLE asset_schema_families(
  schema_signature TEXT PRIMARY KEY REFERENCES asset_schemas(schema_signature),
  source_ordinal INTEGER NOT NULL UNIQUE CHECK(source_ordinal>=1),
  source_json TEXT NOT NULL CHECK(json_valid(source_json))
);
CREATE TABLE asset_occurrences(
  source_ordinal INTEGER PRIMARY KEY CHECK(source_ordinal>=1),
  edition_id TEXT NOT NULL REFERENCES asset_editions(edition_id),
  occurrence_id TEXT NOT NULL CHECK(length(occurrence_id)=64),
  content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
  content_type TEXT NOT NULL,
  embedded_depth INTEGER NOT NULL
    CHECK(embedded_depth>=0 AND typeof(embedded_depth)='integer'),
  resource_name TEXT,
  route TEXT NOT NULL,
  schema_signature TEXT NOT NULL REFERENCES asset_schemas(schema_signature),
  source_capture TEXT NOT NULL,
  status TEXT NOT NULL,
  tika_error TEXT,
  source_json TEXT NOT NULL CHECK(json_valid(source_json))
);
CREATE TABLE asset_failures(
  source_ordinal INTEGER PRIMARY KEY CHECK(source_ordinal>=1),
  edition_id TEXT NOT NULL REFERENCES asset_editions(edition_id),
  occurrence_id TEXT NOT NULL CHECK(length(occurrence_id)=64),
  route TEXT NOT NULL,
  schema_signature TEXT NOT NULL REFERENCES asset_schemas(schema_signature),
  status TEXT NOT NULL,
  tika_error TEXT,
  source_json TEXT NOT NULL CHECK(json_valid(source_json))
);
CREATE TABLE asset_native_tables(
  schema_signature TEXT NOT NULL REFERENCES asset_schemas(schema_signature),
  component TEXT NOT NULL,
  table_name TEXT NOT NULL UNIQUE,
  columns_json TEXT NOT NULL CHECK(json_valid(columns_json)),
  source_rows INTEGER NOT NULL CHECK(source_rows>=0),
  sql_rows INTEGER NOT NULL CHECK(sql_rows>=0),
  source_digest TEXT NOT NULL CHECK(length(source_digest)=64),
  sql_digest TEXT NOT NULL CHECK(length(sql_digest)=64),
  PRIMARY KEY(schema_signature,component)
);
CREATE INDEX asset_occurrences_occurrence_id_idx
  ON asset_occurrences(occurrence_id);
CREATE INDEX asset_occurrences_content_sha_idx
  ON asset_occurrences(content_sha256);
CREATE INDEX asset_occurrences_schema_idx
  ON asset_occurrences(schema_signature);
CREATE INDEX asset_occurrences_route_status_idx
  ON asset_occurrences(route,status);
CREATE INDEX asset_occurrences_source_idx
  ON asset_occurrences(source_capture);
CREATE INDEX asset_occurrences_resource_idx
  ON asset_occurrences(resource_name);
CREATE INDEX asset_occurrences_content_type_idx
  ON asset_occurrences(content_type);
CREATE INDEX asset_occurrences_depth_idx
  ON asset_occurrences(embedded_depth);
CREATE INDEX asset_failures_occurrence_idx
  ON asset_failures(occurrence_id);
"""


FAMILY_BAG_MISMATCH_SQL = """
WITH occurrence_bag AS (
  SELECT schema_signature, occurrence_id, count(*) AS n
  FROM asset_occurrences
  GROUP BY schema_signature, occurrence_id
), family_bag AS (
  SELECT f.schema_signature, j.value AS occurrence_id, count(*) AS n
  FROM asset_schema_families AS f,
       json_each(f.source_json, '$.occurrence_ids') AS j
  GROUP BY f.schema_signature, j.value
), mismatch AS (
  SELECT * FROM occurrence_bag EXCEPT SELECT * FROM family_bag
  UNION ALL
  SELECT * FROM family_bag EXCEPT SELECT * FROM occurrence_bag
)
SELECT count(*) FROM mismatch
"""


class CaptureError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _digest_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_uri(value: str) -> str:
    return value if value.startswith("sha256:") else "sha256:" + value


def _transformation_hash() -> str:
    file_hashes: dict[str, str] = {}
    for relative in TRANSFORMATION_PATHS:
        path = MODULE_ROOT / relative
        if not path.is_file():
            raise CaptureError(f"missing schema-forest module source: {path}")
        file_hashes[relative] = _sha256_uri(_digest_file(path))
    return _sha256_uri(_digest_bytes(_canonical(file_hashes).encode("utf-8")))


def _stable_id(prefix: str, *values: Any) -> str:
    return prefix + "_" + _digest_bytes(_canonical(values).encode("utf-8"))


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(_canonical(value) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _inside(root: Path, path: Path) -> Path:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CaptureError(f"capture path escapes root: {path}") from exc
    return path


def _capture_file(root: Path, relative: str, *, required: bool = True) -> Path:
    relative_path = Path(relative)
    if relative_path.is_absolute():
        raise CaptureError(f"capture path must be relative: {relative}")
    unresolved = root / relative_path
    if unresolved.is_symlink():
        raise CaptureError(f"capture evidence must not be a symlink: {relative}")
    resolved = _inside(root, unresolved.resolve())
    if required and not resolved.is_file():
        raise CaptureError(f"capture is missing: {relative}")
    return resolved


def _capture_paths(capture: Path) -> dict[str, Any]:
    root = Path(capture).resolve()
    if not root.is_dir():
        raise CaptureError(f"capture directory does not exist: {root}")
    paths: dict[str, Any] = {
        "root": root,
        "manifest": _capture_file(root, ".schemas/manifest.json"),
        "verification": _capture_file(
            root, ".receipts/schema-source-verification.json"
        ),
        "tool_lock": _capture_file(root, ".vendor/schema-tools.lock.json"),
        "tika_manifest": _capture_file(root, ".tika/manifest.json"),
        "build_mirror": _capture_file(
            root, ".receipts/schema-build.json", required=False
        ),
        "streams": {},
    }
    for filename in STREAMS:
        paths["streams"][filename] = _capture_file(
            root, f".schemas/{filename}"
        )
    return paths


def _json_object(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise CaptureError(f"JSON root must be an object: {path}")
    return raw, value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CaptureError(f"{label} must be an integer")
    return value


def _hex64(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise CaptureError(f"{label} must be a 64-character hex string")
    try:
        int(value, 16)
    except ValueError as exc:
        raise CaptureError(f"{label} must be a 64-character hex string") from exc
    if value.lower() != value:
        raise CaptureError(f"{label} must use lowercase hex")
    return value


def _required_text(row: dict[str, Any], field: str, label: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise CaptureError(f"{label}.{field} must be non-empty text")
    return value


def _verify_capture(capture: Path) -> dict[str, Any]:
    paths = _capture_paths(capture)
    manifest_raw, manifest = _json_object(paths["manifest"])
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise CaptureError(
            f"unsupported schema-forest manifest: {manifest.get('manifest_version')!r}"
        )
    artifacts = manifest.get("artifacts")
    totals = manifest.get("totals")
    if not isinstance(artifacts, dict) or not isinstance(totals, dict):
        raise CaptureError("schema-forest manifest lacks artifacts or totals")
    if not set(STREAMS).issubset(artifacts):
        raise CaptureError("schema-forest manifest lacks required streams")
    for filename, path in paths["streams"].items():
        descriptor = artifacts.get(filename)
        if not isinstance(descriptor, dict):
            raise CaptureError(f"manifest artifact is invalid: {filename}")
        expected_bytes = _integer(descriptor.get("bytes"), f"{filename}.bytes")
        expected_sha = _hex64(descriptor.get("sha256"), f"{filename}.sha256")
        if path.stat().st_size != expected_bytes:
            raise CaptureError(f"manifest artifact size mismatch: {filename}")
        if _digest_file(path) != expected_sha:
            raise CaptureError(f"manifest artifact hash mismatch: {filename}")
    for field in (
        "tika_occurrences",
        "physical_occurrences",
        "unique_parse_tasks",
        "executed_tasks",
        "schema_signatures",
        "failures",
    ):
        if _integer(totals.get(field), f"totals.{field}") < 0:
            raise CaptureError(f"totals.{field} must be non-negative")

    tool_lock_sha = _hex64(manifest.get("tool_lock_sha256"), "tool_lock_sha256")
    tika_manifest_sha = _hex64(
        manifest.get("tika_manifest_sha256"), "tika_manifest_sha256"
    )
    if _digest_file(paths["tool_lock"]) != tool_lock_sha:
        raise CaptureError("schema tool lock hash mismatch")
    if _digest_file(paths["tika_manifest"]) != tika_manifest_sha:
        raise CaptureError("Tika manifest hash mismatch")
    mirror = paths["build_mirror"]
    if mirror.is_file() and mirror.read_bytes() != manifest_raw:
        raise CaptureError("schema-build receipt does not mirror the manifest")

    verification_raw, verification = _json_object(paths["verification"])
    files = verification.get("files")
    if not isinstance(files, dict) or not files:
        raise CaptureError("source-verification receipt has no files")
    for relative, descriptor in sorted(files.items()):
        if not isinstance(relative, str) or not isinstance(descriptor, dict):
            raise CaptureError("source-verification entry is invalid")
        source = _capture_file(paths["root"], relative)
        expected = _hex64(
            descriptor.get("expected_sha256"), f"{relative}.expected_sha256"
        )
        verified = _hex64(
            descriptor.get("verified_sha256"), f"{relative}.verified_sha256"
        )
        if expected != verified:
            raise CaptureError(f"source-verification hashes disagree: {relative}")
        size = _integer(descriptor.get("size_bytes"), f"{relative}.size_bytes")
        if source.stat().st_size != size:
            raise CaptureError(f"source file size mismatch: {relative}")
        if _digest_file(source) != verified:
            raise CaptureError(f"source file hash mismatch: {relative}")

    transformation = _transformation_hash()
    manifest_sha = _digest_bytes(manifest_raw)
    verification_sha = _digest_bytes(verification_raw)
    return {
        "paths": paths,
        "manifest": manifest,
        "manifest_raw": manifest_raw,
        "manifest_sha256": manifest_sha,
        "verification": verification,
        "verification_raw": verification_raw,
        "verification_sha256": verification_sha,
        "transformation_sha256": transformation,
        "edition_id": _stable_id("edition", manifest_sha, verification_sha),
    }


def _stream_jsonl(path: Path) -> Iterator[tuple[int, str, dict[str, Any]]]:
    executable = shutil.which("zstd")
    if executable is None:
        raise CaptureError("zstd is required to read schema-forest streams")
    process = subprocess.Popen(
        [executable, "-dc", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        for ordinal, raw in enumerate(process.stdout, 1):
            if not raw.endswith(b"\n") or raw.endswith(b"\r\n"):
                raise CaptureError(f"{path}:{ordinal}: expected one LF terminator")
            record = raw[:-1]
            try:
                text = record.decode("utf-8")
                value = json.loads(text)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CaptureError(f"{path}:{ordinal}: invalid UTF-8 JSON") from exc
            if not isinstance(value, dict):
                raise CaptureError(f"{path}:{ordinal}: record must be an object")
            yield ordinal, text, value
        stderr = process.stderr.read()
        return_code = process.wait()
        if return_code:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise CaptureError(f"zstd failed for {path}: {detail or return_code}")
    finally:
        process.stdout.close()
        process.stderr.close()
        if process.poll() is None:
            process.terminate()
            process.wait()


def _validate_occurrence(row: dict[str, Any], label: str) -> None:
    _hex64(row.get("occurrence_id"), f"{label}.occurrence_id")
    _hex64(row.get("content_sha256"), f"{label}.content_sha256")
    _hex64(row.get("schema_signature"), f"{label}.schema_signature")
    for field in ("content_type", "route", "source_capture", "status"):
        _required_text(row, field, label)
    depth = row.get("embedded_depth")
    if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0:
        raise CaptureError(f"{label}.embedded_depth must be a non-negative integer")
    for field in ("resource_name", "tika_error"):
        value = row.get(field)
        if value is not None and not isinstance(value, str):
            raise CaptureError(f"{label}.{field} must be text or null")


def _ordered_digest_step(digest, ordinal: int, source_json: str) -> None:
    digest.update(str(ordinal).encode("ascii"))
    digest.update(b"\0")
    digest.update(source_json.encode("utf-8"))
    digest.update(b"\n")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _column_name(value: str, ordinal: int, used: set[str]) -> str:
    candidate = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    if not candidate:
        candidate = f"field_{ordinal + 1}"
    if candidate[0].isdigit():
        candidate = "field_" + candidate
    base = candidate
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _csv_header(schema_json: str) -> list[dict[str, Any]] | None:
    value = json.loads(schema_json)
    header = value.get("schema", {}).get("header")
    if not isinstance(header, list) or not header:
        return None
    result: list[dict[str, Any]] = []
    for expected, field in enumerate(header):
        if not isinstance(field, dict):
            return None
        name = field.get("name")
        ordinal = field.get("ordinal")
        if not isinstance(name, str) or ordinal != expected:
            return None
        result.append({"name": name, "ordinal": ordinal})
    return result


def _csv_bytes(root: Path, source_capture: str, resource_name: str) -> bytes:
    source = _capture_file(root, source_capture)
    if resource_name == source_capture:
        return source.read_bytes()
    if not zipfile.is_zipfile(source):
        raise CaptureError(
            f"CSV occurrence is not directly recoverable from source: {resource_name}"
        )
    with zipfile.ZipFile(source) as archive:
        try:
            return archive.read(resource_name)
        except KeyError as exc:
            raise CaptureError(
                f"CSV member is missing from {source_capture}: {resource_name}"
            ) from exc


def _csv_rows(payload: bytes, width: int, label: str) -> list[list[str]]:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CaptureError(f"CSV is not UTF-8: {label}") from exc
    rows = list(csv.reader(io.StringIO(text, newline="")))
    for ordinal, row in enumerate(rows):
        if len(row) != width:
            raise CaptureError(
                f"CSV row width differs from schema: {label}:{ordinal}: "
                f"expected {width}, got {len(row)}"
            )
    return rows


OOXML_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
OOXML_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PACKAGE_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _rows_digest(rows: Iterator[tuple[int, str, list[str]]]) -> str:
    digest = sha256()
    for ordinal, occurrence_id, row in rows:
        _ordered_digest_step(
            digest,
            ordinal,
            _canonical({"occurrence_id": occurrence_id, "values": row}),
        )
    return digest.hexdigest()


def _records_digest(rows: Iterator[tuple[int, str, dict[str, Any]]]) -> str:
    digest = sha256()
    for ordinal, occurrence_id, record in rows:
        _ordered_digest_step(
            digest,
            ordinal,
            _canonical({"occurrence_id": occurrence_id, "record": record}),
        )
    return digest.hexdigest()


def _expected_csv_native_tables(db: sqlite3.Connection) -> dict[tuple[str, str], str]:
    expected: dict[tuple[str, str], str] = {}
    for signature, source_json in db.execute(
        "SELECT schema_signature,source_json FROM asset_schemas "
        "WHERE kind='tabular.csv'"
    ):
        if _csv_header(str(source_json)) is None:
            continue
        occurrence_count = int(
            db.execute(
                "SELECT count(*) FROM asset_occurrences "
                "WHERE schema_signature=? AND route='csv'",
                (signature,),
            ).fetchone()[0]
        )
        if occurrence_count:
            expected[(str(signature), "csv.rows")] = f"sf_csv_{str(signature)[:16]}"
    return expected


def _expected_native_tables(db: sqlite3.Connection) -> dict[tuple[str, str], str]:
    expected = _expected_csv_native_tables(db)
    for signature in db.execute(
        "SELECT DISTINCT s.schema_signature "
        "FROM asset_schemas AS s "
        "JOIN asset_occurrences AS o USING(schema_signature) "
        "WHERE s.kind='xlsx' AND o.route='xlsx'"
    ):
        value = str(signature[0])
        expected[(value, "xlsx.cells")] = f"sf_xlsx_{value}_cells"
    return expected


def _materialize_csv_tables(db: sqlite3.Connection, contract: dict[str, Any]) -> None:
    schemas = {
        row[0]: (row[1], _csv_header(row[2]))
        for row in db.execute(
            "SELECT schema_signature,kind,source_json FROM asset_schemas "
            "WHERE kind='tabular.csv'"
        )
    }
    occurrences = db.execute(
        "SELECT source_ordinal,occurrence_id,schema_signature,source_capture,"
        "resource_name FROM asset_occurrences WHERE route='csv' "
        "ORDER BY source_ordinal"
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row | tuple[Any, ...]]] = {}
    for occurrence in occurrences:
        signature = occurrence[2]
        schema = schemas.get(signature)
        if schema is None or schema[1] is None:
            continue
        grouped.setdefault(signature, []).append(occurrence)

    for signature, members in sorted(grouped.items()):
        header = schemas[signature][1]
        assert header is not None
        used = {"occurrence_source_ordinal", "occurrence_id", "row_ordinal"}
        columns = [
            {
                "ordinal": field["ordinal"],
                "source_name": field["name"],
                "sql_name": _column_name(field["name"], field["ordinal"], used),
                "sqlite_type": "TEXT",
            }
            for field in header
        ]
        table_name = f"sf_csv_{signature[:16]}"
        definitions = ",".join(
            f"{_quote_identifier(column['sql_name'])} TEXT NOT NULL"
            for column in columns
        )
        db.execute(
            f"CREATE TABLE {_quote_identifier(table_name)}("
            "occurrence_source_ordinal INTEGER NOT NULL "
            "REFERENCES asset_occurrences(source_ordinal),"
            "occurrence_id TEXT NOT NULL,"
            "row_ordinal INTEGER NOT NULL CHECK(row_ordinal>=0),"
            f"{definitions},"
            "PRIMARY KEY(occurrence_source_ordinal,row_ordinal))"
        )
        placeholders = ",".join("?" for _ in range(3 + len(columns)))
        insert_sql = (
            f"INSERT INTO {_quote_identifier(table_name)} VALUES({placeholders})"
        )
        source_rows: list[tuple[int, str, list[str]]] = []
        sql_count = 0
        for source_ordinal, occurrence_id, _, source_capture, resource_name in members:
            if not isinstance(resource_name, str):
                raise CaptureError(f"CSV occurrence lacks resource name: {source_ordinal}")
            rows = _csv_rows(
                _csv_bytes(contract["paths"]["root"], source_capture, resource_name),
                len(columns),
                resource_name,
            )
            for row_ordinal, row in enumerate(rows):
                db.execute(insert_sql, (source_ordinal, occurrence_id, row_ordinal, *row))
                source_rows.append(
                    (source_ordinal << 32 | row_ordinal, occurrence_id, row)
                )
                sql_count += 1
        source_digest = _rows_digest(iter(source_rows))
        selected = db.execute(
            f"SELECT occurrence_source_ordinal,row_ordinal,"
            + ",".join(_quote_identifier(c["sql_name"]) for c in columns)
            + f" FROM {_quote_identifier(table_name)} "
            "ORDER BY occurrence_source_ordinal,row_ordinal"
        ).fetchall()
        sql_digest = _rows_digest(
            (source_ordinal << 32 | row_ordinal, occurrence_id, list(values))
            for source_ordinal, occurrence_id, row_ordinal, *values in db.execute(
                f"SELECT occurrence_source_ordinal,occurrence_id,row_ordinal,"
                + ",".join(_quote_identifier(c["sql_name"]) for c in columns)
                + f" FROM {_quote_identifier(table_name)} "
                "ORDER BY occurrence_source_ordinal,row_ordinal"
            ).fetchall()
        )
        if source_digest != sql_digest or sql_count != len(selected):
            raise CaptureError(f"CSV SQL round-trip differs: {table_name}")
        db.execute(
            "INSERT INTO asset_native_tables VALUES(?,?,?,?,?,?,?,?)",
            (
                signature,
                "csv.rows",
                table_name,
                _canonical(columns),
                sql_count,
                len(selected),
                source_digest,
                sql_digest,
            ),
        )
def _xlsx_bytes(root: Path, source_capture: str, resource_name: str) -> bytes:
    source = _capture_file(root, source_capture)
    if resource_name == source_capture:
        return source.read_bytes()
    if not zipfile.is_zipfile(source):
        raise CaptureError(
            f"XLSX occurrence is not directly recoverable from source: {resource_name}"
        )
    with zipfile.ZipFile(source) as archive:
        try:
            return archive.read(resource_name)
        except KeyError as exc:
            raise CaptureError(
                f"XLSX member is missing from {source_capture}: {resource_name}"
            ) from exc


def _xlsx_xml(archive: zipfile.ZipFile, member: str) -> ET.Element | None:
    try:
        payload = archive.read(member)
    except KeyError:
        return None
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise CaptureError(f"invalid XLSX XML member: {member}") from exc


def _xlsx_text(element: ET.Element | None, relative: str) -> str | None:
    if element is None:
        return None
    found = element.find(relative)
    if found is None:
        return None
    return found.text


def _xlsx_inline_text(cell: ET.Element) -> str | None:
    inline = cell.find(f"{OOXML_MAIN}is")
    if inline is None:
        return None
    return "".join(text.text or "" for text in inline.iter(f"{OOXML_MAIN}t"))


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    root = _xlsx_xml(archive, "xl/sharedStrings.xml")
    if root is None:
        return []
    return [
        "".join(text.text or "" for text in item.iter(f"{OOXML_MAIN}t"))
        for item in root.findall(f"{OOXML_MAIN}si")
    ]


def _xlsx_column_index(column_letters: str) -> int:
    value = 0
    for character in column_letters:
        value = value * 26 + (ord(character) - ord("A") + 1)
    return value


def _xlsx_cell_position(reference: str, fallback_row: int | None) -> tuple[int, int]:
    match = re.match(r"^([A-Z]+)([0-9]+)$", reference)
    if match is None:
        if fallback_row is None:
            raise CaptureError(f"XLSX cell lacks usable reference: {reference!r}")
        return fallback_row, 0
    return int(match.group(2)), _xlsx_column_index(match.group(1))


def _xlsx_target_path(target: str) -> str:
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join("xl", target))


def _xlsx_sheet_records(payload: bytes, label: str) -> list[dict[str, Any]]:
    if not zipfile.is_zipfile(io.BytesIO(payload)):
        raise CaptureError(f"XLSX occurrence is not a ZIP package: {label}")
    records: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        workbook = _xlsx_xml(archive, "xl/workbook.xml")
        rels = _xlsx_xml(archive, "xl/_rels/workbook.xml.rels")
        if workbook is None or rels is None:
            raise CaptureError(f"XLSX package lacks workbook relationships: {label}")
        targets = {
            relationship.get("Id"): _xlsx_target_path(relationship.get("Target", ""))
            for relationship in rels.findall(f"{PACKAGE_REL}Relationship")
        }
        shared_strings = _xlsx_shared_strings(archive)
        sheets = workbook.find(f"{OOXML_MAIN}sheets")
        if sheets is None:
            raise CaptureError(f"XLSX package lacks sheets: {label}")
        for sheet_ordinal, sheet in enumerate(sheets.findall(f"{OOXML_MAIN}sheet"), 1):
            sheet_name = sheet.get("name") or ""
            sheet_state = sheet.get("state") or "visible"
            relationship_id = sheet.get(f"{OOXML_REL}id") or ""
            target = targets.get(relationship_id)
            if not target:
                raise CaptureError(f"XLSX sheet target is missing: {label}:{sheet_name}")
            worksheet = _xlsx_xml(archive, target)
            if worksheet is None:
                raise CaptureError(f"XLSX worksheet is missing: {label}:{target}")
            for row in worksheet.findall(f"{OOXML_MAIN}sheetData/{OOXML_MAIN}row"):
                row_number = row.get("r")
                fallback_row = int(row_number) if row_number and row_number.isdigit() else None
                for cell in row.findall(f"{OOXML_MAIN}c"):
                    cell_ref = cell.get("r") or ""
                    row_index, column_index = _xlsx_cell_position(cell_ref, fallback_row)
                    data_type = cell.get("t")
                    raw_value = _xlsx_text(cell, f"{OOXML_MAIN}v")
                    shared_text = None
                    if data_type == "s" and raw_value is not None:
                        try:
                            shared_text = shared_strings[int(raw_value)]
                        except (ValueError, IndexError) as exc:
                            raise CaptureError(
                                f"XLSX shared string index is invalid: {label}:{cell_ref}"
                            ) from exc
                    records.append(
                        {
                            "sheet_ordinal": sheet_ordinal,
                            "sheet_name": sheet_name,
                            "sheet_state": sheet_state,
                            "sheet_rel_id": relationship_id,
                            "cell_ref": cell_ref,
                            "row_index": row_index,
                            "column_index": column_index,
                            "data_type": data_type,
                            "style_id": cell.get("s"),
                            "raw_value": raw_value,
                            "formula": _xlsx_text(cell, f"{OOXML_MAIN}f"),
                            "inline_text": _xlsx_inline_text(cell),
                            "shared_text": shared_text,
                        }
                    )
    # ElementTree traversal above is the worksheet XML order.  Do not sort by
    # coordinates: OOXML permits source order to carry information independent
    # of row/column position, and reordering would make the projection lossy.
    return records


XLSX_CELL_COLUMNS = [
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


def _materialize_xlsx_cell_tables(db: sqlite3.Connection, contract: dict[str, Any]) -> None:
    schemas = {
        row[0]
        for row in db.execute(
            "SELECT schema_signature FROM asset_schemas WHERE kind='xlsx'"
        )
    }
    occurrences = db.execute(
        "SELECT source_ordinal,occurrence_id,schema_signature,source_capture,"
        "resource_name FROM asset_occurrences WHERE route='xlsx' "
        "ORDER BY source_ordinal"
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row | tuple[Any, ...]]] = {}
    for occurrence in occurrences:
        signature = occurrence[2]
        if signature in schemas:
            grouped.setdefault(signature, []).append(occurrence)

    definitions = ",".join(
        f"{_quote_identifier(column['sql_name'])} {column['sqlite_type']}"
        for column in XLSX_CELL_COLUMNS
    )
    value_columns = [column["sql_name"] for column in XLSX_CELL_COLUMNS]
    for signature, members in sorted(grouped.items()):
        # Keep the complete schema signature in the physical table name.  A
        # short prefix can collide when two distinct workbook schemas share
        # the same leading digest bytes; that would silently fold occurrences.
        table_name = f"sf_xlsx_{signature}_cells"
        db.execute(
            f"CREATE TABLE {_quote_identifier(table_name)}("
            "occurrence_source_ordinal INTEGER NOT NULL "
            "REFERENCES asset_occurrences(source_ordinal),"
            "occurrence_id TEXT NOT NULL,"
            "cell_ordinal INTEGER NOT NULL CHECK(cell_ordinal>=1),"
            f"{definitions},"
            "PRIMARY KEY(occurrence_source_ordinal,cell_ordinal))"
        )
        placeholders = ",".join("?" for _ in range(3 + len(value_columns)))
        insert_sql = f"INSERT INTO {_quote_identifier(table_name)} VALUES({placeholders})"
        source_records: list[tuple[int, str, dict[str, Any]]] = []
        sql_count = 0
        for source_ordinal, occurrence_id, _, source_capture, resource_name in members:
            if not isinstance(resource_name, str):
                raise CaptureError(f"XLSX occurrence lacks resource name: {source_ordinal}")
            records = _xlsx_sheet_records(
                _xlsx_bytes(contract["paths"]["root"], source_capture, resource_name),
                resource_name,
            )
            for cell_ordinal, record in enumerate(records, 1):
                db.execute(
                    insert_sql,
                    (
                        source_ordinal,
                        occurrence_id,
                        cell_ordinal,
                        *(record[column] for column in value_columns),
                    ),
                )
                source_records.append(
                    (source_ordinal << 32 | cell_ordinal, occurrence_id, record)
                )
                sql_count += 1
        source_digest = _records_digest(iter(source_records))
        selected = db.execute(
            "SELECT occurrence_source_ordinal,occurrence_id,cell_ordinal,"
            + ",".join(_quote_identifier(column) for column in value_columns)
            + f" FROM {_quote_identifier(table_name)} "
            "ORDER BY occurrence_source_ordinal,cell_ordinal"
        ).fetchall()
        sql_digest = _records_digest(
            (
                source_ordinal << 32 | cell_ordinal,
                occurrence_id,
                dict(zip(value_columns, values)),
            )
            for source_ordinal, occurrence_id, cell_ordinal, *values in selected
        )
        if source_digest != sql_digest or sql_count != len(selected):
            raise CaptureError(f"XLSX SQL round-trip differs: {table_name}")
        db.execute(
            "INSERT INTO asset_native_tables VALUES(?,?,?,?,?,?,?,?)",
            (
                signature,
                "xlsx.cells",
                table_name,
                _canonical(XLSX_CELL_COLUMNS),
                sql_count,
                len(selected),
                source_digest,
                sql_digest,
            ),
        )

def _install_metadata(
    db: sqlite3.Connection,
    contract: dict[str, Any],
    counts: dict[str, int],
    digests: dict[str, str],
) -> None:
    metadata = {
        "provider": "schema_forest",
        "format": FORMAT,
        "lifecycle": "static",
        "capture_path": str(contract["paths"]["root"]),
        "edition_id": contract["edition_id"],
        "manifest_sha256": contract["manifest_sha256"],
        "source_verification_sha256": contract["verification_sha256"],
        "transformation_sha256": contract["transformation_sha256"],
        "source_json_rule": (
            "exact UTF-8 record bytes excluding the single LF terminator"
        ),
        "raw_specialist_artifacts": "excluded_untrusted",
        "source_authority": "capture folder",
        "recovery_rule": (
            "five verified roots are direct; embedded rows are container-traceable"
        ),
        "instructions_sha256": _sha256_uri(_digest_file(INSTRUCTIONS_PATH)),
        "orient_sha256": _sha256_uri(_digest_file(ORIENT_PATH)),
    }
    for relation, count in counts.items():
        metadata[f"count_{relation}"] = str(count)
    for relation, digest in digests.items():
        metadata[f"ordered_digest_{relation}"] = _sha256_uri(digest)
    db.executemany(
        "INSERT OR REPLACE INTO _metadata(key,value) VALUES(?,?)",
        sorted(metadata.items()),
    )
    db.execute(
        "INSERT OR REPLACE INTO _presets(name,description,params,sql,source) "
        "VALUES(?,?,?,?,?)",
        (
            "orient",
            "Schema-forest identity, live shape, recovery, and limitations",
            "",
            ORIENT_PATH.read_text(encoding="utf-8"),
            "schema_forest",
        ),
    )


def _build_database(contract: dict[str, Any], output: Path) -> None:
    manifest = contract["manifest"]
    totals = manifest["totals"]
    db = sqlite3.connect(output)
    try:
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA temp_store=FILE")
        db.execute("PRAGMA cache_size=-65536")
        install_cell_core(db)
        db.executescript(SCHEMA)
        db.execute(
            """
            INSERT INTO asset_editions(
              edition_id,capture_root,manifest_version,manifest_path,
              manifest_sha256,manifest_json,verification_path,
              verification_sha256,verification_json,tika_occurrences,
              physical_occurrences,unique_parse_tasks,executed_tasks,
              schema_signatures,failures,tool_lock_sha256,
              tika_manifest_sha256,transformation_sha256
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                contract["edition_id"],
                str(contract["paths"]["root"]),
                manifest["manifest_version"],
                ".schemas/manifest.json",
                contract["manifest_sha256"],
                contract["manifest_raw"].decode("utf-8"),
                ".receipts/schema-source-verification.json",
                contract["verification_sha256"],
                contract["verification_raw"].decode("utf-8"),
                totals["tika_occurrences"],
                totals["physical_occurrences"],
                totals["unique_parse_tasks"],
                totals["executed_tasks"],
                totals["schema_signatures"],
                totals["failures"],
                manifest["tool_lock_sha256"],
                manifest["tika_manifest_sha256"],
                contract["transformation_sha256"],
            ),
        )

        counts: dict[str, int] = {}
        digests: dict[str, str] = {}
        for filename in (
            "schemas.jsonl.zst",
            "families.jsonl.zst",
            "structures.jsonl.zst",
            "failures.jsonl.zst",
        ):
            relation = STREAMS[filename]
            digest = sha256()
            count = 0
            seen_signatures: set[str] = set()
            for ordinal, source_json, row in _stream_jsonl(
                contract["paths"]["streams"][filename]
            ):
                _ordered_digest_step(digest, ordinal, source_json)
                if relation == "asset_schemas":
                    signature = _hex64(
                        row.get("schema_signature"),
                        f"{filename}:{ordinal}.schema_signature",
                    )
                    if signature in seen_signatures:
                        raise CaptureError(f"duplicate schema signature: {signature}")
                    seen_signatures.add(signature)
                    schema = row.get("schema")
                    if not isinstance(schema, dict):
                        raise CaptureError(f"{filename}:{ordinal}.schema must be an object")
                    kind = _required_text(schema, "kind", f"{filename}:{ordinal}.schema")
                    db.execute(
                        "INSERT INTO asset_schemas VALUES(?,?,?,?)",
                        (signature, ordinal, kind, source_json),
                    )
                elif relation == "asset_schema_families":
                    signature = _hex64(
                        row.get("schema_signature"),
                        f"{filename}:{ordinal}.schema_signature",
                    )
                    if signature in seen_signatures:
                        raise CaptureError(f"duplicate family signature: {signature}")
                    seen_signatures.add(signature)
                    members = row.get("occurrence_ids")
                    if not isinstance(members, list):
                        raise CaptureError(
                            f"{filename}:{ordinal}.occurrence_ids must be an array"
                        )
                    for member in members:
                        _hex64(member, f"{filename}:{ordinal}.occurrence_ids[]")
                    db.execute(
                        "INSERT INTO asset_schema_families VALUES(?,?,?)",
                        (signature, ordinal, source_json),
                    )
                elif relation == "asset_occurrences":
                    _validate_occurrence(row, f"{filename}:{ordinal}")
                    db.execute(
                        "INSERT INTO asset_occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            ordinal,
                            contract["edition_id"],
                            row["occurrence_id"],
                            row["content_sha256"],
                            row["content_type"],
                            row["embedded_depth"],
                            row["resource_name"],
                            row["route"],
                            row["schema_signature"],
                            row["source_capture"],
                            row["status"],
                            row["tika_error"],
                            source_json,
                        ),
                    )
                else:
                    _validate_occurrence(row, f"{filename}:{ordinal}")
                    db.execute(
                        "INSERT INTO asset_failures VALUES(?,?,?,?,?,?,?,?)",
                        (
                            ordinal,
                            contract["edition_id"],
                            row["occurrence_id"],
                            row["route"],
                            row["schema_signature"],
                            row["status"],
                            row["tika_error"],
                            source_json,
                        ),
                    )
                count += 1
            counts[relation] = count
            digests[relation] = digest.hexdigest()
        _materialize_csv_tables(db, contract)
        _materialize_xlsx_cell_tables(db, contract)
        counts["asset_native_tables"] = int(
            db.execute("SELECT count(*) FROM asset_native_tables").fetchone()[0]
        )
        _install_metadata(db, contract, counts, digests)
        db.commit()
        db.execute("VACUUM")
    finally:
        db.close()


def _finalize_sqlite_artifact(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("PRAGMA optimize")
        db.commit()
        db.execute("VACUUM")


def _ordered_digest_from_db(db: sqlite3.Connection, relation: str) -> str:
    digest = sha256()
    for ordinal, source_json in db.execute(
        f"SELECT source_ordinal,source_json FROM {relation} ORDER BY source_ordinal"
    ):
        _ordered_digest_step(digest, int(ordinal), str(source_json))
    return digest.hexdigest()


def verify_cell(path: Path) -> dict[str, Any]:
    target = Path(path).resolve()
    with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        validate_cell_core(db)
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise CaptureError("integrity check failed")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise CaptureError("foreign key check failed")
        edition_rows = db.execute("SELECT * FROM asset_editions").fetchall()
        if len(edition_rows) != 1:
            raise CaptureError("cell must contain one source edition")
        edition = edition_rows[0]
        counts = {
            relation: int(db.execute(f"SELECT count(*) FROM {relation}").fetchone()[0])
            for relation in (
                "asset_editions",
                "asset_occurrences",
                "asset_schemas",
                "asset_schema_families",
                "asset_failures",
                "asset_native_tables",
            )
        }
        expected = {
            "asset_editions": 1,
            "asset_occurrences": int(edition["tika_occurrences"]),
            "asset_schemas": int(edition["schema_signatures"]),
            "asset_schema_families": int(edition["schema_signatures"]),
            "asset_failures": int(edition["failures"]),
            "asset_native_tables": counts["asset_native_tables"],
        }
        if counts != expected:
            raise CaptureError(f"cell population differs from manifest: {counts}")
        physical = int(
            db.execute(
                "SELECT count(*) FROM asset_occurrences "
                "WHERE route<>'nested' AND status<>'container'"
            ).fetchone()[0]
        )
        if physical != int(edition["physical_occurrences"]):
            raise CaptureError("physical occurrence count differs from manifest")
        roots = int(
            db.execute(
                "SELECT count(*) FROM asset_occurrences WHERE embedded_depth=0"
            ).fetchone()[0]
        )
        verified_roots = int(
            db.execute(
                "SELECT count(*) FROM json_each(?, '$.files')",
                (edition["verification_json"],),
            ).fetchone()[0]
        )
        if roots != verified_roots:
            raise CaptureError("depth-zero occurrences differ from verified roots")
        recovery_mismatches = int(
            db.execute(
                """
                WITH expected AS (
                  SELECT j.key AS source_capture,
                         json_extract(j.value,'$.verified_sha256') AS content_sha256
                  FROM asset_editions AS e,
                       json_each(e.verification_json,'$.files') AS j
                ), actual AS (
                  SELECT source_capture,content_sha256
                  FROM asset_occurrences
                  WHERE embedded_depth=0
                ), mismatch AS (
                  SELECT * FROM expected EXCEPT SELECT * FROM actual
                  UNION ALL
                  SELECT * FROM actual EXCEPT SELECT * FROM expected
                )
                SELECT count(*) FROM mismatch
                """
            ).fetchone()[0]
        )
        if recovery_mismatches:
            raise CaptureError("depth-zero recovery evidence differs from verified roots")
        if db.execute(FAMILY_BAG_MISMATCH_SQL).fetchone()[0] != 0:
            raise CaptureError("family membership bag differs from occurrences")
        failure_mismatches = int(
            db.execute(
                """
                WITH occurrence_bag AS (
                  SELECT source_json,count(*) AS n
                  FROM asset_occurrences GROUP BY source_json
                ), failure_bag AS (
                  SELECT source_json,count(*) AS n
                  FROM asset_failures GROUP BY source_json
                )
                SELECT count(*)
                FROM failure_bag AS f
                LEFT JOIN occurrence_bag AS o USING(source_json)
                WHERE o.n IS NULL OR f.n>o.n
                """
            ).fetchone()[0]
        )
        if failure_mismatches:
            raise CaptureError("failure rows are not a subset of occurrences")
        expected_native = _expected_native_tables(db)
        actual_native = {
            (str(row[0]), str(row[1])): str(row[2])
            for row in db.execute(
                "SELECT schema_signature,component,table_name FROM asset_native_tables"
            )
        }
        if expected_native != actual_native:
            raise CaptureError("native table set differs from source schemas")
        for native in db.execute(
            "SELECT table_name,columns_json,source_rows,sql_rows,"
            "source_digest,sql_digest,schema_signature,component "
            "FROM asset_native_tables"
        ):
            (
                table_name,
                columns_json,
                source_rows,
                sql_rows,
                source_digest,
                sql_digest,
                schema_signature,
                component,
            ) = native
            if actual_native.get((str(schema_signature), str(component))) != table_name:
                raise CaptureError(f"native table registry is inconsistent: {table_name}")
            actual_rows = int(
                db.execute(
                    f"SELECT count(*) FROM {_quote_identifier(table_name)}"
                ).fetchone()[0]
            )
            if source_rows != sql_rows or sql_rows != actual_rows:
                raise CaptureError(f"native table row count differs: {table_name}")
            provenance_mismatches = int(
                db.execute(
                    f"SELECT count(*) FROM {_quote_identifier(table_name)} AS n "
                    "LEFT JOIN asset_occurrences AS o "
                    "ON o.source_ordinal=n.occurrence_source_ordinal "
                    "WHERE o.source_ordinal IS NULL "
                    "OR o.occurrence_id<>n.occurrence_id "
                    "OR o.schema_signature<>? "
                    "OR o.route<>?",
                    (schema_signature, "csv" if component == "csv.rows" else "xlsx"),
                ).fetchone()[0]
            )
            if provenance_mismatches:
                raise CaptureError(
                    f"native table occurrence provenance differs: {table_name}"
                )
            columns = json.loads(columns_json)
            if not isinstance(columns, list) or not all(
                isinstance(column, dict) and isinstance(column.get("sql_name"), str)
                for column in columns
            ):
                raise CaptureError(f"native table column map is invalid: {table_name}")
            if component == "csv.rows":
                ordinal_column = "row_ordinal"
            elif component == "xlsx.sheets":
                ordinal_column = "sheet_ordinal"
            else:
                ordinal_column = "cell_ordinal"
            selected = db.execute(
                f"SELECT occurrence_source_ordinal,occurrence_id,{ordinal_column},"
                + ",".join(
                    _quote_identifier(column["sql_name"]) for column in columns
                )
                + f" FROM {_quote_identifier(table_name)} "
                f"ORDER BY occurrence_source_ordinal,{ordinal_column}"
            ).fetchall()
            if component == "csv.rows":
                actual_digest = _rows_digest(
                    (source_ordinal << 32 | ordinal, occurrence_id, list(values))
                    for source_ordinal, occurrence_id, ordinal, *values in selected
                )
            else:
                value_columns = [column["sql_name"] for column in columns]
                actual_digest = _records_digest(
                    (
                        source_ordinal << 32 | ordinal,
                        occurrence_id,
                        dict(zip(value_columns, values)),
                    )
                    for source_ordinal, occurrence_id, ordinal, *values in selected
                )
            if source_digest != sql_digest or sql_digest != actual_digest:
                raise CaptureError(f"native table digest differs: {table_name}")
        for relation in (
            "asset_occurrences",
            "asset_schemas",
            "asset_schema_families",
            "asset_failures",
        ):
            invalid = int(
                db.execute(
                    f"SELECT count(*) FROM {relation} "
                    "WHERE NOT json_valid(source_json) OR instr(source_json,char(10))>0"
                ).fetchone()[0]
            )
            if invalid:
                raise CaptureError(f"{relation} contains altered source JSON")
        present = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE name IN "
                f"({','.join('?' for _ in FORBIDDEN_RELATIONS)})",
                tuple(sorted(FORBIDDEN_RELATIONS)),
            )
        }
        if present:
            raise CaptureError("forbidden generic surfaces are present: " + ", ".join(present))
        metadata = dict(db.execute("SELECT key,value FROM _metadata"))
        ordered_digests: dict[str, str] = {}
        for relation in (
            "asset_occurrences",
            "asset_schemas",
            "asset_schema_families",
            "asset_failures",
        ):
            digest = _ordered_digest_from_db(db, relation)
            expected_digest = metadata.get(f"ordered_digest_{relation}")
            if expected_digest != _sha256_uri(digest):
                raise CaptureError(f"ordered source digest mismatch: {relation}")
            ordered_digests[relation] = digest
        preset = db.execute(
            "SELECT sql FROM _presets WHERE name='orient'"
        ).fetchone()
        if preset is None:
            raise CaptureError("@orient preset is missing")
        sections = [row[0] for row in db.execute(preset[0]).fetchall()]
        expected_sections = [
            "identity",
            "capabilities",
            "shape",
            "source_shape",
                "query_surface",
                "recovery",
                "traversal",
                "limitations",
        ]
        if sections != expected_sections:
            raise CaptureError("@orient does not expose the provider contract")
        distinct_occurrence_ids = int(
            db.execute(
                "SELECT count(DISTINCT occurrence_id) FROM asset_occurrences"
            ).fetchone()[0]
        )
    return {
        **counts,
        "edition_id": str(edition["edition_id"]),
        "physical_occurrences": physical,
        "verified_roots": roots,
        "distinct_occurrence_ids": distinct_occurrence_ids,
        "ordered_source_digests": ordered_digests,
        "orient_sections": expected_sections,
        "cell_sha256": _digest_file(target),
        "cell_bytes": target.stat().st_size,
        "orient_sha256": _digest_file(ORIENT_PATH),
        "instructions_sha256": _digest_file(INSTRUCTIONS_PATH),
    }


def inspect_capture(capture: Path) -> dict[str, Any]:
    contract = _verify_capture(Path(capture))
    totals = contract["manifest"]["totals"]
    return {
        "capture_path": str(contract["paths"]["root"]),
        "manifest_version": contract["manifest"]["manifest_version"],
        "manifest_sha256": contract["manifest_sha256"],
        "source_verification_sha256": contract["verification_sha256"],
        "edition_id": contract["edition_id"],
        "totals": dict(totals),
    }


def _build_receipt(
    contract: dict[str, Any],
    verification: dict[str, Any],
    elapsed_seconds: float,
) -> dict[str, Any]:
    count_names = (
        "asset_editions",
        "asset_occurrences",
        "asset_schemas",
        "asset_schema_families",
        "asset_failures",
        "asset_native_tables",
    )
    return {
        "schema_version": BUILD_RECEIPT_SCHEMA,
        "capture_manifest_sha256": _sha256_uri(contract["manifest_sha256"]),
        "source_verification_sha256": _sha256_uri(contract["verification_sha256"]),
        "transformation_sha256": contract["transformation_sha256"],
        "cell_sha256": _sha256_uri(verification["cell_sha256"]),
        "cell_bytes": verification["cell_bytes"],
        "edition_id": verification["edition_id"],
        "orient_sha256": _sha256_uri(verification["orient_sha256"]),
        "instructions_sha256": _sha256_uri(verification["instructions_sha256"]),
        "counts": {name: verification[name] for name in count_names},
        "physical_occurrences": verification["physical_occurrences"],
        "verified_roots": verification["verified_roots"],
        "distinct_occurrence_ids": verification["distinct_occurrence_ids"],
        "ordered_source_digests": {
            key: _sha256_uri(value)
            for key, value in verification["ordered_source_digests"].items()
        },
        "orient_sections": verification["orient_sections"],
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


def build_cell(
    capture: Path,
    cell_path: Path,
    *,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    target = Path(cell_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if receipt_path is not None and Path(receipt_path).resolve() == target.resolve():
        raise CaptureError("build receipt path must differ from the SQLite artifact")
    staging_root = Path(
        tempfile.mkdtemp(prefix=f".{target.stem}.schema-forest.", dir=target.parent)
    )
    try:
        contract = _verify_capture(Path(capture))
        candidate = staging_root / (target.name + ".candidate")
        _build_database(contract, candidate)
        _finalize_sqlite_artifact(candidate)
        verification = verify_cell(candidate)
        receipt = _build_receipt(
            contract, verification, time.monotonic() - started
        )
        os.replace(candidate, target)
        if receipt_path is not None:
            _write_json_atomic(Path(receipt_path), receipt)
        return receipt
    except CaptureError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        raise CaptureError(f"schema-forest build failed: {exc}") from exc
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def register_cell_path(
    name: str,
    path: Path,
    *,
    description: str | None = None,
) -> str:
    from flex.registry import register_cell

    target = Path(path).resolve()
    return register_cell(
        name=name,
        path=target,
        cell_type="schema_forest",
        description=description,
        checksum=_sha256_uri(_digest_file(target)),
        origin="sealed-schema-forest",
        lifecycle="static",
        active=True,
        unlisted=False,
        allow_external=True,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True)
    parser.add_argument("--cell", required=True)
    parser.add_argument("--receipt")
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args(argv)
    if args.inspect:
        result = inspect_capture(Path(args.capture))
    else:
        result = build_cell(
            Path(args.capture),
            Path(args.cell),
            receipt_path=Path(args.receipt) if args.receipt else None,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
