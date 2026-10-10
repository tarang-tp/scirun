"""Run the Mandelbrot workload as a subprocess, the way a pod would."""

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

MAIN = Path(__file__).parent.parent / "workloads" / "mandelbrot" / "main.py"
SMALL = ["--width", "60", "--height", "37", "--max-iter", "40"]  # 37 rows: strips are uneven


def run(out: Path, *args: str, tiles: int = 0, index: int | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SCIRUN_", "JOB_"))}
    if tiles:
        env["SCIRUN_TILE_COUNT"] = str(tiles)
    if index is not None:
        env["JOB_COMPLETION_INDEX"] = str(index)
    env["NODE_NAME"] = "test-node"
    return subprocess.run([sys.executable, str(MAIN), *SMALL, "--out", str(out), *args],
                          env=env, capture_output=True, text=True, check=False)


def test_tiles_plus_gather_equal_single_render(tmp_path):
    whole = tmp_path / "whole" / "m.png"
    assert run(whole).returncode == 0

    tiled = tmp_path / "tiled" / "m.png"
    for i in range(5):
        assert run(tiled, tiles=5, index=i).returncode == 0
    assert run(tiled, "--gather", tiles=5).returncode == 0

    assert np.array_equal(np.array(Image.open(whole)), np.array(Image.open(tiled)))


def test_gather_aggregates_metrics_and_nodes(tmp_path):
    out = tmp_path / "m.png"
    for i in range(3):
        run(out, tiles=3, index=i)
    run(out, "--gather", tiles=3)
    import json
    m = json.loads((tmp_path / "metrics.json").read_text())
    assert m["tiles"] == 3 and m["nodes"] == ["test-node"] and m["retried_tiles"] == 0
    assert m["cpu_seconds"] > 0


def test_gather_refuses_when_tiles_missing(tmp_path):
    out = tmp_path / "m.png"
    run(out, tiles=4, index=0)
    r = run(out, "--gather", tiles=4)
    assert r.returncode == 3 and "missing tiles [1, 2, 3]" in r.stdout
    assert not out.exists()


def test_injected_failure_then_retry_succeeds(tmp_path):
    out = tmp_path / "m.png"
    first = run(out, "--fail-tiles", "1", "--fail-attempts", "1", tiles=2, index=1)
    assert first.returncode == 1 and "injected failure" in first.stdout
    assert not (tmp_path / "tiles" / "tile-001.npy").exists()  # nothing half-written

    retry = run(out, "--fail-tiles", "1", "--fail-attempts", "1", tiles=2, index=1)
    assert retry.returncode == 0
    run(out, tiles=2, index=0)
    run(out, "--gather", tiles=2)
    import json
    assert json.loads((tmp_path / "metrics.json").read_text())["retried_tiles"] == 1


def test_rerunning_a_finished_tile_is_safe(tmp_path):
    out = tmp_path / "m.png"
    run(out, tiles=2, index=0)
    before = (tmp_path / "tiles" / "tile-000.npy").read_bytes()
    assert run(out, tiles=2, index=0).returncode == 0
    assert (tmp_path / "tiles" / "tile-000.npy").read_bytes() == before
    assert not [p for p in (tmp_path / "tiles").iterdir() if p.name.endswith(".tmp")]


def test_gather_without_tile_count_is_an_error(tmp_path):
    assert run(tmp_path / "m.png", "--gather").returncode != 0


@pytest.mark.parametrize("n", [1, 2, 5, 37])
def test_strips_cover_every_row_exactly_once(n):
    sys.path.insert(0, str(MAIN.parent))
    try:
        import main as workload
    finally:
        sys.path.pop(0)
    rows = np.concatenate([workload.tile_rows(37, n, i) for i in range(n)])
    assert rows.tolist() == list(range(37))
