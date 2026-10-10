"""Combine requested resources, Job timings and workload-reported usage into a table."""

from dataclasses import dataclass

from kubernetes.utils import parse_quantity

from scirun.kubernetes.jobs import JobStatus


@dataclass(frozen=True)
class ReportRow:
    job_id: str
    phase: str | None  # None if the Job no longer exists
    cpu_request: float | None
    memory_request: float | None  # bytes
    job_seconds: float | None  # Job start to finish, includes scheduling and image start
    run_seconds: float | None  # workload's own wall time
    cpu_seconds: float | None
    peak_memory: float | None  # bytes
    parallelism: int = 1  # pods per job; CPU and memory requests are per pod


def build_row(job_id: str, status: JobStatus | None, record: dict | None, metrics: dict | None) -> ReportRow:
    record, metrics = record or {}, metrics or {}
    return ReportRow(
        job_id=job_id,
        phase=status.phase if status else None,
        cpu_request=float(parse_quantity(record["cpu"])) if "cpu" in record else None,
        memory_request=float(parse_quantity(record["memory"])) if "memory" in record else None,
        job_seconds=status.duration_seconds if status else None,
        run_seconds=metrics.get("wall_seconds"),
        cpu_seconds=metrics.get("cpu_seconds"),
        peak_memory=metrics.get("peak_memory_bytes"),
        parallelism=int(record.get("parallelism", 1)),
    )


def cpu_utilization(row: ReportRow) -> float | None:
    """Fraction of the CPU allowance actually used while running."""
    if not (row.cpu_seconds is not None and row.run_seconds and row.cpu_request):
        return None
    return row.cpu_seconds / (row.run_seconds * row.cpu_request * row.parallelism)


def memory_utilization(row: ReportRow) -> float | None:
    if row.peak_memory is None or not row.memory_request:
        return None
    return row.peak_memory / row.memory_request


def _secs(v: float | None) -> str:
    return "-" if v is None else f"{v:.1f}s"


def _num(v: float | None, fmt: str) -> str:
    return "-" if v is None else format(v, fmt)


def _mem(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v / 2**30:.0f}Gi" if v >= 2**30 and v % 2**30 == 0 else f"{v / 2**20:.0f}Mi"


def _pct(v: float | None) -> str:
    return "" if v is None else f" ({v:.0%})"


def table_cells(rows: list[ReportRow]) -> tuple[list[str], list[list[str]]]:
    header = ["JOB", "PHASE", "CPU", "MEM", "TIME", "RUN", "CPU USED", "MEM USED"]
    body = [
        [
            r.job_id,
            r.phase or "gone",
            _num(r.cpu_request, "g") + (f"×{r.parallelism}" if r.parallelism > 1 else ""),
            _mem(r.memory_request),
            _secs(r.job_seconds),
            _secs(r.run_seconds),
            _secs(r.cpu_seconds) + _pct(cpu_utilization(r)),
            _mem(r.peak_memory) + _pct(memory_utilization(r)),
        ]
        for r in rows
    ]
    return header, body


def format_table(rows: list[ReportRow]) -> str:
    header, body = table_cells(rows)
    widths = [max(len(row[i]) for row in [header, *body]) for i in range(len(header))]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in [header, *body])
