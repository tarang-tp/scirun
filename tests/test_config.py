import pytest

from scirun.config import SpecError, load_spec, parse_spec


def test_example_spec_loads():
    spec = load_spec("examples/mandelbrot.yaml")
    assert spec.name == "mandelbrot"
    assert spec.image == "scirun/mandelbrot:0.1"
    assert spec.args[:2] == ["--width", "2000"]  # ints are stringified
    assert spec.cpu == "1" and spec.memory == "1Gi"


def test_defaults():
    spec = parse_spec({"name": "x", "image": "img"})
    assert spec.args == [] and spec.command is None
    assert spec.results_path == "/results"


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"image": "img"},
        {"name": "x"},
        {"name": "Bad_Name", "image": "img"},
        {"name": "a" * 51, "image": "img"},
        {"name": "x", "image": "img", "typo": 1},
        {"name": "x", "image": "img", "args": "not-a-list"},
        {"name": "x", "image": "img", "resources": "big"},
    ],
)
def test_invalid_specs(data):
    with pytest.raises(SpecError):
        parse_spec(data)


def test_missing_file():
    with pytest.raises(SpecError):
        load_spec("does-not-exist.yaml")
