"""Terminal styling: a purple / green / grey palette with ● bullets and ⎿ detail lines."""

from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from scirun import __version__

PURPLE = "#a78bfa"
GREEN = "#4ade80"
GREY = "#8b8b99"
RED = "#f87171"
AMBER = "#fbbf24"

_theme = Theme({"accent": PURPLE, "ok": GREEN, "muted": GREY, "err": RED, "warn": AMBER})
console = Console(theme=_theme, highlight=False)
err_console = Console(theme=_theme, highlight=False, stderr=True)

PHASE_STYLE = {"Succeeded": "ok", "Running": "accent", "Pending": "muted", "Failed": "err", "gone": "muted"}


def bullet(*parts: str | Text, style: str = "accent") -> None:
    console.print(Text.assemble(("● ", style), *parts))


def detail(text: str, style: str = "muted") -> None:
    console.print(Text.assemble(("  ⎿  ", "muted"), (text, style)))


def error(message: str) -> None:
    err_console.print(Text.assemble(("✗ ", "err"), ("error: ", "err"), message))


def warning(message: str) -> None:
    err_console.print(Text.assemble(("! ", "warn"), ("warning: ", "warn"), message))


def phase(name: str) -> Text:
    return Text(name, style=PHASE_STYLE.get(name, "muted"))


def humanize_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def humanize_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def outputs_table(manifest: dict) -> Table:
    table = Table(box=None, pad_edge=False, show_edge=False, header_style="muted", padding=(0, 1))
    for h in ("FILE", "SIZE", "SHA-256"):
        table.add_column(h, no_wrap=h == "FILE", overflow="fold" if h == "FILE" else "ellipsis")
    for path, info in manifest.items():
        table.add_row(Text(path, style="accent"), humanize_bytes(info["size"]), Text(info["sha256"][:16], style="muted"))
    return table


def sweep_table(rows: list[list[str]]) -> Table:
    """Rows are [settings, job, phase, total time, speedup, compute time, cpu utilization, peak memory].

    The phase shows as the colour of the leading dot, which keeps the table within 80 columns.
    """
    table = Table(box=None, pad_edge=False, show_edge=False, header_style="muted", padding=(0, 1))
    for h in ("SETTINGS", "JOB", "TIME", "SPEEDUP", "COMPUTE", "CPU", "MEM"):
        table.add_column(h, no_wrap=h in ("SETTINGS", "JOB"), overflow="fold" if h in ("SETTINGS", "JOB") else "ellipsis")
    for settings, job, phase_name, total, speedup, compute, cpu, mem in rows:
        table.add_row(
            Text.assemble(("● ", PHASE_STYLE.get(phase_name, "muted")), (settings, "bold")),
            Text(job, style="accent"),
            total if total != "-" else Text(phase_name.lower(), style="err"),
            Text(speedup, style="ok" if speedup not in ("-", "1.0x") else "muted"),
            compute,
            Text(cpu, style="muted" if cpu == "-" else ""),
            Text(mem, style="muted" if mem == "-" else ""),
        )
    return table


def list_table(rows: list[list[str]]) -> Table:
    """Rows are [job, experiment, phase, age, run time]."""
    table = Table(box=None, pad_edge=False, show_edge=False, header_style="muted", padding=(0, 1))
    for i, h in enumerate(["JOB", "EXPERIMENT", "PHASE", "AGE", "RUN"]):
        table.add_column(h, no_wrap=i == 0, overflow="fold" if i == 0 else "ellipsis")
    for job, experiment, phase_name, age, run in rows:
        table.add_row(Text(job, style="accent"), Text(experiment, style="muted"), phase(phase_name), age, run)
    return table


_BAR_WIDTH = 20


