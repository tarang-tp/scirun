from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from scirun.config import ExperimentSpec
from scirun.kubernetes.jobs import EXPERIMENT_LABEL, _to_status
from scirun.records import delete_record, save_record
from scirun.results import delete_results
from scirun.ui import humanize_age


def _job(name="demo-abc123", labels=None, **status):
    base = {"succeeded": None, "failed": None, "active": None, "conditions": None,
            "completion_time": None, "start_time": None}
    return NS(
        metadata=NS(name=name, labels=labels, creation_timestamp=datetime.now(UTC)),
        status=NS(**{**base, **status}),
    )


def test_to_status_reads_experiment_label_and_phase():
    start = datetime.now(UTC) - timedelta(seconds=30)
    s = _to_status(_job(labels={EXPERIMENT_LABEL: "demo"}, active=1, start_time=start))
    assert s.job_id == "demo-abc123" and s.experiment == "demo" and s.phase == "Running"
    assert s.duration_seconds == pytest.approx(30, abs=2)


def test_pending_job_has_no_duration():
    s = _to_status(_job(start_time=datetime.now(UTC)))
    assert s.phase == "Pending" and s.duration_seconds is None and s.experiment is None


@pytest.mark.parametrize("secs,expected", [(0, "0s"), (59, "59s"), (60, "1m"), (3700, "1h"), (200000, "2d"), (-5, "0s")])
def test_humanize_age(secs, expected):
    assert humanize_age(secs) == expected


def test_delete_results(tmp_path):
    (tmp_path / "demo-abc123").mkdir()
    (tmp_path / "demo-abc123" / "f.txt").write_text("x")
    (tmp_path / "keep-me").mkdir()
    assert delete_results("demo-abc123", tmp_path) is True
    assert not (tmp_path / "demo-abc123").exists() and (tmp_path / "keep-me").exists()
    assert delete_results("demo-abc123", tmp_path) is False


@pytest.mark.parametrize("bad", ["..", "../keep-me", "a/b", ""])
def test_delete_results_refuses_unsafe_ids(tmp_path, bad):
    (tmp_path / "keep-me").mkdir()
    assert delete_results(bad, tmp_path) is False
    assert (tmp_path / "keep-me").exists()


def test_delete_results_does_not_follow_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "demo-abc123").symlink_to(outside)
    assert delete_results("demo-abc123", root) is False
    assert outside.exists()


def test_delete_record(tmp_path, monkeypatch):
    monkeypatch.setenv("SCIRUN_RESULTS_DIR", str(tmp_path))
    save_record("demo-abc123", ExperimentSpec(name="demo", image="i"))
    assert delete_record("demo-abc123") is True
    assert delete_record("demo-abc123") is False
