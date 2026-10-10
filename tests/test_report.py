from datetime import datetime, timezone

import pytest

from scirun.config import ExperimentSpec
from scirun.kubernetes.jobs import JobStatus
from scirun.records import load_metrics, load_record, save_record
from scirun.report import build_row, cpu_utilization, format_table, memory_utilization

NOW = datetime.now(timezone.utc)
STATUS = JobStatus("demo-abc123", "Succeeded", NOW, NOW, 20.0)
RECORD = {"cpu": "500m", "memory": "1Gi"}
METRICS = {"wall_seconds": 10.0, "cpu_seconds": 4.0, "peak_memory_bytes": 256 * 2**20}


def test_row_and_utilization():
    row = build_row("demo-abc123", STATUS, RECORD, METRICS)
    assert row.cpu_request == 0.5 and row.memory_request == 2**30
    assert cpu_utilization(row) == pytest.approx(0.8)  # 4s of CPU in 10s * 0.5 CPU
    assert memory_utilization(row) == pytest.approx(0.25)


def test_missing_pieces_render_dashes():
    row = build_row("demo-abc123", None, None, None)
    assert cpu_utilization(row) is None and memory_utilization(row) is None
    line = format_table([row]).splitlines()[1]
    assert "gone" in line and "-" in line


def test_table_contains_values():
    out = format_table([build_row("demo-abc123", STATUS, RECORD, METRICS)])
    assert "demo-abc123" in out and "1024Mi" in out and "(80%)" in out and "(25%)" in out


def test_records_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SCIRUN_RESULTS_DIR", str(tmp_path))
    save_record("demo-abc123", ExperimentSpec(name="demo", image="i", cpu="2", memory="2Gi"))
    assert load_record("demo-abc123")["cpu"] == "2"
    assert load_metrics("demo-abc123") is None
    (tmp_path / "demo-abc123").mkdir()
    (tmp_path / "demo-abc123" / "metrics.json").write_text('{"wall_seconds": 1}')
    assert load_metrics("demo-abc123") == {"wall_seconds": 1}
    assert load_record("../x") is None
