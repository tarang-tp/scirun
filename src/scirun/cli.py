import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

import typer
import urllib3
from kubernetes.client.rest import ApiException
from rich.live import Live

from scirun import ui
from scirun.config import ExperimentSpec, SpecError, load_spec, results_root
from scirun.kubernetes import jobs
from scirun.kubernetes import logs as k8s_logs
from scirun.records import (
    delete_record,
    load_metrics,
    load_record,
    save_record,
    spec_from_record,
)
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
    ("gather <job-id>", "(re)run the combine step of a parallel job"),
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


_DONE = ("Succeeded", "Failed")


@app.command()
def submit(
    spec: str = typer.Argument(..., help="Path to an experiment YAML spec"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Watch progress, then run the gather step"),
) -> None:
    """Submit an experiment spec as a Kubernetes Job."""
    parsed = _guard(load_spec, spec)
    job_id = _guard(jobs.submit_job, parsed)
    try:
        save_record(job_id, parsed)
    except OSError as e:
        ui.warning(f"could not save job record ({e.strerror}); `report` and `gather` will lack the spec")
    if not sys.stdout.isatty() and not follow:
        typer.echo(job_id)  # plain, so `ID=$(scirun submit ...)` keeps working
        return
    ui.bullet("Submitted ", (job_id, "bold accent"), style="ok")
    size = f"{parsed.parallelism} tiles · " if parsed.parallelism > 1 else ""
    ui.detail(f"{parsed.name} · {parsed.image} · {size}{parsed.cpu} CPU · {parsed.memory}")
    if not follow:
        ui.detail(f"track it with: scirun status {job_id}")
        return
    _follow_to_the_end(parsed, job_id)


def _follow(job_id: str, label: str = "") -> jobs.JobStatus:
    """Poll a job until it finishes, drawing a live progress line."""
    status = _guard(jobs.get_status, job_id)
    if not ui.console.is_terminal:
        last = None
        while True:
            line = ui.progress_line(status, label).plain
            if line != last:
                typer.echo(line)
                last = line
            if status.phase in _DONE:
                return status
            time.sleep(1)
            status = _guard(jobs.get_status, job_id)
    with Live(ui.progress_line(status, label), console=ui.console, refresh_per_second=4) as live:
        while status.phase not in _DONE:
            time.sleep(1)
            status = _guard(jobs.get_status, job_id)
            live.update(ui.progress_line(status, label))
    return status


def _follow_to_the_end(spec: ExperimentSpec, job_id: str) -> None:
    try:
        status = _follow(job_id, "tiles")
        if status.phase == "Failed":
            ui.detail(status.detail or "job failed", style="err")
            ui.detail(f"inspect with: scirun status {job_id} --tiles   ·   logs: scirun logs {job_id}")
            raise typer.Exit(1)
        if spec.parallelism > 1 and spec.gather:
            _run_gather(spec, job_id)
        else:
            ui.detail(f"fetch output with: scirun results {job_id}")
    except KeyboardInterrupt:
        ui.detail(f"stopped watching; the job keeps running. Resume with: scirun status {job_id}", style="warn")
        raise typer.Exit(130) from None


def _run_gather(spec: ExperimentSpec, job_id: str) -> None:
    name = _guard(jobs.submit_gather, spec, job_id)
    status = _follow(name, "gather")
    if status.phase == "Failed":
        ui.detail(status.detail or "gather failed", style="err")
        ui.detail(f"see: scirun logs {name}   ·   retry with: scirun gather {job_id}")
        raise typer.Exit(1)
    ui.detail(f"fetch output with: scirun results {job_id}")


@app.command()
def gather(job_id: str) -> None:
    """(Re)run the combine step of a finished parallel job. Safe to repeat."""
    spec = spec_from_record(load_record(job_id))
    if spec is None or not spec.gather:
        _fail(f"no gather step recorded for {job_id}")

    def tiles_phase() -> str | None:
        try:
            return jobs.get_status(job_id).phase
        except jobs.JobNotFound:
            return None  # job was cleaned up; the gather step checks the tile files itself

    phase = _guard(tiles_phase)
    if phase not in (None, "Succeeded"):
        _fail(f"tiles are not finished (job is {phase}); see `scirun status {job_id} --tiles`")
    try:
        _run_gather(spec, job_id)
    except KeyboardInterrupt:
        ui.detail("stopped watching; the gather job keeps running", style="warn")
        raise typer.Exit(130) from None


@app.command()
def status(
    job_id: str,
    tiles: bool = typer.Option(False, "--tiles", "-t", help="Show one row per tile (parallel jobs)"),
) -> None:
    """Show the status of a job."""
    s = _guard(jobs.get_status, job_id)
    ui.bullet((s.job_id, "bold accent"), "  ", ui.phase(s.phase), style=ui.PHASE_STYLE[s.phase])
    if s.tiles_total:
        summary = [f"{s.tiles_done}/{s.tiles_total} tiles done"]
        if s.tiles_active:
            summary.append(f"{s.tiles_active} running")
        if s.tiles_failed:
            summary.append(f"{s.tiles_failed} failed")
        if s.retries:
            summary.append(f"{s.retries} {'crash' if s.retries == 1 else 'crashes'}")
        ui.detail(" · ".join(summary))
    timing = []
    if s.start_time:
        timing.append(f"started {s.start_time.astimezone():%H:%M:%S}")
    if s.duration_seconds is not None:
        timing.append(f"{'ran' if s.end_time else 'running'} {s.duration_seconds:.0f}s")
    if timing:
        ui.detail(" · ".join(timing))
    if s.detail:
        ui.detail(s.detail, style="err")
    if s.tiles_total:
        _parallel_extras(s, tiles)


def _parallel_extras(s: jobs.JobStatus, show_table: bool) -> None:
    tile_rows = _guard(jobs.get_tiles, s.job_id)
    for t in (t for t in tile_rows if t.slow):
        ui.detail(f"tile {t.index} looks slow ({t.seconds:.0f}s while finished tiles took much less)", style="warn")
    spec = spec_from_record(load_record(s.job_id))
    if spec and spec.gather and s.phase == "Succeeded":
        try:
            g = jobs.get_status(jobs.gather_job_id(s.job_id))
            ui.detail(f"gather: {g.phase}", style=ui.PHASE_STYLE[g.phase])
        except jobs.JobNotFound:
            ui.detail(f"gather has not run; start it with: scirun gather {s.job_id}", style="warn")
    if show_table:
        ui.console.print()
        ui.console.print(ui.tile_table(tile_rows))


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
    spec = spec_from_record(load_record(job_id))
    if spec and spec.gather and (problem := _gather_problem(job_id)):
        ui.warning(f"the gather step {problem}; output may be only tiles (scirun gather {job_id})")
    dest = out or Path("results") / job_id
    try:
        files = fetch_results(job_id, results_root(), dest)
    except ResultsNotFound as e:
        _fail(f"{e}. Was the cluster created with scripts/create-cluster.sh?")
    noun = "file" if len(files) == 1 else "files"
    ui.bullet(f"Copied {len(files)} {noun} to ", (str(dest), "bold accent"), style="ok")
    for entry in _summarize_files(files):
        ui.detail(entry if entry.endswith(")") else str(dest / entry))


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



def _gather_problem(job_id: str) -> str | None:
    """Why a parallel job's combined output may be missing, or None if it is fine or unknowable."""
    try:
        phase = jobs.get_status(jobs.gather_job_id(job_id)).phase
    except jobs.JobNotFound:
        return "has not run"
    except Exception:  # noqa: BLE001 - only a courtesy warning, never block `results` on it
        return None
    return None if phase == "Succeeded" else "has not succeeded"


def _summarize_files(files: list[Path]) -> list[str]:
    """Top-level files by name; each subfolder collapsed to 'name/ (N files)'."""
    entries: list[str] = []
    folders: dict[str, int] = {}
    for f in files:
        if len(f.parts) == 1:
            entries.append(str(f))
        else:
            folders[f.parts[0]] = folders.get(f.parts[0], 0) + 1
    entries += [f"{name}/ ({n} {'file' if n == 1 else 'files'})" for name, n in folders.items()]
    return entries
