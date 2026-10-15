"""What produced a result: provenance at submit time, and content hashes of the outputs."""

import hashlib
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path

from scirun import __version__
from scirun.config import ExperimentSpec

# Not final results: timings and retry counters differ between identical runs, and tiles/ holds
# intermediate pieces that only exist in parallel runs (the combined output is what gets compared).
DEFAULT_IGNORE = ("metrics.json", "*.attempts", "tiles/*")

Runner = Callable[[list[str], Path | None], str | None]


def _run(cmd: list[str], cwd: Path | None = None) -> str | None:
    """Run a command without a shell; None if it is missing, slow or fails."""
    try:
        done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def collect(
    spec: ExperimentSpec,
    spec_path: Path | None = None,
    *,
    rerun_of: str | None = None,
    base: dict | None = None,
    run: Runner = _run,
) -> dict:
    """Provenance for a submission.

    For a rerun, `base` is the original's provenance: the spec file and git state it came from
    are inherited, because they describe where the spec came from, not this resubmission.
    """
    prov: dict = {
        "submitted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scirun_version": __version__,
        "image": {"name": spec.image, "id": run(["docker", "image", "inspect", "--format", "{{.Id}}", spec.image], None)},
        "rerun_of": rerun_of,
    }
    if base:
        prov.update({k: base.get(k) for k in ("spec_file", "spec_sha256", "git")})
    else:
        prov.update(_spec_file_info(spec_path, run))
    return prov


def _spec_file_info(spec_path: Path | None, run: Runner) -> dict:
    if spec_path is None or not spec_path.is_file():
        return {"spec_file": None, "spec_sha256": None, "git": None}
    folder = spec_path.resolve().parent
    git: dict | None = None
    # fsmonitor can execute a configured program; turn it off for this read-only look.
    base = ["git", "-c", "core.fsmonitor=false"]
    commit = run([*base, "rev-parse", "HEAD"], folder)
    if commit:
        porcelain = run([*base, "status", "--porcelain"], folder)
        git = {"commit": commit, "dirty": bool(porcelain)}
    return {
        "spec_file": str(spec_path),
        "spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
        "git": git,
    }


# --- output hashing and comparison ----------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def is_ignored(relpath: str, ignore: Iterable[str]) -> bool:
    return any(fnmatch(relpath, pattern) for pattern in ignore)


def output_manifest(root: Path, ignore: Iterable[str] = DEFAULT_IGNORE) -> tuple[dict[str, dict], int]:
    """({relative path: {size, sha256}}, number of ignored files) for every regular file under root."""
    ignore = tuple(ignore)
    manifest: dict[str, dict] = {}
    ignored = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if is_ignored(rel, ignore):
            ignored += 1
            continue
        manifest[rel] = {"size": path.stat().st_size, "sha256": _sha256(path)}
    return manifest, ignored


@dataclass(frozen=True)
class ManifestDiff:
    same: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    only_a: list[str] = field(default_factory=list)
    only_b: list[str] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not (self.changed or self.only_a or self.only_b)


def compare(a: dict[str, dict], b: dict[str, dict]) -> ManifestDiff:
    both = sorted(a.keys() & b.keys())
    return ManifestDiff(
        same=[p for p in both if a[p]["sha256"] == b[p]["sha256"]],
        changed=[p for p in both if a[p]["sha256"] != b[p]["sha256"]],
        only_a=sorted(a.keys() - b.keys()),
        only_b=sorted(b.keys() - a.keys()),
    )
