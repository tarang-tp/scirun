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
