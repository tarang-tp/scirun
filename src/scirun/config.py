"""Experiment spec loading and validation."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Job names become DNS-1123 labels; leave room for the "-<6 hex>" suffix.
_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_MAX_NAME_LEN = 50
_KNOWN_KEYS = {"name", "image", "command", "args", "resources", "results"}


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
    if not isinstance(image, str) or not image:
        raise SpecError("'image' is required")

    resources = _mapping(data, "resources")
    results = _mapping(data, "results")
    return ExperimentSpec(
        name=name,
        image=image,
        command=_str_list(data, "command") if "command" in data else None,
        args=_str_list(data, "args"),
        cpu=str(resources.get("cpu", "1")),
        memory=str(resources.get("memory", "1Gi")),
        results_path=str(results.get("path", "/results")),
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
