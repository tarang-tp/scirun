"""Local per-job records, kept in the results root so they outlive the Kubernetes Job.

Layout under results_root():
    .jobs/<job-id>.json        written by `scirun submit` (what was requested)
    <job-id>/metrics.json      written by the workload (what it used), by convention
"""

import json
from pathlib import Path

from scirun.config import ExperimentSpec, results_root
from scirun.results import valid_job_id


def save_record(job_id: str, spec: ExperimentSpec) -> None:
    path = results_root() / ".jobs" / f"{job_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"experiment": spec.name, "image": spec.image, "cpu": spec.cpu, "memory": spec.memory})
    )


def load_record(job_id: str) -> dict | None:
    return _load(results_root() / ".jobs" / f"{job_id}.json", job_id)


def load_metrics(job_id: str) -> dict | None:
    return _load(results_root() / job_id / "metrics.json", job_id)


def _load(path: Path, job_id: str) -> dict | None:
    if not valid_job_id(job_id):
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
