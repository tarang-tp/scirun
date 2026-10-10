"""Read pod logs for a Job."""

import time
from collections.abc import Iterator

from kubernetes import client

from scirun.kubernetes.jobs import NAMESPACE, JobNotFound, _load

_WAIT_SECONDS = 60


def stream_logs(job_id: str, follow: bool = False) -> Iterator[str]:
    _load()
    core = client.CoreV1Api()
    pod = _wait_for_pod(core, job_id)
    if not follow:
        yield core.read_namespaced_pod_log(pod, NAMESPACE)
        return
    resp = core.read_namespaced_pod_log(pod, NAMESPACE, follow=True, _preload_content=False)
    try:
        for chunk in resp.stream():
            yield chunk.decode(errors="replace")
    finally:
        resp.release_conn()


def _wait_for_pod(core: client.CoreV1Api, job_id: str) -> str:
    """Return the name of the Job's pod once its container has started."""
    deadline = time.monotonic() + _WAIT_SECONDS
    while True:
        pods = core.list_namespaced_pod(NAMESPACE, label_selector=f"job-name={job_id}").items
        if pods and pods[0].status.phase != "Pending":
            return pods[0].metadata.name
        if time.monotonic() > deadline:
            if not pods:
                raise JobNotFound(job_id)
            raise TimeoutError(f"pod for {job_id} still Pending after {_WAIT_SECONDS}s")
        time.sleep(1)
