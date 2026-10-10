from types import SimpleNamespace as NS

from scirun.config import ExperimentSpec
from scirun.kubernetes.jobs import build_job_manifest, compute_phase, new_job_id

SPEC = ExperimentSpec(name="demo", image="img:1", args=["--n", "5"], cpu="2", memory="512Mi")


def test_job_id_has_name_prefix_and_is_unique():
    a, b = new_job_id(SPEC), new_job_id(SPEC)
    assert a.startswith("demo-") and len(a) == len("demo-") + 6
    assert a != b


def test_manifest_shape():
    m = build_job_manifest(SPEC, "demo-abc123")
    assert m["metadata"]["name"] == "demo-abc123"
    assert m["metadata"]["labels"]["scirun.io/experiment"] == "demo"
    assert m["spec"]["backoffLimit"] == 0
    pod = m["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    c = pod["containers"][0]
    assert c["image"] == "img:1" and c["args"] == ["--n", "5"]
    assert "command" not in c
    assert c["resources"]["requests"] == {"cpu": "2", "memory": "512Mi"}
    assert c["resources"]["limits"] == c["resources"]["requests"]
    assert c["volumeMounts"][0]["mountPath"] == "/results"


def test_manifest_command_override():
    spec = ExperimentSpec(name="d", image="i", command=["python", "x.py"])
    c = build_job_manifest(spec, "d-1")["spec"]["template"]["spec"]["containers"][0]
    assert c["command"] == ["python", "x.py"]


def _status(**kw):
    return NS(**{"succeeded": None, "failed": None, "active": None, "conditions": None, **kw})


def test_phases():
    assert compute_phase(_status()) == "Pending"
    assert compute_phase(_status(active=1)) == "Running"
    assert compute_phase(_status(succeeded=1)) == "Succeeded"
    assert compute_phase(_status(failed=1)) == "Failed"
    failed_cond = NS(type="Failed", status="True")
    assert compute_phase(_status(conditions=[failed_cond])) == "Failed"
