"""Local per-job records, kept in the results root so they outlive the Kubernetes Job.

Layout under results_root():
    .jobs/<job-id>.json        written by `scirun submit` (the submitted spec)
    <job-id>/metrics.json      written by the workload (what it used), by convention
"""

import json
from dataclasses import asdict, fields
from pathlib import Path

from scirun.config import ExperimentSpec, results_root
from scirun.results import valid_job_id


def save_record(job_id: str, spec: ExperimentSpec) -> None:
    path = results_root() / ".jobs" / f"{job_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(spec)))


def load_record(job_id: str) -> dict | None:
    return _load(results_root() / ".jobs" / f"{job_id}.json", job_id)


def spec_from_record(record: dict | None) -> ExperimentSpec | None:
    """Rebuild the submitted spec from its record, or None for records too old to hold one."""
    if not record or "name" not in record or "image" not in record:
        return None
    return ExperimentSpec(**{f.name: record[f.name] for f in fields(ExperimentSpec) if f.name in record})


def load_metrics(job_id: str) -> dict | None:
    return _load(results_root() / job_id / "metrics.json", job_id)


def _load(path: Path, job_id: str) -> dict | None:
    if not valid_job_id(job_id):
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def delete_record(job_id: str) -> bool:
    if not valid_job_id(job_id):
        return False
    path = results_root() / ".jobs" / f"{job_id}.json"
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
