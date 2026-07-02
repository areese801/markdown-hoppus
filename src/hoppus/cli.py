"""
Typer CLI application shared by the three interchangeable console commands
``hoppus``, ``mark``, and ``hop`` (spec §1, §10).

Fully implemented here: ``vaults`` (list vaults under the Vaults Root) and
the dispatch/skeleton for every always-available subcommand. Subcommands
owned by later stories (``find``, ``new``, ``daily``, ``capture``,
``preview``, ``index``/``reindex``, ``audit``, ``doctor``, ``mcp``) and the
TUI launch are registered as clearly-marked stubs so ``--help`` shows the
full command tree.
"""

from pathlib import Path

import typer

from hoppus import __version__
from hoppus.config import load_config
from hoppus.vault import Vault, discover_vaults

NOT_IMPLEMENTED_SUFFIX = "not yet implemented"

app = typer.Typer(
    name="hoppus",
    help=(
        "A terminal-first, TUI Markdown knowledge base — Obsidian-compatible, "
        "standalone. The `hoppus`, `mark`, and `hop` commands are "
        "interchangeable."
    ),
    add_completion=False,
)


def _vaults_root() -> Path:
    """
    Return the configured Vaults Root as an expanded path.

    Returns:
        The ``vaults_root`` value from the merged config (spec §12), with
        ``~`` expanded.
    """
    config = load_config()
    return Path(config["vaults_root"]).expanduser()


def _discover_or_exit(vaults_root: Path) -> list[Vault]:
    """
    Discover vaults under ``vaults_root``, exiting with code 1 on failure.

    Args:
        vaults_root: The directory expected to contain vaults.

    Returns:
        The discovered vaults (possibly empty).

    Raises:
        typer.Exit: With code 1 if the Vaults Root is missing or not a
            directory.
    """
    try:
        return discover_vaults(vaults_root)
    except (FileNotFoundError, NotADirectoryError) as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error


def _stub(feature: str) -> None:
    """
    Print the standard placeholder notice for an unimplemented subcommand.

    Args:
        feature: Human-readable name of the feature being stubbed.
    """
    typer.echo(f"{feature}: {NOT_IMPLEMENTED_SUFFIX}")


@app.callback(invoke_without_command=True)
def cli(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Show the version and exit."),
) -> None:
    """
    Launch the TUI when invoked with no subcommand (spec §10).
    """
    if version:
        typer.echo(f"markdown-hoppus {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        _stub("TUI")


@app.command()
def vaults() -> None:
    """
    List vaults under the Vaults Root (spec §10).
    """
    vaults_root = _vaults_root()
    discovered = _discover_or_exit(vaults_root)
    if not discovered:
        typer.echo(f"No vaults found under {vaults_root}")
        return
    for vault in discovered:
        typer.echo(f"{vault.name}\t{vault.path}")


@app.command(name="open")
def open_(
    vault: str = typer.Argument(
        None, help="Vault name to open. Defaults to the configured default vault."
    ),
) -> None:
    """
    Launch the TUI on a named vault (spec §10). TUI launch is stubbed.
    """
    config = load_config()
    vaults_root = Path(config["vaults_root"]).expanduser()
    discovered = _discover_or_exit(vaults_root)
    name = vault or config["default_vault"]
    selected = next((entry for entry in discovered if entry.name == name), None)
    if selected is None:
        typer.secho(
            f"No vault named {name!r} under {vaults_root}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"Vault: {selected.name} ({selected.path})")
    _stub("TUI")


@app.command()
def index() -> None:
    """
    Build the vault index (spec §10).
    """
    _stub("index")


@app.command()
def reindex() -> None:
    """
    Refresh the vault index (spec §10).
    """
    _stub("reindex")


@app.command()
def mcp() -> None:
    """
    Run the bundled MCP server (spec §11).
    """
    _stub("mcp")


@app.command()
def new(
    title: str = typer.Argument(None, help="Title of the note to create."),
) -> None:
    """
    Create a note at the vault root (spec §10).
    """
    _stub("new")


@app.command()
def daily() -> None:
    """
    Create/print the path to today's daily note (spec §10).
    """
    _stub("daily")


@app.command()
def capture(
    text: str = typer.Argument(None, help="Text to capture to the inbox."),
) -> None:
    """
    Quick-capture to the inbox folder (spec §10).
    """
    _stub("capture")


@app.command()
def preview(
    path: str = typer.Argument(None, help="Path of the note to render."),
    browser: bool = typer.Option(
        False, "--browser", help="Render to the local browser instead."
    ),
) -> None:
    """
    Render a note to the terminal or the local browser (spec §10).
    """
    _stub("preview")


@app.command()
def find(
    query: str = typer.Argument(None, help="Initial fuzzy-search query."),
) -> None:
    """
    Standalone fzf-powered fuzzy search (spec §10).
    """
    _stub("find")


@app.command()
def audit() -> None:
    """
    Vault content health: orphans, unresolved links, broken anchors (spec §10).
    """
    _stub("audit")


@app.command()
def doctor() -> None:
    """
    Environment health: optional binaries, editor detection, config (spec §7.5).
    """
    _stub("doctor")


def main() -> None:
    """
    Run the typer app. Target of the ``hoppus``/``mark``/``hop`` console
    scripts via ``hoppus:main``.
    """
    app()