def progress_line(status, label: str = "") -> Text:
    """One live line for a running job: a bar for parallel jobs, the phase for single-pod ones."""
    style = PHASE_STYLE.get(status.phase, "muted")
    line = Text.assemble(("● ", style), (status.job_id, "bold accent"), "  ")
    parts = []
    if status.tiles_total:
        filled = round(_BAR_WIDTH * status.tiles_done / status.tiles_total)
        bar_style = {"Succeeded": "ok", "Failed": "err"}.get(status.phase, "accent")
        line.append("█" * filled, style=bar_style)
        line.append("░" * (_BAR_WIDTH - filled), style="muted")
        line.append(f"  {status.tiles_done}/{status.tiles_total} {label or 'tiles'}")
        if status.tiles_failed:
            parts.append((f"{status.tiles_failed} failed", "err"))
        if status.retries:
            parts.append((f"{status.retries} {'crash' if status.retries == 1 else 'crashes'}", "warn"))
    else:
        line.append(status.phase, style=style)
        if label:
            line.append(f" {label}", style="muted")
    if status.duration_seconds is not None:
        parts.append((f"{status.duration_seconds:.0f}s", "muted"))
    for text, part_style in parts:
        line.append(" · ", style="muted")
        line.append(text, style=part_style)
    return line


_TILE_STYLE = {"done": "ok", "running": "accent", "retrying": "warn", "failed": "err", "pending": "muted"}


def tile_table(tiles) -> Table:
    table = Table(box=None, pad_edge=False, show_edge=False, header_style="muted", padding=(0, 1))
    for h in ("TILE", "STATE", "TRIES", "NODE", "TIME"):
        table.add_column(h)
    for t in tiles:
        state = Text(t.state, style=_TILE_STYLE[t.state])
        if t.slow:
            state.append(" slow", style="warn")
        table.add_row(
            Text(str(t.index), style="accent"),
            state,
            str(t.attempts) if t.attempts else "-",
            Text(t.node or "-", style="muted"),
            f"{t.seconds:.0f}s" if t.seconds is not None else "-",
        )
    return table


def banner(cluster: str | None, results_dir: str, commands: list[tuple[str, str]]) -> None:
    info = Group(
        Text.assemble(("◆ ", "accent"), "Welcome to ", ("SciRun", "bold accent")),
        Text("run scientific workloads on Kubernetes", style="muted"),
        Text(),
        Text.assemble(("cluster  ", "muted"), cluster or ("not found", "err")),
        Text.assemble(("results  ", "muted"), results_dir),
    )
    console.print(
        Panel(info, title=f"SciRun v{__version__}", title_align="left", border_style="accent", padding=(1, 2), expand=False)
    )
    table = Table.grid(padding=(0, 3))
    for usage, desc in commands:
        table.add_row(Text(usage, style="accent"), Text(desc, style="muted"))
    console.print(Text("  Commands", style="muted"))
    console.print(Padding(table, (0, 0, 0, 2)))


def report_table(header: list[str], body: list[list[str]]) -> Table:
    table = Table(box=None, pad_edge=False, show_edge=False, header_style="muted", padding=(0, 1))
    for i, h in enumerate(header):
        table.add_column(h, no_wrap=i == 0, overflow="fold" if i == 0 else "ellipsis")
    for row in body:
        cells = [Text(row[0], style="accent"), phase(row[1])]
        cells += [Text(c, style="muted" if c == "-" else "") for c in row[2:]]
        table.add_row(*cells)
    return table


def apply_help_theme() -> None:
    """Recolor Typer's rich-formatted --help to match."""
    from typer import rich_utils

    for name, value in {
        "STYLE_OPTION": f"bold {PURPLE}",
        "STYLE_SWITCH": f"bold {GREEN}",
        "STYLE_METAVAR": GREY,
        "STYLE_USAGE": PURPLE,
        "STYLE_COMMANDS_TABLE_FIRST_COLUMN": f"bold {PURPLE}",
        "STYLE_OPTIONS_PANEL_BORDER": GREY,
        "STYLE_COMMANDS_PANEL_BORDER": GREY,
        "STYLE_ERRORS_PANEL_BORDER": RED,
    }.items():
        if hasattr(rich_utils, name):
            setattr(rich_utils, name, value)
