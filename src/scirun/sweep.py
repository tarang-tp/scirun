"""Expand one spec into a grid of runs for `scirun sweep`."""

import copy
import itertools
from dataclasses import dataclass

from scirun.config import ExperimentSpec, SpecError, parse_spec

# What a sweep may vary: resources and parallelism, never the workload's own arguments.
VARIABLES = ("cpu", "memory", "parallelism", "retries")
MAX_RUNS = 16


@dataclass(frozen=True)
class Run:
    settings: dict[str, str]  # what differs in this run, e.g. {"parallelism": "4"}
    spec: ExperimentSpec

    @property
    def label(self) -> str:
        return " ".join(f"{k}={v}" for k, v in self.settings.items())


def parse_vary(options: list[str]) -> dict[str, list[str]]:
    """['cpu=0.5,1', 'parallelism=2,4'] -> {'cpu': ['0.5', '1'], 'parallelism': ['2', '4']}."""
    grid: dict[str, list[str]] = {}
    for option in options:
        key, sep, raw = option.partition("=")
        key = key.strip()
        values = list(dict.fromkeys(v.strip() for v in raw.split(",") if v.strip()))
        if not sep or not values:
            raise SpecError(f"--vary expects name=value1,value2 (got {option!r})")
        if key not in VARIABLES:
            raise SpecError(f"cannot vary {key!r}; choose from: {', '.join(VARIABLES)}")
        if key in grid:
            raise SpecError(f"--vary {key} given twice")
        grid[key] = values
    if not grid:
        raise SpecError("nothing to sweep: add at least one --vary name=v1,v2")
    return grid


def expand(data: dict, grid: dict[str, list[str]]) -> list[Run]:
    """One validated spec per combination, first variable varying slowest."""
    total = 1
    for values in grid.values():
        total *= len(values)
    if total > MAX_RUNS:
        raise SpecError(f"{total} runs is more than the limit of {MAX_RUNS}; narrow the grid")

    runs = []
    for combo in itertools.product(*grid.values()):
        settings = dict(zip(grid, combo, strict=True))
        runs.append(Run(settings, _apply(data, settings)))
    return runs


def _apply(data: dict, settings: dict[str, str]) -> ExperimentSpec:
    data = copy.deepcopy(data)
    for key, value in settings.items():
        if key in ("cpu", "memory"):
            data.setdefault("resources", {})[key] = value
        else:
            try:
                data[key] = int(value)
            except ValueError:
                raise SpecError(f"{key} must be a whole number, got {value!r}") from None
    if data.get("parallelism", 1) == 1:
        data.pop("gather", None)  # a single pod has nothing to gather
    return parse_spec(data)


def speedups(times: list[float | None]) -> list[float | None]:
    """Each time relative to the first usable one (1.0 for that row); None where no time exists."""
    base = next((t for t in times if t), None)
    return [base / t if base and t else None for t in times]
