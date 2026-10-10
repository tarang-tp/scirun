import sys
from typing import NoReturn

import typer
import urllib3
from kubernetes.client.rest import ApiException

from scirun.config import SpecError, load_spec
from scirun.kubernetes import jobs, logs as k8s_logs

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
def results(job_id: str) -> None:
    """Fetch result files for a job (not implemented until Stage 3)."""
    _fail("results is not implemented yet")
