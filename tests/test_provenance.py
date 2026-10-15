from pathlib import Path

import pytest

from scirun.config import ExperimentSpec, SpecError, parse_spec
from scirun.provenance import (
    DEFAULT_IGNORE,
    collect,
    compare,
    is_ignored,
    output_manifest,
)
from scirun.records import load_record, save_record

SPEC = ExperimentSpec(name="demo", image="img:1")


def fake_runner(answers):
    calls = []

    def run(cmd, cwd=None):
        calls.append(cmd)
        for key, value in answers.items():
            if key in " ".join(cmd):
                return value
        return None

    run.calls = calls
    return run


# --- collect -------------------------------------------------------------------------------

def test_collect_records_git_image_and_spec_hash(tmp_path):
    spec_file = tmp_path / "s.yaml"
    spec_file.write_text("name: demo\n")
    run = fake_runner({"image inspect": "sha256:abc", "rev-parse": "deadbeef", "status": " M file"})
    prov = collect(SPEC, spec_file, run=run)
    assert prov["image"] == {"name": "img:1", "id": "sha256:abc"}
    assert prov["git"] == {"commit": "deadbeef", "dirty": True}
    assert len(prov["spec_sha256"]) == 64 and prov["spec_file"] == str(spec_file)
    assert prov["rerun_of"] is None and prov["scirun_version"]
    assert all(c[:3] != ["git", "status"] or "core.fsmonitor=false" in c for c in run.calls)


def test_collect_without_git_or_docker(tmp_path):
    spec_file = tmp_path / "s.yaml"
    spec_file.write_text("x")
    prov = collect(SPEC, spec_file, run=fake_runner({}))
    assert prov["git"] is None and prov["image"]["id"] is None


def test_collect_for_rerun_inherits_origin_of_the_spec():
    base = {"spec_file": "a.yaml", "spec_sha256": "f" * 64, "git": {"commit": "c0ffee", "dirty": False}}
    prov = collect(SPEC, None, rerun_of="demo-abc123", base=base, run=fake_runner({"image inspect": "sha256:new"}))
    assert prov["rerun_of"] == "demo-abc123"
    assert prov["spec_file"] == "a.yaml" and prov["git"]["commit"] == "c0ffee"
    assert prov["image"]["id"] == "sha256:new"  # the image is looked up fresh


def test_record_roundtrip_keeps_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("SCIRUN_RESULTS_DIR", str(tmp_path))
    save_record("demo-abc123", SPEC, {"rerun_of": "x"})
    assert load_record("demo-abc123")["provenance"] == {"rerun_of": "x"}


# --- manifests -----------------------------------------------------------------------------

def _make(root: Path, files: dict[str, str]):
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def test_manifest_hashes_files_and_skips_ignored(tmp_path):
    _make(tmp_path, {"out.png": "pixels", "metrics.json": "{1}", "tiles/t0.npy": "a", "tiles/t0.json": "{}", "tiles/t0.attempts": "2"})
    manifest, ignored = output_manifest(tmp_path)
    assert sorted(manifest) == ["out.png"] and ignored == 4
    assert manifest["out.png"]["size"] == 6 and len(manifest["out.png"]["sha256"]) == 64


def test_manifest_ignores_symlinks(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link.txt").symlink_to(outside)
    assert output_manifest(root) == ({}, 0)


@pytest.mark.parametrize("rel,expected", [("metrics.json", True), ("tiles/tile-001.json", True), ("tiles/tile-001.attempts", True),
                                           ("tiles/tile-001.npy", True), ("out.png", False), ("sub/metrics.json", False)])
def test_default_ignore_patterns(rel, expected):
    assert is_ignored(rel, DEFAULT_IGNORE) is expected


def test_compare_identical_and_different(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _make(a, {"x": "1", "y": "2", "only-a": "?"})
    _make(b, {"x": "1", "y": "CHANGED", "only-b": "?"})
    diff = compare(output_manifest(a)[0], output_manifest(b)[0])
    assert diff.same == ["x"] and diff.changed == ["y"]
    assert diff.only_a == ["only-a"] and diff.only_b == ["only-b"] and not diff.identical
    assert compare(output_manifest(a)[0], output_manifest(a)[0]).identical


# --- spec ----------------------------------------------------------------------------------

def test_spec_verify_ignore_and_image_check():
    assert parse_spec({"name": "x", "image": "i", "verify_ignore": ["*.log"]}).verify_ignore == ["*.log"]
    assert parse_spec({"name": "x", "image": "i"}).verify_ignore is None
    with pytest.raises(SpecError):
        parse_spec({"name": "x", "image": "--help"})
