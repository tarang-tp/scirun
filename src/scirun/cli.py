import typer

app = typer.Typer(help="Run and manage scientific workloads on Kubernetes (pre-alpha).")


@app.command()
def submit(spec: str) -> None:
    """Submit an experiment spec as a Kubernetes Job."""
    raise typer.Exit(_not_implemented("submit"))


@app.command()
def status(job_id: str) -> None:
    """Show the status of a job."""
    raise typer.Exit(_not_implemented("status"))


@app.command()
def logs(job_id: str) -> None:
    """Print logs for a job."""
    raise typer.Exit(_not_implemented("logs"))


@app.command()
def results(job_id: str) -> None:
    """Fetch result files for a job."""
    raise typer.Exit(_not_implemented("results"))


def _not_implemented(name: str) -> int:
    typer.echo(f"scirun {name}: not implemented yet", err=True)
    return 1
