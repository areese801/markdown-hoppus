"""
Typer CLI application shared by the three interchangeable console commands
``hoppus``, ``mark``, and ``hop`` (spec §1, §10).

Fully implemented here: ``vaults`` (list vaults under the Vaults Root) and
``audit`` (report-only unresolved-wikilink report, spec §10 / §19 D1,
HOPPUS-40), plus ``doctor`` (environment health report, spec §7.5,
HOPPUS-43), ``find`` (standalone fzf-powered fuzzy search, spec §10 / §3,
HOPPUS-46), ``daily`` (open/create today's daily note, spec §9.9,
HOPPUS-49), ``capture`` (quick-capture to the inbox, spec §9.11,
HOPPUS-51), and the dispatch/skeleton for every always-available
subcommand. ``preview --browser`` renders locally to the browser (spec
§9.5, HOPPUS-56). Subcommands owned by later stories (``new``,
``preview`` without ``--browser``, ``index``/``reindex``, ``mcp``) and
the TUI launch are registered as clearly-marked stubs so ``--help`` shows
the full command tree.
"""

from datetime import datetime
from pathlib import Path

import typer

from hoppus import __version__
from hoppus.capture import capture_note
from hoppus.config import load_config
from hoppus.daily import open_or_create_daily
from hoppus.environment import find_binary, run_doctor
from hoppus.find import run_find
from hoppus.index.indexer import Index
from hoppus.integrity import AuditIssue, audit_vault
from hoppus.render.browser import (
    go_grip_available,
    launch_go_grip,
    open_in_browser,
    write_preview_html,
)
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
    Open (creating if needed) today's daily note (spec §10, §9.9,
    HOPPUS-49).

    Resolves the default vault, then prints the note's absolute path
    plus a ``(created)``/``(exists)`` marker. Idempotent — a second
    invocation reports the same file without touching it.
    """
    config = load_config()
    vaults_root = Path(config["vaults_root"]).expanduser()
    discovered = _discover_or_exit(vaults_root)
    name = config["default_vault"]
    selected = next((entry for entry in discovered if entry.name == name), None)
    if selected is None:
        typer.secho(
            f"No vault named {name!r} under {vaults_root}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    path, created = open_or_create_daily(selected.path, config, now=datetime.now())
    typer.echo(f"{path} ({'created' if created else 'exists'})")


@app.command()
def capture(
    text: str = typer.Argument(None, help="Text to capture to the inbox."),
) -> None:
    """
    Quick-capture to the inbox folder (spec §10, §9.11, HOPPUS-51).

    Drops a timestamp-named note into the configured ``inbox.folder``
    (default ``"."`` — the vault root, GTD convention) of the default
    vault. The optional TEXT argument becomes the note body; the
    filename is always the timestamp. Prints the created note's
    absolute path.
    """
    config = load_config()
    vaults_root = Path(config["vaults_root"]).expanduser()
    discovered = _discover_or_exit(vaults_root)
    name = config["default_vault"]
    selected = next((entry for entry in discovered if entry.name == name), None)
    if selected is None:
        typer.secho(
            f"No vault named {name!r} under {vaults_root}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        path = capture_note(selected.path, config, text=text or "", now=datetime.now())
    except (ValueError, FileExistsError, OSError) as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error
    typer.echo(str(path))


@app.command()
def preview(
    path: str = typer.Argument(None, help="Path of the note to render."),
    browser: bool = typer.Option(
        False, "--browser", help="Render to the local browser instead."
    ),
) -> None:
    """
    Render a note to the terminal or the local browser (spec §10, §9.5,
    HOPPUS-56).

    With ``--browser``, renders PATH locally (markdown-it-py + Pygments,
    GitHub-like CSS) to a temp HTML file and opens it in the browser via
    a ``file://`` URL — no content ever leaves the machine. PATH is
    resolved as given (absolute or relative to the current directory).
    If ``preview.browser_renderer`` is ``go-grip`` and the binary is on
    the PATH, rendering is delegated to it instead; the Python ``grip``
    package is never used. The terminal preview (without ``--browser``)
    belongs to a later story and remains stubbed.
    """
    if not browser:
        _stub("preview")
        return
    if path is None:
        typer.secho("preview --browser requires a PATH", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    note = Path(path).expanduser()
    if not note.is_file():
        typer.secho(f"No such note: {note}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    config = load_config()
    renderer = config.get("preview", {}).get("browser_renderer", "builtin")
    if renderer == "go-grip" and go_grip_available():
        launch_go_grip(note)
        typer.echo(f"Rendering {note} with go-grip (localhost)")
        return

    text = note.read_text(encoding="utf-8")
    html_path = write_preview_html(text, title=note.stem)
    open_in_browser(html_path)
    typer.echo(str(html_path))


@app.command()
def find(
    query: str = typer.Argument(None, help="Initial fuzzy-search query."),
) -> None:
    """
    Standalone fzf-powered fuzzy search over the default vault's notes
    (spec §10, §3, HOPPUS-46).

    Pipes the vault's notes through the real ``fzf`` binary (with a
    ``bat``/``cat`` preview) and prints the selected note's absolute
    path. This is the one fzf-dependent subcommand; it errors clearly
    when ``fzf`` is absent. Cancelling in fzf exits 0 and prints
    nothing.
    """
    config = load_config()
    vaults_root = Path(config["vaults_root"]).expanduser()
    discovered = _discover_or_exit(vaults_root)
    name = config["default_vault"]
    selected = next((entry for entry in discovered if entry.name == name), None)
    if selected is None:
        typer.secho(
            f"No vault named {name!r} under {vaults_root}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    fzf_path = find_binary("fzf")
    if fzf_path is None:
        typer.secho(
            "hop find requires the 'fzf' binary, which was not found on "
            "PATH. Install fzf, or use the in-TUI search (press / in the "
            "TUI).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    index = Index.build(selected.path)
    selection = run_find(
        index, query, fzf_path=fzf_path, preview_path=find_binary("bat")
    )
    if selection is not None:
        typer.echo(str(selection))


def _format_issue(issue: AuditIssue) -> str:
    """
    Render an audit issue as its written link form plus a kind label.

    Wikilinks render as ``[[target#anchor|display]]`` (embeds keep the
    ``!``); markdown links render as ``[display](target)``. The label
    is the issue kind with underscores spaced, e.g. ``(broken anchor)``
    — plain and greppable.

    Args:
        issue: The audit issue to render.

    Returns:
        The rendered line fragment, e.g. ``![[img.png]] (missing
        attachment)``.
    """
    if issue.is_wikilink:
        inner = issue.target
        if issue.anchor:
            inner += f"#{issue.anchor}"
        if issue.display:
            inner += f"|{issue.display}"
        rendered = f"{'!' if issue.is_embed else ''}[[{inner}]]"
    else:
        rendered = f"[{issue.display or ''}]({issue.target})"
    return f"{rendered} ({issue.kind.replace('_', ' ')})"


@app.command()
def audit(
    vault: str = typer.Argument(
        None, help="Vault name (default: the configured default vault)."
    ),
) -> None:
    """
    Report vault content health (spec §10, §7.4, D1): unresolved
    wikilinks, broken anchors, missing attachments, and broken markdown
    links, each gated by its ``link_integrity`` toggle (§7.6).

    Report-only — never prompts or mutates.
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

    index = Index.build(selected.path)
    report = audit_vault(index, config=config)
    if report.count == 0:
        typer.echo(f"No unresolved links in {selected.name}.")
        return
    typer.echo(
        f"{selected.name}: {report.count} unresolved link(s) "
        f"across {report.note_count()} note(s)"
    )
    for note_path, issues in report.by_note().items():
        relative = note_path.relative_to(selected.path)
        for issue in issues:
            typer.echo(f"  {relative}: {_format_issue(issue)}")


@app.command()
def doctor() -> None:
    """
    Environment health: optional binaries, editor detection, config (spec §7.5).

    Distinct from ``audit`` (content health). Always runs to completion —
    problems become report lines, not nonzero exits.
    """
    report = run_doctor(load_config())

    typer.echo("Optional accelerators:")
    for name, path in report.binaries.items():
        typer.echo(f"  {name}: {path or 'not found'}")

    typer.echo("Editor:")
    typer.echo(f"  command: {' '.join(report.editor_info.argv)}")
    typer.echo(f"  nvim: {'yes' if report.editor_info.is_nvim else 'no'}")
    plugin_status = {True: "detected", False: "not detected", None: "unknown"}[
        report.plugin_detected
    ]
    typer.echo(f"  obsidian.nvim: {plugin_status}")
    if report.nudge:
        typer.echo(f"  {report.nudge}")

    typer.echo("Config health:")
    if report.config_warnings:
        for warning in report.config_warnings:
            typer.echo(f"  warning: {warning}")
    else:
        typer.echo("  OK")

    typer.echo("Vaults:")
    typer.echo(f"  root: {report.vaults_root}")
    if report.vaults_error is not None:
        typer.echo(f"  warning: {report.vaults_error}")
    else:
        names = report.vault_names or []
        typer.echo(f"  discovered: {len(names)}")
        for name in names:
            typer.echo(f"    {name}")


def main() -> None:
    """
    Run the typer app. Target of the ``hoppus``/``mark``/``hop`` console
    scripts via ``hoppus:main``.
    """
    app()
