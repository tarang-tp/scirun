from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from scirun.config import ExperimentSpec, SpecError, parse_spec
from scirun.kubernetes.jobs import (
    TileInfo,
    _to_status,
    build_job_manifest,
    compute_phase,
    flag_stragglers,
    parse_index_ranges,
    tile_infos,
)

SPEC = ExperimentSpec(name="demo", image="img", args=["--w", "9"], parallelism=4, retries=3, gather=["--gather"])
NOW = datetime(2026, 1, 1, tzinfo=UTC)


# --- spec ----------------------------------------------------------------------------------

def test_spec_parses_parallel_fields():
    s = parse_spec({"name": "x", "image": "i", "parallelism": 8, "retries": 1, "gather": {"args": ["--gather", 3]}})
    assert (s.parallelism, s.retries, s.gather) == (8, 1, ["--gather", "3"])


@pytest.mark.parametrize(
    "extra",
    [
        {"parallelism": 0},
        {"parallelism": 33},
        {"parallelism": "4"},
        {"parallelism": True},
        {"retries": -1},
        {"gather": {"args": ["x"]}},  # gather without parallelism
        {"parallelism": 2, "gather": ["x"]},
        {"parallelism": 2, "gather": {"args": ["x"], "image": "y"}},
    ],
)
def test_invalid_parallel_specs(extra):
    with pytest.raises(SpecError):
        parse_spec({"name": "x", "image": "i", **extra})


# --- manifests -----------------------------------------------------------------------------

def test_parallel_manifest_is_an_indexed_job():
    m = build_job_manifest(SPEC, "demo-abc123")
    js = m["spec"]
    assert js["completionMode"] == "Indexed" and js["completions"] == 4 and js["parallelism"] == 4
    assert js["backoffLimitPerIndex"] == 3 and js["maxFailedIndexes"] == 4
    assert "backoffLimit" not in js
    c = js["template"]["spec"]["containers"][0]
    assert {"name": "SCIRUN_TILE_COUNT", "value": "4"} in c["env"]
    assert c["args"] == ["--w", "9"]
    assert m["metadata"]["labels"]["scirun.io/role"] == "main"


def test_gather_manifest_is_a_plain_job_on_the_same_results_dir():
    main = build_job_manifest(SPEC, "demo-abc123")
    g = build_job_manifest(SPEC, "demo-abc123", gather=True)
    assert g["metadata"]["name"] == "demo-abc123-gather"
    assert g["metadata"]["labels"]["scirun.io/role"] == "gather"
    assert "completionMode" not in g["spec"] and g["spec"]["backoffLimit"] == 1
    gc = g["spec"]["template"]["spec"]["containers"][0]
    assert gc["args"] == ["--gather"]
    assert {"name": "SCIRUN_TILE_COUNT", "value": "4"} in gc["env"]
    assert g["spec"]["template"]["spec"]["volumes"] == main["spec"]["template"]["spec"]["volumes"]


def test_single_pod_manifest_has_no_tile_env():
    m = build_job_manifest(ExperimentSpec(name="d", image="i"), "d-1")
    env = m["spec"]["template"]["spec"]["containers"][0]["env"]
    assert [e["name"] for e in env] == ["NODE_NAME"]
    assert "completionMode" not in m["spec"]


# --- status --------------------------------------------------------------------------------

def _st(**kw):
    base = {"succeeded": None, "failed": None, "active": None, "conditions": None,
            "completed_indexes": None, "failed_indexes": None, "completion_time": None, "start_time": None}
    return NS(**{**base, **kw})


def test_indexed_job_is_not_succeeded_until_complete_condition():
    assert compute_phase(_st(succeeded=3, active=1), indexed=True) == "Running"
    assert compute_phase(_st(), indexed=True) == "Pending"
    done = NS(type="Complete", status="True")
    assert compute_phase(_st(succeeded=4, conditions=[done]), indexed=True) == "Succeeded"
    failed = NS(type="Failed", status="True")
    assert compute_phase(_st(succeeded=3, conditions=[failed]), indexed=True) == "Failed"


def test_indexed_status_counts_tiles():
    job = NS(
        metadata=NS(name="demo-abc123", labels={}, creation_timestamp=NOW),
        spec=NS(completion_mode="Indexed", completions=8),
        status=_st(active=2, failed=3, completed_indexes="0-2,5", failed_indexes="6", start_time=NOW),
    )
    s = _to_status(job)
    assert (s.tiles_total, s.tiles_done, s.tiles_failed, s.tiles_active, s.retries) == (8, 4, 1, 2, 3)


@pytest.mark.parametrize("text,expected", [(None, set()), ("", set()), ("3", {3}), ("0-2,5", {0, 1, 2, 5}), ("1,3-4", {1, 3, 4})])
def test_parse_index_ranges(text, expected):
    assert parse_index_ranges(text) == expected


# --- tiles ---------------------------------------------------------------------------------

def _pod(index, phase, node="n1", started=0, finished=None, created=0):
    state = NS(running=None, terminated=None, waiting=None)
    t0 = NOW + timedelta(seconds=started)
    if phase == "Running":
        state.running = NS(started_at=t0)
    elif finished is not None:
        state.terminated = NS(started_at=t0, finished_at=NOW + timedelta(seconds=finished))
    return NS(
        metadata=NS(annotations={"batch.kubernetes.io/job-completion-index": str(index)},
                    creation_timestamp=NOW + timedelta(seconds=created)),
        spec=NS(node_name=node),
        status=NS(phase=phase, container_statuses=[NS(state=state)]),
    )


def test_tile_states():
    pods = [
        _pod(0, "Succeeded", finished=10),
        _pod(1, "Running", started=0),
        _pod(2, "Failed", finished=2, created=0),  # attempt 1 crashed...
        _pod(2, "Running", started=3, created=3),  # ...attempt 2 running
        _pod(3, "Failed", finished=2),
    ]
    tiles = tile_infos(pods, total=6, completed={0}, failed={3}, now=NOW + timedelta(seconds=4))
    assert [t.state for t in tiles] == ["done", "running", "retrying", "failed", "pending", "pending"]
    assert tiles[2].attempts == 2 and tiles[0].seconds == 10 and tiles[1].seconds == 4
    assert tiles[0].node == "n1" and tiles[4].node is None


def test_straggler_is_flagged_only_with_enough_finished_tiles():
    done = [TileInfo(i, "done", 1, "n", 10.0) for i in range(3)]
    slow = TileInfo(3, "running", 1, "n", 50.0)
    ok = TileInfo(4, "running", 1, "n", 12.0)
    flagged = flag_stragglers([*done, slow, ok])
    assert [t.slow for t in flagged] == [False, False, False, True, False]
    assert not any(t.slow for t in flag_stragglers([done[0], slow]))  # too little evidence
