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
§9.5, HOPPUS-56). ``mcp`` launches the bundled MCP server over stdio
(spec §11, HOPPUS-58). Bare invocation and ``open`` launch the real TUI
(:class:`hoppus.tui.app.HoppusApp`, HOPPUS-68). Subcommands owned by later
stories (``new``, ``preview`` without ``--browser``, ``index``/``reindex``)
are registered as clearly-marked stubs so ``--help`` shows the full command
tree.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import typer

from hoppus import __version__
from hoppus.capture import capture_note
from hoppus.config import (
    ConfigError,
    default_config,
    global_config_path,
    load_config,
    resolved_config_path,
)
from hoppus.daily import open_or_create_daily
from hoppus.environment import find_binary, run_doctor
from hoppus.find import run_find
from hoppus.index.indexer import Index
from hoppus.integrity import (
    KIND_INVALID_FRONTMATTER,
    KIND_UNREADABLE_FILE,
    AuditIssue,
    audit_vault,
)
from hoppus.mcp import server as mcp_server
from hoppus.mcp.tools import NoteNotFound, resolve_note
from hoppus.render.browser import (
    go_grip_available,
    launch_go_grip,
    open_in_browser,
    write_preview_html,
)
from hoppus.tui.app import HoppusApp
from hoppus.vault import Vault, discover_vaults, resolve_default_vault

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


def _load_config_or_exit() -> dict[str, Any]:
    """
    Load the merged config, exiting cleanly on a malformed file.

    A YAML parse error in a config file becomes an actionable message
    naming the file and the problem — never a raw traceback
    (HOPPUS-87).

    Returns:
        The fully merged configuration dict.

    Raises:
        typer.Exit: With code 1 when a config file cannot be parsed.
    """
    try:
        return load_config()
    except ConfigError as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        typer.secho("Fix the file above, then run `hop doctor` to verify.", err=True)
        raise typer.Exit(code=1) from error


def _missing_root_guidance() -> str:
    """
    Return the actionable first-run guidance for a missing Vaults Root.

    Names the exact global config file (XDG-aware, via
    :func:`hoppus.config.global_config_path`) and the two keys required
    to get going, and points at ``hop doctor`` (HOPPUS-74 F14).

    Returns:
        The multi-line guidance message.
    """
    return (
        f"No vault configured. Create {global_config_path()} with:\n"
        "  vaults_root: /path/to/your/vaults\n"
        "  default_vault: <name>\n"
        "Then run `hop doctor` to verify."
    )


