import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import typer
import urllib3
from kubernetes.client.rest import ApiException

from scirun import ui
from scirun.config import SpecError, load_spec, results_root
from scirun.kubernetes import jobs
from scirun.kubernetes import logs as k8s_logs
from scirun.records import delete_record, load_metrics, load_record, save_record
from scirun.report import ReportRow, build_row, table_cells
from scirun.results import ResultsNotFound, delete_results, fetch_results

ui.apply_help_theme()
app = typer.Typer(
    help="Run and manage scientific workloads on Kubernetes (pre-alpha).",
    no_args_is_help=False,
    add_completion=False,
)

_COMMANDS = [
    ("submit <spec>", "create a Job from an experiment spec"),
    ("list", "show jobs on the cluster, newest first"),
    ("status <job-id>", "phase, timing and failure detail"),
    ("logs <job-id> [-f]", "print or stream container logs"),
    ("results <job-id>", "copy output files to local disk"),
    ("report <job-id>...", "compare resources, timings and usage"),
    ("delete <job-id>...", "remove jobs (and optionally results)"),
]


def _fail(message: str) -> NoReturn:
    ui.error(message)
    raise typer.Exit(1)


def _guard(fn, *args, **kwargs):
    """Call fn, converting expected errors into a clean CLI failure."""
    try:
        return fn(*args, **kwargs)
    except SpecError as e:
        _fail(str(e))
    except jobs.JobNotFound as e:
        _fail(f"job not found: {e}")
    except jobs.NotManaged as e:
        _fail(f"{e} is not a SciRun job; refusing to touch it")
    except urllib3.exceptions.HTTPError:
        _fail("cannot reach the Kubernetes cluster (is Docker/kind running?)")
    except ApiException as e:
        _fail(f"Kubernetes API error {e.status}: {e.reason}")
    except (RuntimeError, TimeoutError) as e:
        _fail(str(e))


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Run and manage scientific workloads on Kubernetes (pre-alpha)."""
    if ctx.invoked_subcommand is None:
        ui.banner(jobs.current_context(), str(results_root()), _COMMANDS)


@app.command()
def submit(spec: str = typer.Argument(..., help="Path to an experiment YAML spec")) -> None:
    """Submit an experiment spec as a Kubernetes Job."""
    parsed = _guard(load_spec, spec)
    job_id = _guard(jobs.submit_job, parsed)
    try:
        save_record(job_id, parsed)
    except OSError as e:
        ui.warning(f"could not save job record ({e.strerror}); `report` will lack requests")
    if not sys.stdout.isatty():
        typer.echo(job_id)  # plain, so `ID=$(scirun submit ...)` keeps working
        return
    ui.bullet("Submitted ", (job_id, "bold accent"), style="ok")
    ui.detail(f"{parsed.name} · {parsed.image} · {parsed.cpu} CPU · {parsed.memory}")
    ui.detail(f"track it with: scirun status {job_id}")


@app.command()
def status(job_id: str) -> None:
    """Show the status of a job."""
    s = _guard(jobs.get_status, job_id)
    ui.bullet((s.job_id, "bold accent"), "  ", ui.phase(s.phase), style=ui.PHASE_STYLE[s.phase])
    timing = []
    if s.start_time:
        timing.append(f"started {s.start_time.astimezone():%H:%M:%S}")
    if s.duration_seconds is not None:
        timing.append(f"{'ran' if s.end_time else 'running'} {s.duration_seconds:.0f}s")
    if timing:
        ui.detail(" · ".join(timing))
    if s.detail:
        ui.detail(s.detail, style="err")


@app.command()
def logs(
    job_id: str,
    follow: bool = typer.Option(False, "--follow", "-f", help="Stream logs until the job ends"),
) -> None:
    """Print logs for a job."""

    def run() -> None:
        for chunk in k8s_logs.stream_logs(job_id, follow=follow):
            sys.stdout.write(chunk)  # raw on purpose: safe to pipe
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
    ui.console.print(ui.report_table(*table_cells(rows)))


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
        ui.warning("job failed; results may be partial")
    dest = out or Path("results") / job_id
    try:
        files = fetch_results(job_id, results_root(), dest)
    except ResultsNotFound as e:
        _fail(f"{e}. Was the cluster created with scripts/create-cluster.sh?")
    noun = "file" if len(files) == 1 else "files"
    ui.bullet(f"Copied {len(files)} {noun} to ", (str(dest), "bold accent"), style="ok")
    for f in files:
        ui.detail(str(dest / f))


@app.command(name="list")
def list_() -> None:
    """Show SciRun jobs on the cluster, newest first."""
    statuses = _guard(jobs.list_jobs)
    if not statuses:
        ui.bullet("No jobs yet", style="muted")
        ui.detail("create one with: scirun submit <spec>")
        return
    now = datetime.now(UTC)
    rows = [
        [
            s.job_id,
            s.experiment or "-",
            s.phase,
            ui.humanize_age((now - s.created).total_seconds()) if s.created else "-",
            f"{s.duration_seconds:.0f}s" if s.duration_seconds is not None else "-",
        ]
        for s in statuses
    ]
    ui.console.print(ui.list_table(rows))


@app.command()
def delete(
    job_ids: list[str] = typer.Argument(..., help="One or more job IDs"),
    with_results: bool = typer.Option(False, "--results", help="Also delete result files and the saved record"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation for --results"),
) -> None:
    """Delete jobs from the cluster. Result files are kept unless --results is given."""
    if with_results and not yes:
        typer.confirm(f"Permanently delete results for {len(job_ids)} job(s)?", abort=True)

    for job_id in job_ids:

        def remove(job_id: str = job_id) -> bool:
            try:
                jobs.delete_job(job_id)
            except jobs.JobNotFound:
                return False  # already gone; fine if we are cleaning up local files
            return True

        removed_job = _guard(remove)
        if not removed_job and not with_results:
            _fail(f"job not found: {job_id}")
        removed_results = False
        if with_results:
            removed_results = delete_results(job_id, results_root())
            removed_record = delete_record(job_id)
            if not (removed_job or removed_results or removed_record):
                _fail(f"nothing to delete for {job_id}")
        ui.bullet("Deleted ", (job_id, "bold accent"), style="ok")
        if removed_job:
            ui.detail("job removed from the cluster")
        if with_results and (removed_results or removed_record):
            ui.detail("results and record removed")
        elif not with_results and (results_root() / job_id).is_dir():
            ui.detail("results kept (use --results to remove them)")

