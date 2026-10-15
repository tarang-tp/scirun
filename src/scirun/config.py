"""Experiment spec loading and validation."""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from kubernetes.utils import parse_quantity

# Job names become DNS-1123 labels; leave room for the "-<6 hex>" suffix.
_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_MAX_NAME_LEN = 40  # job id adds "-<6 hex>", and "-gather" must still fit in 63 chars
_MAX_PARALLELISM = 32
_KNOWN_KEYS = {"name", "image", "command", "args", "resources", "results", "parallelism", "retries", "gather", "verify_ignore"}


# Where job results live on the Mac, and where kind mounts that folder inside the node.
# scripts/create-cluster.sh sets up the mount; keep the node path in sync with it.
NODE_RESULTS_ROOT = "/scirun-results"


def results_root() -> Path:
    return Path(os.environ.get("SCIRUN_RESULTS_DIR", "~/.scirun/results")).expanduser()


class SpecError(ValueError):
    """The experiment spec is missing, malformed, or invalid."""


@dataclass(frozen=True)
class ExperimentSpec:
    name: str
    image: str
    command: list[str] | None = None
    args: list[str] = field(default_factory=list)
    cpu: str = "1"
    memory: str = "1Gi"
    results_path: str = "/results"
    parallelism: int = 1  # >1 runs an Indexed Job: one pod per tile
    retries: int = 2  # extra attempts per tile before it counts as failed
    gather: list[str] | None = None  # args for the follow-up job that combines tile outputs
    verify_ignore: list[str] | None = None  # output patterns `scirun verify` skips (default: timings, counters)


def load_spec(path: str | Path) -> ExperimentSpec:
    path = Path(path)
    try:
        text = path.read_text()
    except OSError as e:
        raise SpecError(f"cannot read spec {path}: {e.strerror}") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SpecError(f"invalid YAML in {path}: {e}") from e
    return parse_spec(data)


def parse_spec(data: Any) -> ExperimentSpec:
    if not isinstance(data, dict):
        raise SpecError("spec must be a YAML mapping")
    unknown = set(data) - _KNOWN_KEYS
    if unknown:
        raise SpecError(f"unknown spec field(s): {', '.join(sorted(map(str, unknown)))}")

    name = data.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name) or len(name) > _MAX_NAME_LEN:
        raise SpecError(
            f"'name' is required: lowercase letters, digits and '-', at most {_MAX_NAME_LEN} chars"
        )
    image = data.get("image")
    if not isinstance(image, str) or not image or image.startswith("-"):
        raise SpecError("'image' is required (and must not start with '-')")

    resources = _mapping(data, "resources")
    results = _mapping(data, "results")
    return ExperimentSpec(
        name=name,
        image=image,
        command=_str_list(data, "command") if "command" in data else None,
        args=_str_list(data, "args"),
        cpu=_quantity(resources, "cpu", "1"),
        memory=_quantity(resources, "memory", "1Gi"),
        results_path=str(results.get("path", "/results")),
        parallelism=_int(data, "parallelism", default=1, low=1, high=_MAX_PARALLELISM),
        retries=_int(data, "retries", default=2, low=0, high=10),
        gather=_gather_args(data),
        verify_ignore=_str_list(data, "verify_ignore") if "verify_ignore" in data else None,
    )


def _mapping(data: dict, key: str) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise SpecError(f"'{key}' must be a mapping")
    return value


def _str_list(data: dict, key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise SpecError(f"'{key}' must be a list")
    return [str(v) for v in value]


def _int(data: dict, key: str, *, default: int, low: int, high: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise SpecError(f"'{key}' must be an integer between {low} and {high}")
    return value


def _gather_args(data: dict) -> list[str] | None:
    if "gather" not in data:
        return None
    gather = data["gather"]
    if not isinstance(gather, dict) or set(gather) - {"args"}:
        raise SpecError("'gather' must be a mapping with only an 'args' list")
    if data.get("parallelism", 1) == 1:
        raise SpecError("'gather' only makes sense with 'parallelism' greater than 1")
    return _str_list(gather, "args")


def _quantity(resources: dict, key: str, default: str) -> str:
    """A Kubernetes quantity such as '500m', '0.5' or '1Gi', checked here rather than by the API later."""
    value = str(resources.get(key, default))
    try:
        ok = parse_quantity(value) > 0
    except (ValueError, ArithmeticError):
        ok = False
    if not ok:
        raise SpecError(f"'resources.{key}' must be a positive Kubernetes quantity (like 500m or 1Gi), got {value!r}")
    return value


def load_spec_data(path: str | Path) -> dict:
    """The raw YAML mapping of a spec file (for tools that tweak a spec before validating it)."""
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text())
    except OSError as e:
        raise SpecError(f"cannot read spec {path}: {e.strerror}") from e
    except yaml.YAMLError as e:
        raise SpecError(f"invalid YAML in {path}: {e}") from e
    if not isinstance(data, dict):
        raise SpecError("spec must be a YAML mapping")
    return data
