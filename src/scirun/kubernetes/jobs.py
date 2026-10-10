"""Create Jobs and report their status."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from kubernetes import client
from kubernetes import config as kube_config
from kubernetes.client.rest import ApiException

from scirun.config import NODE_RESULTS_ROOT, ExperimentSpec

NAMESPACE = "default"
MANAGED_BY = {"app.kubernetes.io/managed-by": "scirun"}
EXPERIMENT_LABEL = "scirun.io/experiment"


class JobNotFound(Exception):
    pass


class NotManaged(Exception):
    """The Job exists but was not created by SciRun."""


@dataclass(frozen=True)
class JobStatus:
    job_id: str
    phase: str  # Pending | Running | Succeeded | Failed
    start_time: datetime | None
    end_time: datetime | None
    duration_seconds: float | None
    detail: str | None = None  # failure explanation, if any
    experiment: str | None = None  # spec name, from the Job's label
    created: datetime | None = None


def new_job_id(spec: ExperimentSpec) -> str:
    return f"{spec.name}-{uuid.uuid4().hex[:6]}"


def build_job_manifest(spec: ExperimentSpec, job_id: str) -> dict[str, Any]:
    container: dict[str, Any] = {
        "name": spec.name,
        "image": spec.image,
        "imagePullPolicy": "IfNotPresent",  # images are loaded into kind, not pulled
        "args": spec.args,
        "resources": {
            "requests": {"cpu": spec.cpu, "memory": spec.memory},
            "limits": {"cpu": spec.cpu, "memory": spec.memory},
        },
        "volumeMounts": [{"name": "results", "mountPath": spec.results_path}],
    }
    if spec.command is not None:
        container["command"] = spec.command
    labels = {**MANAGED_BY, EXPERIMENT_LABEL: spec.name}
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": job_id, "labels": labels},
        "spec": {
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": 3600,
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [container],
                    "volumes": [
                        {
                            "name": "results",
                            "hostPath": {
                                "path": f"{NODE_RESULTS_ROOT}/{job_id}",
                                "type": "DirectoryOrCreate",
                            },
                        }
                    ],
                },
            },
        },
    }


def _load() -> None:
    try:
        kube_config.load_kube_config()
    except kube_config.ConfigException as e:
        raise RuntimeError(f"no usable kubeconfig: {e}") from e


def submit_job(spec: ExperimentSpec) -> str:
    _load()
    job_id = new_job_id(spec)
    client.BatchV1Api().create_namespaced_job(NAMESPACE, build_job_manifest(spec, job_id))
    return job_id


def compute_phase(status: Any) -> str:
    """Map a V1JobStatus to a simple phase."""
    if status.succeeded:
        return "Succeeded"
    if status.failed or any(c.type == "Failed" and c.status == "True" for c in status.conditions or []):
        return "Failed"
    if status.active:
        return "Running"
    return "Pending"


def _to_status(job: Any, detail: str | None = None) -> JobStatus:
    status = job.status
    phase = compute_phase(status)

    end = status.completion_time
    if end is None and phase == "Failed":
        end = next(
            (c.last_transition_time for c in status.conditions or [] if c.type == "Failed"), None
        )
    start = status.start_time
    duration = None
    if start is not None and phase != "Pending":
        duration = ((end or datetime.now(UTC)) - start).total_seconds()

    labels = job.metadata.labels or {}
    return JobStatus(
        job.metadata.name,
        phase,
        start,
        end,
        duration,
        detail,
        experiment=labels.get(EXPERIMENT_LABEL),
        created=job.metadata.creation_timestamp,
    )


def _read_job(job_id: str) -> Any:
    try:
        return client.BatchV1Api().read_namespaced_job(job_id, NAMESPACE)
    except ApiException as e:
        if e.status == 404:
            raise JobNotFound(job_id) from e
        raise


def get_status(job_id: str) -> JobStatus:
    _load()
    job = _read_job(job_id)
    detail = _failure_detail(job_id) if compute_phase(job.status) == "Failed" else None
    return _to_status(job, detail)


def list_jobs() -> list[JobStatus]:
    """SciRun-managed Jobs, newest first."""
    _load()
    selector = ",".join(f"{k}={v}" for k, v in MANAGED_BY.items())
    items = client.BatchV1Api().list_namespaced_job(NAMESPACE, label_selector=selector).items
    statuses = [_to_status(j) for j in items]
    return sorted(statuses, key=lambda s: s.created or datetime.min.replace(tzinfo=UTC), reverse=True)


def delete_job(job_id: str) -> None:
    """Delete a SciRun Job and its pods. Refuses Jobs SciRun did not create."""
    _load()
    job = _read_job(job_id)
    labels = job.metadata.labels or {}
    if any(labels.get(k) != v for k, v in MANAGED_BY.items()):
        raise NotManaged(job_id)
    try:
        client.BatchV1Api().delete_namespaced_job(job_id, NAMESPACE, propagation_policy="Background")
    except ApiException as e:
        if e.status == 404:
            raise JobNotFound(job_id) from e
        raise


def _failure_detail(job_id: str) -> str | None:
    pods = client.CoreV1Api().list_namespaced_pod(NAMESPACE, label_selector=f"job-name={job_id}")
    for pod in pods.items:
        for cs in pod.status.container_statuses or []:
            term = cs.state.terminated if cs.state else None
            if term is not None and term.exit_code != 0:
                return f"container exited with code {term.exit_code} ({term.reason})"
            waiting = cs.state.waiting if cs.state else None
            if waiting is not None and waiting.reason:
                return f"container {waiting.reason}: {waiting.message or ''}".strip()
    return None


def current_context() -> str | None:
    """Name of the active kubeconfig context, or None if there isn't one."""
    try:
        _, active = kube_config.list_kube_config_contexts()
    except (kube_config.ConfigException, OSError):
        return None
    return active["name"] if active else None
