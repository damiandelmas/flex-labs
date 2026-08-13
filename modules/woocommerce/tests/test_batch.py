from __future__ import annotations

import json
import threading
import time

import pytest

from modules.woocommerce.compile import batch


def _write_manifest(tmp_path, rows):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_batch_rejects_duplicate_cells_before_building(tmp_path, monkeypatch):
    manifest = _write_manifest(
        tmp_path,
        [
            {"cell": "lighting_woo", "url": "https://one.example"},
            {"cell": "lighting_woo", "url": "https://two.example"},
        ],
    )
    monkeypatch.setenv("FLEX_HOME", str(tmp_path / "flex-home"))

    def fail_build(*args, **kwargs):
        raise AssertionError("build should not run for an invalid manifest")

    monkeypatch.setattr(batch, "build_cell_from_store_api", fail_build)

    with pytest.raises(ValueError, match="duplicate manifest cell"):
        batch.run_batch(manifest)


def test_batch_rejects_duplicate_urls_before_building(tmp_path, monkeypatch):
    manifest = _write_manifest(
        tmp_path,
        [
            {"cell": "one_woo", "url": "https://store.example/"},
            {"cell": "two_woo", "url": "https://STORE.example"},
        ],
    )
    monkeypatch.setenv("FLEX_HOME", str(tmp_path / "flex-home"))

    def fail_build(*args, **kwargs):
        raise AssertionError("build should not run for an invalid manifest")

    monkeypatch.setattr(batch, "build_cell_from_store_api", fail_build)

    with pytest.raises(ValueError, match="duplicate manifest url"):
        batch.run_batch(manifest)


def test_batch_builds_and_registers_each_manifest_row_once_without_loss(tmp_path, monkeypatch):
    manifest = _write_manifest(
        tmp_path,
        [
            {"cell": "one_woo", "url": "https://one.example", "description": "One source"},
            {"cell": "two_woo", "url": "https://two.example", "price_policy": "login_required"},
            {"cell": "three_woo", "url": "https://three.example"},
        ],
    )
    monkeypatch.setenv("FLEX_HOME", str(tmp_path / "flex-home"))
    calls = []
    registrations = []
    active = 0
    peak_active = 0
    lock = threading.Lock()

    def fake_build(
        url, cell_path, *, description, price_policy, per_page, retries, retry_delay,
        refresh_interval
    ):
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
            calls.append((
                url, str(cell_path), description, price_policy, per_page, retries,
                retry_delay, refresh_interval,
            ))
        time.sleep(0.01)
        with lock:
            active -= 1
        return {"cell": str(cell_path), "url": url}

    def fake_register(cell_name, cell_path, *, description, refresh_interval):
        registrations.append((cell_name, str(cell_path), description, refresh_interval))

    monkeypatch.setattr(batch, "build_cell_from_store_api", fake_build)
    monkeypatch.setattr(batch, "register_cell_path", fake_register)

    receipt = batch.run_batch(
        manifest,
        concurrency=2,
        per_page=25,
        retries=4,
        retry_delay=0.0,
        refresh_interval=99,
    )

    assert receipt["complete"] is True
    assert receipt["requested_count"] == 3
    assert receipt["ok_count"] == 3
    assert receipt["error_count"] == 0
    assert [row["index"] for row in receipt["rows"]] == [0, 1, 2]
    assert [row["cell"] for row in receipt["rows"]] == ["one_woo", "two_woo", "three_woo"]
    assert sorted(call[0] for call in calls) == [
        "https://one.example",
        "https://three.example",
        "https://two.example",
    ]
    assert len(calls) == 3
    assert len(registrations) == 3
    assert peak_active <= 2
    assert all(call[4:] == (25, 4, 0.0, 99) for call in calls)
    policies = {call[0]: call[3] for call in calls}
    assert policies == {
        "https://one.example": "public",
        "https://two.example": "login_required",
        "https://three.example": "public",
    }
    assert all(registration[3] == 99 for registration in registrations)


def test_batch_reports_row_error_and_keeps_other_unique_rows(tmp_path, monkeypatch):
    manifest = _write_manifest(
        tmp_path,
        [
            {"cell": "one_woo", "url": "https://one.example"},
            {"cell": "two_woo", "url": "https://two.example"},
        ],
    )
    monkeypatch.setenv("FLEX_HOME", str(tmp_path / "flex-home"))
    registered = []

    def fake_build(url, cell_path, **kwargs):
        if url == "https://two.example":
            raise ValueError("fixture failure")
        return {"cell": str(cell_path), "url": url}

    def fake_register(cell_name, cell_path, **kwargs):
        registered.append(cell_name)

    monkeypatch.setattr(batch, "build_cell_from_store_api", fake_build)
    monkeypatch.setattr(batch, "register_cell_path", fake_register)

    receipt = batch.run_batch(manifest, concurrency=2)

    assert receipt["complete"] is False
    assert receipt["ok_count"] == 1
    assert receipt["error_count"] == 1
    assert receipt["rows"][0]["status"] == "ok"
    assert receipt["rows"][1]["status"] == "error"
    assert receipt["rows"][1]["error"]["message"] == "fixture failure"
    assert registered == ["one_woo"]


def test_batch_rejects_invalid_price_policy_before_building(tmp_path, monkeypatch):
    manifest = _write_manifest(
        tmp_path,
        [{"cell": "one_woo", "url": "https://one.example", "price_policy": "guess"}],
    )
    monkeypatch.setenv("FLEX_HOME", str(tmp_path / "flex-home"))

    def fail_build(*args, **kwargs):
        raise AssertionError("build should not run for an invalid manifest")

    monkeypatch.setattr(batch, "build_cell_from_store_api", fail_build)
    with pytest.raises(ValueError, match="price_policy must be"):
        batch.run_batch(manifest)
