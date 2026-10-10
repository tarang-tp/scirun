import sys
from pathlib import Path
from typing import NoReturn

import typer
import urllib3
from kubernetes.client.rest import ApiException

from scirun.config import SpecError, load_spec, results_root
from scirun.kubernetes import jobs, logs as k8s_logs
from scirun.records import load_metrics, load_record, save_record
from scirun.report import ReportRow, build_row, format_table
from scirun.results import ResultsNotFound, fetch_results

app = typer.Typer(help="Run and manage scientific workloads on Kubernetes (pre-alpha).")


def _fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)


def _guard(fn, *args, **kwargs):
    """Call fn, converting expected errors into a clean CLI failure."""
    try:
        return fn(*args, **kwargs)
    except SpecError as e:
        _fail(str(e))
    except jobs.JobNotFound as e:
        _fail(f"job not found: {e}")
    except urllib3.exceptions.HTTPError:
        _fail("cannot reach the Kubernetes cluster (is Docker/kind running?)")
    except ApiException as e:
        _fail(f"Kubernetes API error {e.status}: {e.reason}")
    except (RuntimeError, TimeoutError) as e:
        _fail(str(e))


@app.command()
def submit(spec: str = typer.Argument(..., help="Path to an experiment YAML spec")) -> None:
    """Submit an experiment spec as a Kubernetes Job."""
    parsed = _guard(load_spec, spec)
    job_id = _guard(jobs.submit_job, parsed)
    try:
        save_record(job_id, parsed)
    except OSError as e:
        typer.echo(f"warning: could not save job record ({e.strerror}); `report` will lack requests", err=True)
    typer.echo(job_id)


@app.command()
def status(job_id: str) -> None:
    """Show the status of a job."""
    s = _guard(jobs.get_status, job_id)
    typer.echo(f"Job:      {s.job_id}")
    typer.echo(f"Phase:    {s.phase}")
    if s.start_time:
        typer.echo(f"Started:  {s.start_time.astimezone():%Y-%m-%d %H:%M:%S}")
    if s.duration_seconds is not None:
        label = "Duration" if s.end_time else "Running"
        typer.echo(f"{label + ':':<9} {s.duration_seconds:.0f}s")
    if s.detail:
        typer.echo(f"Detail:   {s.detail}")


@app.command()
def logs(
    job_id: str,
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream logs until the job ends"),
) -> None:
    """Print logs for a job."""

    def run() -> None:
        for chunk in k8s_logs.stream_logs(job_id, follow=follow):
            sys.stdout.write(chunk)
            sys.stdout.flush()

    _guard(run)


@app.command()
def report(job_ids: list[str] = typer.Argument(..., help="One or more job IDs to compare")) -> None:
    """Compare requested resources, timings and measured usage across jobs."""

    def lookup(job_id: str) -> ReportRow:
        try:
            status = jobs.get_status(job_id)
        except jobs.JobNotFound:
            status = None
        row = build_row(job_id, status, load_record(job_id), load_metrics(job_id))
        if status is None and row.cpu_request is None and row.run_seconds is None:
            raise jobs.JobNotFound(job_id)
        return row

    rows = [_guard(lookup, j) for j in job_ids]
    typer.echo(format_table(rows))


@app.command()
def results(
    job_id: str,
    out: Path | None = typer.Option(None, "--out", "-o", help="Destination (default: ./results/<job-id>)"),
) -> None:
    """Copy a finished job's result files to local disk."""
    def lookup_phase() -> str | None:
        try:
            return jobs.get_status(job_id).phase
        except jobs.JobNotFound:
            return None  # Job was cleaned up (TTL); its results may still be on disk

    phase = _guard(lookup_phase)
    if phase in ("Pending", "Running"):
        _fail(f"job is {phase}; wait for it to finish (see `scirun status {job_id}`)")
    if phase == "Failed":
        typer.echo("warning: job failed; results may be partial", err=True)
    dest = out or Path("results") / job_id
    try:
        files = fetch_results(job_id, results_root(), dest)
    except ResultsNotFound as e:
        _fail(f"{e}. Was the cluster created with scripts/create-cluster.sh?")
    for f in files:
        typer.echo(dest / f)
