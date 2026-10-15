import pytest

from scirun.config import SpecError, parse_spec
from scirun.sweep import MAX_RUNS, expand, parse_vary, speedups

DATA = {
    "name": "demo",
    "image": "img",
    "args": ["--n", 1],
    "parallelism": 4,
    "gather": {"args": ["--gather"]},
    "resources": {"cpu": "1", "memory": "1Gi"},
}


def test_parse_vary():
    assert parse_vary(["cpu=0.5, 1", "parallelism=2,4,2"]) == {"cpu": ["0.5", "1"], "parallelism": ["2", "4"]}


@pytest.mark.parametrize("bad", [[], ["cpu"], ["cpu="], ["args=1,2"], ["cpu=1", "cpu=2"], ["=1,2"]])
def test_parse_vary_rejects(bad):
    with pytest.raises(SpecError):
        parse_vary(bad)


def test_grid_is_a_cartesian_product_first_variable_slowest():
    runs = expand(DATA, {"cpu": ["0.5", "1"], "parallelism": ["2", "4"]})
    assert [r.label for r in runs] == [
        "cpu=0.5 parallelism=2", "cpu=0.5 parallelism=4", "cpu=1 parallelism=2", "cpu=1 parallelism=4",
    ]
    assert runs[0].spec.cpu == "0.5" and runs[0].spec.parallelism == 2 and runs[0].spec.gather == ["--gather"]


def test_single_pod_runs_drop_the_gather_step():
    runs = expand(DATA, {"parallelism": ["1", "4"]})
    assert runs[0].spec.parallelism == 1 and runs[0].spec.gather is None
    assert runs[1].spec.gather == ["--gather"]


def test_expand_does_not_mutate_the_input():
    before = repr(DATA)
    expand(DATA, {"cpu": ["2"], "parallelism": ["1"]})
    assert repr(DATA) == before


def test_too_many_runs_is_refused():
    with pytest.raises(SpecError, match="limit"):
        expand(DATA, {"cpu": [str(i) for i in range(1, 6)], "retries": [str(i) for i in range(5)]})
    assert MAX_RUNS == 16


@pytest.mark.parametrize("grid", [{"cpu": ["abc"]}, {"memory": ["-1Gi"]}, {"parallelism": ["x"]}, {"parallelism": ["99"]}, {"cpu": ["0"]}])
def test_bad_values_fail_before_anything_runs(grid):
    with pytest.raises(SpecError):
        expand(DATA, grid)


def test_speedups_use_first_usable_time_as_baseline():
    assert speedups([50.0, 25.0, None, 10.0]) == [1.0, 2.0, None, 5.0]
    assert speedups([None, 20.0, 10.0]) == [None, 1.0, 2.0]
    assert speedups([None, None]) == [None, None]


@pytest.mark.parametrize("cpu", ["500m", "0.25", "2", "1.5"])
def test_spec_accepts_kubernetes_quantities(cpu):
    assert parse_spec({"name": "x", "image": "i", "resources": {"cpu": cpu}}).cpu == cpu


@pytest.mark.parametrize("res", [{"cpu": "fast"}, {"memory": "lots"}, {"cpu": "0"}, {"memory": "-5Mi"}])
def test_spec_rejects_bad_quantities(res):
    with pytest.raises(SpecError):
        parse_spec({"name": "x", "image": "i", "resources": res})
