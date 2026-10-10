"""Copy a job's result files out of the host-mounted results folder."""

import re
import shutil
from pathlib import Path

_JOB_ID_RE = re.compile(r"^[a-z0-9][-a-z0-9]*$")


class ResultsNotFound(Exception):
    pass


def valid_job_id(job_id: str) -> bool:
    return bool(_JOB_ID_RE.match(job_id))


def fetch_results(job_id: str, root: Path, dest: Path) -> list[Path]:
    """Copy root/<job_id> into dest and return the copied files (relative paths)."""
    if not valid_job_id(job_id):
        raise ResultsNotFound(f"invalid job id: {job_id!r}")
    src = root / job_id
    if not src.is_dir():
        raise ResultsNotFound(f"no results folder at {src}")
    files = sorted(p.relative_to(src) for p in src.rglob("*") if p.is_file())
    if not files:
        raise ResultsNotFound(f"results folder {src} is empty (job wrote no files)")
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return files


def delete_results(job_id: str, root: Path) -> bool:
    """Remove root/<job_id>. Returns whether anything was removed."""
    if not valid_job_id(job_id):
        return False
    target = root / job_id
    if not target.is_dir() or target.is_symlink():
        return False
    shutil.rmtree(target)
    return True