def _discover_or_exit(vaults_root: Path) -> list[Vault]:
    """
    Discover vaults under ``vaults_root``, exiting with code 1 on failure.

    On a missing or non-directory Vaults Root, prints the error plus
    actionable first-run guidance naming the config file to create
    (HOPPUS-74 F14) before exiting.

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
        typer.secho(_missing_root_guidance(), err=True)
        raise typer.Exit(code=1) from error


def _choose_vault_guidance(discovered: list[Vault]) -> str:
    """
    Return the "choose a vault" guidance for an unresolved default.

    Shown when no ``default_vault`` is set (or it matches nothing) and
    several vaults exist, so hoppus cannot pick one for the user
    (HOPPUS-88). Mirrors the HOPPUS-74 first-run guidance: name the
    exact config file and the key to set.

    Args:
        discovered: The vaults discovered under the Vaults Root.

    Returns:
        The multi-line guidance message.
    """
    names = ", ".join(vault.name for vault in discovered)
    return (
        f"No default_vault set; choose one of: {names}\n"
        f"Pass a vault name (e.g. `hop open <vault>`), or set in "
        f"{global_config_path()}:\n"
        "  default_vault: <name>"
    )


def _select_vault(
    config: dict[str, Any],
    discovered: list[Vault],
    vaults_root: Path,
    requested: str | None = None,
) -> Vault:
    """
    Select the vault a command should operate on (HOPPUS-88).

    An explicitly requested vault name (CLI argument/option) must match
    a discovered vault exactly. Otherwise the configured
    ``default_vault`` is resolved gracefully: a name match wins; with
    no usable name and exactly one vault, that vault is used; with
    several vaults, the user is asked to choose.

    Args:
        config: The merged configuration mapping (spec §12).
        discovered: The vaults discovered under ``vaults_root``.
        vaults_root: The Vaults Root (for error messages).
        requested: An explicit vault name from the command line, or
            None to use the configured default.

    Returns:
        The selected vault.

    Raises:
        typer.Exit: With code 1 when the requested vault does not
            exist, no vaults were discovered, or several vaults exist
            and no default could be resolved.
    """
    if requested is not None:
        selected = next(
            (entry for entry in discovered if entry.name == requested), None
        )
        if selected is None:
            typer.secho(
                f"No vault named {requested!r} under {vaults_root}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)
        return selected

    if not discovered:
        typer.secho(
            f"No vaults found under {vaults_root}", fg=typer.colors.RED, err=True
        )
        typer.secho(_missing_root_guidance(), err=True)
        raise typer.Exit(code=1)

    name = config.get("default_vault")
    selected = resolve_default_vault(str(name) if name else None, discovered)
    if selected is None:
        typer.secho(_choose_vault_guidance(discovered), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    return selected


STUB_EXIT_CODE = 3
"""Exit code shared by every not-yet-implemented stub subcommand."""


def _stub(feature: str) -> None:
    """
    Report an unimplemented subcommand and exit non-zero.

    Prints the placeholder notice to STDERR and exits with
    :data:`STUB_EXIT_CODE` so scripts and CI can detect the no-op
    instead of mistaking it for success.

    Args:
        feature: Human-readable name of the feature being stubbed.

    Raises:
        typer.Exit: Always, with code :data:`STUB_EXIT_CODE`.
    """
    typer.secho(f"{feature}: {NOT_IMPLEMENTED_SUFFIX}", err=True)
    raise typer.Exit(code=STUB_EXIT_CODE)


def _launch_tui(vault: str | None = None) -> None:
    """
    Resolve a vault and run the TUI on it (spec §10, HOPPUS-68).

    Args:
        vault: Vault name to open. Defaults to the configured default
            vault.

    Raises:
        typer.Exit: With code 1 if the Vaults Root is missing, no vault
            with the resolved name exists under it, or no default vault
            can be resolved (HOPPUS-88).
    """
    config = _load_config_or_exit()
    vaults_root = Path(config["vaults_root"]).expanduser()
    discovered = _discover_or_exit(vaults_root)
    selected = _select_vault(config, discovered, vaults_root, requested=vault)
    tui_config = {**config, "default_vault": selected.name}
    HoppusApp(config=tui_config, vaults_root=vaults_root).run()


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
        _launch_tui()


@app.command()
def vaults() -> None:
    """
    List vaults under the Vaults Root (spec §10).
    """
    config = _load_config_or_exit()
    vaults_root = Path(config["vaults_root"]).expanduser()
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
    Launch the TUI on a named vault (spec §10, HOPPUS-68).
    """
    _launch_tui(vault)


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
    Run the bundled MCP server over stdio (spec §11, HOPPUS-58).

    Blocks serving MCP clients until the client disconnects.
    """
    mcp_server.run(_load_config_or_exit())


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
    config = _load_config_or_exit()
    vaults_root = Path(config["vaults_root"]).expanduser()
    discovered = _discover_or_exit(vaults_root)
    selected = _select_vault(config, discovered, vaults_root)

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
    config = _load_config_or_exit()
    vaults_root = Path(config["vaults_root"]).expanduser()
    discovered = _discover_or_exit(vaults_root)
    selected = _select_vault(config, discovered, vaults_root)

    try:
        path = capture_note(selected.path, config, text=text or "", now=datetime.now())
    except (ValueError, FileExistsError, OSError) as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error
    typer.echo(str(path))


def _resolve_preview_note(
    config: dict[str, Any], note_ref: str, vault: str | None
) -> Path:
    """
    Resolve a preview NOTE reference as a note name within a vault.

    Mirrors the ``audit``/MCP note semantics: the reference may be a
    vault-relative path or a note title/alias.

    Args:
        config: The merged configuration mapping (spec §12).
        note_ref: The note reference to resolve.
        vault: Vault name; defaults to the configured default vault.

    Returns:
        The absolute path of the matching note.

    Raises:
        typer.Exit: With code 1 if the vault or the note cannot be
            resolved.
    """
    vaults_root = Path(config["vaults_root"]).expanduser()
    discovered = _discover_or_exit(vaults_root)
    selected = _select_vault(config, discovered, vaults_root, requested=vault)
    index = Index.build(selected.path)
    try:
        return resolve_note(index, note_ref)
    except NoteNotFound as error:
        typer.secho(str(error), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error


@app.command()
def preview(
    path: str = typer.Argument(
        None, help="Note name, or filesystem path, of the note to render."
    ),
    vault: str = typer.Option(
        None,
        "--vault",
        help=(
            "Vault to resolve note names in (default: the configured default vault)."
        ),
    ),
    browser: bool = typer.Option(
        False, "--browser", help="Render to the local browser instead."
    ),
) -> None:
    """
    Render a note to the terminal or the local browser (spec §10, §9.5,
    HOPPUS-56, HOPPUS-72).

    PATH resolution order: if PATH exists as a file (absolute,
    ``~``-expanded, or relative to the current directory) it is used
    directly; otherwise it is resolved as a note name (vault-relative
    path or title/alias) within the selected vault (``--vault`` or the
    configured default), matching ``audit``/MCP semantics.

    With ``--browser``, renders the note locally (markdown-it-py +
    Pygments, GitHub-like CSS) to a temp HTML file and opens it in the
    browser via a ``file://`` URL — no content ever leaves the machine.
    If ``preview.browser_renderer`` is ``go-grip`` and the binary is on
    the PATH, rendering is delegated to it instead; the Python ``grip``
    package is never used. The terminal preview (without ``--browser``)
    belongs to a later story and remains stubbed.
    """
    if not browser:
        _stub("preview")
    if path is None:
        typer.secho("preview --browser requires a PATH", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    config = _load_config_or_exit()
    note = Path(path).expanduser()
    if not note.is_file():
        note = _resolve_preview_note(config, path, vault)

    renderer = config.get("preview", {}).get("browser_renderer", "builtin")
    if renderer == "go-grip" and go_grip_available():
        launch_go_grip(note)
        typer.echo(f"Rendering {note} with go-grip (localhost)")
        return

    try:
        text = note.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        typer.secho(f"Cannot read {note}: {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from error
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
    config = _load_config_or_exit()
    vaults_root = Path(config["vaults_root"]).expanduser()
    discovered = _discover_or_exit(vaults_root)
    selected = _select_vault(config, discovered, vaults_root)

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
    ``!``); markdown links render as ``[display](target)``; invalid
    frontmatter and unreadable files render their one-line failure
    description. The label is the
    issue kind with underscores spaced, e.g. ``(broken anchor)`` —
    plain and greppable.

    Args:
        issue: The audit issue to render.

    Returns:
        The rendered line fragment, e.g. ``![[img.png]] (missing
        attachment)``.
    """
    if issue.kind in (KIND_INVALID_FRONTMATTER, KIND_UNREADABLE_FILE):
        return f"{issue.target} ({issue.kind.replace('_', ' ')})"
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

    Exit codes: 0 when the vault is clean, 1 when one or more problems
    are reported (so CI can gate on link hygiene).
    """
    config = _load_config_or_exit()
    vaults_root = Path(config["vaults_root"]).expanduser()
    discovered = _discover_or_exit(vaults_root)
    selected = _select_vault(config, discovered, vaults_root, requested=vault)

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
    raise typer.Exit(code=1)


@app.command()
def doctor() -> None:
    """
    Environment health: optional binaries, editor detection, config (spec §7.5).

    Distinct from ``audit`` (content health). Always runs to completion —
    problems become report lines, not nonzero exits. A malformed config
    file is reported as unhealthy (with the parse error) rather than
    crashing (HOPPUS-87), and the resolved config file path is always
    shown (HOPPUS-89).
    """
    config_error: str | None = None
    try:
        config = load_config()
    except ConfigError as error:
        config = default_config()
        config_error = str(error)
    report = run_doctor(
        config, config_path=resolved_config_path(), config_error=config_error
    )

    typer.echo("Config:")
    if report.config_path is not None:
        typer.echo(f"  file: {report.config_path}")
    else:
        typer.echo(
            f"  file: none found at {global_config_path()} (using built-in defaults)"
        )
    typer.echo(f"  vaults_root: {report.vaults_root}")
    typer.echo(f"  default_vault: {report.default_vault or '(not set)'}")

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

    typer.echo("Templates:")
    if report.templates_folder is None:
        typer.echo("  folder: unknown (default vault not discovered)")
    else:
        status = "exists" if report.templates_folder_exists else "missing"
        typer.echo(f"  folder: {report.templates_folder} ({status})")
        typer.echo(f"  templates found: {report.template_count}")


def main() -> None:
    """
    Run the typer app. Target of the ``hoppus``/``mark``/``hop`` console
    scripts via ``hoppus:main``.
    """
    app()
