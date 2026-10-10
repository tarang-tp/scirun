"""Create Jobs and report their status."""

import statistics
import time
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
ROLE_LABEL = "scirun.io/role"
INDEX_ANNOTATION = "batch.kubernetes.io/job-completion-index"


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
    # Parallel (Indexed) jobs only:
    tiles_total: int | None = None
    tiles_done: int | None = None
    tiles_failed: int | None = None  # tiles that used up their retries
    tiles_active: int | None = None
    retries: int | None = None  # failed pod attempts so far, across all tiles


@dataclass(frozen=True)
class TileInfo:
    index: int
    state: str  # pending | running | retrying | done | failed
    attempts: int
    node: str | None
    seconds: float | None  # container run time of the latest attempt
    slow: bool = False


def new_job_id(spec: ExperimentSpec) -> str:
    return f"{spec.name}-{uuid.uuid4().hex[:6]}"


def gather_job_id(job_id: str) -> str:
    return f"{job_id}-gather"


def build_job_manifest(spec: ExperimentSpec, job_id: str, *, gather: bool = False) -> dict[str, Any]:
    """The Job for a spec; with gather=True, the follow-up Job that combines tile outputs."""
    parallel = spec.parallelism > 1
    env: list[dict[str, Any]] = [
        {"name": "NODE_NAME", "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}}}
    ]
    if parallel:
        env.append({"name": "SCIRUN_TILE_COUNT", "value": str(spec.parallelism)})
    container: dict[str, Any] = {
        "name": spec.name,
        "image": spec.image,
        "imagePullPolicy": "IfNotPresent",  # images are loaded into kind, not pulled
        "args": (spec.gather or []) if gather else spec.args,
        "env": env,
        "resources": {
            "requests": {"cpu": spec.cpu, "memory": spec.memory},
            "limits": {"cpu": spec.cpu, "memory": spec.memory},
        },
        "volumeMounts": [{"name": "results", "mountPath": spec.results_path}],
    }
    if spec.command is not None:
        container["command"] = spec.command
    labels = {**MANAGED_BY, EXPERIMENT_LABEL: spec.name, ROLE_LABEL: "gather" if gather else "main"}

    job_spec: dict[str, Any] = {
        "ttlSecondsAfterFinished": 3600,
        "template": {
            "metadata": {"labels": labels},
            "spec": {
                "restartPolicy": "Never",
                "containers": [container],
                "volumes": [
                    {
                        "name": "results",
                        "hostPath": {"path": f"{NODE_RESULTS_ROOT}/{job_id}", "type": "DirectoryOrCreate"},
                    }
                ],
            },
        },
    }
    if parallel and not gather:
        # One pod per tile; each tile retries on its own, and a tile that gives up does not stop the rest.
        job_spec.update(
            completionMode="Indexed",
            completions=spec.parallelism,
            parallelism=spec.parallelism,
            backoffLimitPerIndex=spec.retries,
            maxFailedIndexes=spec.parallelism,
        )
    else:
        job_spec["backoffLimit"] = 1 if gather else 0
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": gather_job_id(job_id) if gather else job_id, "labels": labels},
        "spec": job_spec,
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


def submit_gather(spec: ExperimentSpec, job_id: str) -> str:
    """Create the gather Job for a finished parallel job, replacing a failed earlier attempt."""
    _load()
    name = gather_job_id(job_id)
    try:
        existing = client.BatchV1Api().read_namespaced_job(name, NAMESPACE)
    except ApiException as e:
        if e.status != 404:
            raise
    else:
        if compute_phase(existing.status) != "Failed":
            return name  # already running or done; gathering is idempotent, so just reuse it
        client.BatchV1Api().delete_namespaced_job(name, NAMESPACE, propagation_policy="Foreground")
        _wait_gone(name)
    client.BatchV1Api().create_namespaced_job(NAMESPACE, build_job_manifest(spec, job_id, gather=True))
    return name


def _wait_gone(name: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            client.BatchV1Api().read_namespaced_job(name, NAMESPACE)
        except ApiException as e:
            if e.status == 404:
                return
            raise
        time.sleep(0.5)
    raise TimeoutError(f"job {name} did not finish deleting")


def compute_phase(status: Any, indexed: bool = False) -> str:
    """Map a V1JobStatus to a simple phase.

    An Indexed job reports `succeeded` per finished pod, so only its conditions say it is done.
    """
    conditions = status.conditions or []
    if any(c.type == "Complete" and c.status == "True" for c in conditions):
        return "Succeeded"
    if any(c.type == "Failed" and c.status == "True" for c in conditions):
        return "Failed"
    if indexed:
        return "Running" if status.active or status.succeeded or status.failed else "Pending"
    if status.succeeded:
        return "Succeeded"
    if status.failed:
        return "Failed"
    if status.active:
        return "Running"
    return "Pending"


def parse_index_ranges(text: str | None) -> set[int]:
    """'0-2,5' -> {0, 1, 2, 5}, the format Kubernetes uses for completedIndexes/failedIndexes."""
    indexes: set[int] = set()
    for part in (text or "").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        indexes.update(range(int(lo), int(hi or lo) + 1))
    return indexes


def _to_status(job: Any, detail: str | None = None) -> JobStatus:
    status = job.status
    spec = getattr(job, "spec", None)
    indexed = getattr(spec, "completion_mode", None) == "Indexed"
    phase = compute_phase(status, indexed)

    end = status.completion_time
    if end is None and phase == "Failed":
        end = next(
            (c.last_transition_time for c in status.conditions or [] if c.type == "Failed"), None
        )
    start = status.start_time
    duration = None
    if start is not None and phase != "Pending":
        duration = ((end or datetime.now(UTC)) - start).total_seconds()

    tiles: dict[str, Any] = {}
    if indexed:
        tiles = {
            "tiles_total": spec.completions,
            "tiles_done": len(parse_index_ranges(status.completed_indexes)),
            "tiles_failed": len(parse_index_ranges(status.failed_indexes)),
            "tiles_active": status.active or 0,
            "retries": status.failed or 0,
        }

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
        **tiles,
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
    indexed = job.spec.completion_mode == "Indexed"
    failed = compute_phase(job.status, indexed) == "Failed"
    detail = None
    if failed:
        detail = _failed_tiles_detail(job) if indexed else _failure_detail(job_id)
    return _to_status(job, detail)


def list_jobs() -> list[JobStatus]:
    """SciRun-managed jobs (not their gather jobs), newest first."""
    _load()
    selector = ",".join([*(f"{k}={v}" for k, v in MANAGED_BY.items()), f"{ROLE_LABEL}!=gather"])
    items = client.BatchV1Api().list_namespaced_job(NAMESPACE, label_selector=selector).items
    statuses = [_to_status(j) for j in items]
    return sorted(statuses, key=lambda s: s.created or datetime.min.replace(tzinfo=UTC), reverse=True)


def delete_job(job_id: str) -> None:
    """Delete a SciRun Job (and its gather Job) with their pods. Refuses Jobs SciRun did not create."""
    _load()
    job = _read_job(job_id)
    labels = job.metadata.labels or {}
    if any(labels.get(k) != v for k, v in MANAGED_BY.items()):
        raise NotManaged(job_id)
    for name in (job_id, gather_job_id(job_id)):
        try:
            client.BatchV1Api().delete_namespaced_job(name, NAMESPACE, propagation_policy="Background")
        except ApiException as e:
            if e.status != 404:
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


def _failed_tiles_detail(job: Any) -> str:
    failed = sorted(parse_index_ranges(job.status.failed_indexes))
    listed = ", ".join(map(str, failed)) or "?"
    return f"tile(s) {listed} failed after {job.spec.backoff_limit_per_index} retries"


# --- per-tile view -------------------------------------------------------------------------


def _pod_seconds(pod: Any, now: datetime) -> float | None:
    """Run time of a pod's container: start to finish, or start to now while running."""
    for cs in pod.status.container_statuses or []:
        term = cs.state.terminated if cs.state else None
        if term is not None and term.started_at and term.finished_at:
            return (term.finished_at - term.started_at).total_seconds()
        running = cs.state.running if cs.state else None
        if running is not None and running.started_at:
            return (now - running.started_at).total_seconds()
    return None


def tile_infos(pods: list[Any], total: int, completed: set[int], failed: set[int], now: datetime) -> list[TileInfo]:
    """Fold a Job's pods into one row per tile, with stragglers flagged."""
    by_index: dict[int, list[Any]] = {}
    for pod in pods:
        index = (pod.metadata.annotations or {}).get(INDEX_ANNOTATION)
        if index is not None:
            by_index.setdefault(int(index), []).append(pod)

    tiles = []
    for i in range(total):
        attempts = sorted(by_index.get(i, []), key=lambda p: p.metadata.creation_timestamp)
        last = attempts[-1] if attempts else None
        if i in completed:
            state = "done"
        elif i in failed:
            state = "failed"
        elif last is None or last.status.phase == "Pending":
            state = "retrying" if len(attempts) > 1 else "pending"
        elif last.status.phase == "Running":
            state = "retrying" if len(attempts) > 1 else "running"
        else:  # last attempt crashed; the Job controller is about to start another
            state = "retrying"
        tiles.append(
            TileInfo(
                index=i,
                state=state,
                attempts=len(attempts),
                node=last.spec.node_name if last else None,
                seconds=_pod_seconds(last, now) if last else None,
            )
        )
    return flag_stragglers(tiles)


def flag_stragglers(tiles: list[TileInfo], factor: float = 2.0, min_done: int = 3, min_seconds: float = 5.0) -> list[TileInfo]:
    """Mark running tiles that have already taken much longer than the typical finished tile."""
    done = [t.seconds for t in tiles if t.state == "done" and t.seconds is not None]
    if len(done) < min_done:
        return tiles
    limit = max(factor * statistics.median(done), min_seconds)
    return [
        TileInfo(**{**t.__dict__, "slow": True}) if t.state in ("running", "retrying") and (t.seconds or 0) > limit else t
        for t in tiles
    ]


def get_tiles(job_id: str) -> list[TileInfo]:
    _load()
    job = _read_job(job_id)
    if job.spec.completion_mode != "Indexed":
        return []
    pods = client.CoreV1Api().list_namespaced_pod(NAMESPACE, label_selector=f"job-name={job_id}").items
    return tile_infos(
        pods,
        job.spec.completions,
        parse_index_ranges(job.status.completed_indexes),
        parse_index_ranges(job.status.failed_indexes),
        datetime.now(UTC),
    )


def current_context() -> str | None:
    """Name of the active kubeconfig context, or None if there isn't one."""
    try:
        _, active = kube_config.list_kube_config_contexts()
    except (kube_config.ConfigException, OSError):
        return None
    return active["name"] if active else None
