# markdown-hoppus

> A terminal-first, TUI Markdown knowledge base. Think Obsidian, but living
> entirely in the terminal, editing delegated to `$EDITOR`, and storing nothing
> but plain `.md` files on disk.

`hoppus` reimplements Obsidian's core — a folder of plain-text Markdown files —
as a standalone, MIT-licensed CLI/TUI built on [Textual](https://textual.textualize.io/).
It never depends on Obsidian being installed, yet stays fully **round-trip
compatible** with an Obsidian vault: the same folder opens interchangeably in
either tool with no conversion step and no data loss. The TUI is a
navigation/linking/search shell, not an editor — when it's time to write, hoppus
suspends and hands the note to your `$EDITOR`.

- **Distribution:** `markdown-hoppus` · **Import package:** `hoppus`
- **Console commands:** `hoppus`, `mark`, `hop` — three interchangeable entry points
- **License:** [MIT](LICENSE)

## Install

Requires Python 3.11+.

```bash
uv tool install markdown-hoppus
# or
pipx install markdown-hoppus
# or
pip install markdown-hoppus
```

All three console commands invoke the same CLI; use whichever you like
(examples below use `hop` for brevity).

## Zero-external-binary guarantee

hoppus is **fully functional with zero external binaries installed**. Its
dependencies (Textual, Rich, markdown-it-py, ruamel.yaml, watchdog, rapidfuzz,
pyperclip, typer, mcp) are all pure Python — nothing to compile, no system
packages.

If certain binaries happen to be on your `PATH`, hoppus detects and uses them
as accelerators. They only enhance; they are never required:

| Binary | What it speeds up | Fallback when absent |
|---|---|---|
| `rg` (ripgrep) | Full-text content search | Pure-Python file scan (same results, slower) |
| `fzf` | The standalone `hop find` subcommand | `hop find` errors clearly; in-TUI fuzzy search still works |
| `glow` | Alternate styled terminal preview (`preview.renderer: glow`) | Native Textual Markdown rendering |
| `go-grip` | Browser rendering (`preview.browser_renderer: go-grip`) | Built-in local render (markdown-it-py + Pygments); no content ever leaves your machine |

Run `hop doctor` to see which accelerators are present, what editor was
detected, and whether your config is healthy.

## Usage

### The TUI

Launch the TUI on your default vault with a bare `hop`, or on a named vault
with `hop open <VAULT>`. The layout has three regions:

- **Left sidebar** (tabbed): file explorer tree · tags · bookmarks
- **Main pane:** the active note's rendered preview (or the graph view or search results)
- **Right sidebar** (toggleable): backlinks (linked mentions) + unlinked mentions

Key workflows:

- **Navigate wikilinks** — `[[links]]` and `![[embeds]]` in the preview are
  clickable/selectable and resolve the way Obsidian resolves them (by filename,
  shortest-unique-path disambiguation, aliases).
- **Backlinks & unlinked mentions** — see every note that links to the active
  note, plus places its title/aliases appear without a link.
- **Quick switcher** (`Ctrl+O`) — fuzzy-jump to any note by title or alias.
- **Command palette** (`Ctrl+P`) — fuzzy-searchable list of every command.
- **Search** (`Ctrl+F`) — fuzzy full-text plus targeted operators:
  `tag:area/sub`, `title:foo`, `path:Meetings/`, and `<key>:<value>` for
  frontmatter fields.
- **Graph view** (`g`) — a local N-hop neighborhood of the active note rendered
  with box-drawing characters; widen/narrow the radius with `+` / `-`.
- **Daily notes** (`d`) — open (creating if needed) today's `YYYY-MM-DD.md`,
  Obsidian-compatible.
- **Templates** (`N`) — new note from a template with `{{date}}`, `{{time}}`,
  and `{{title}}` substitution; templates are plain `.md` shared verbatim with
  Obsidian.
- **Quick capture** (`c`) — drop a timestamped note into your inbox folder
  without leaving the current context.
- **Bookmarks** (`s`) — star notes; they persist in `<vault>/.hoppus/bookmarks.yaml`.
- **Yanks** (`y`) — copy the note body, its absolute path, or a `[[wikilink]]`
  to the clipboard (with an OSC 52 option that works over SSH).

### Editing and link integrity

Press `e` to open the active note in `$VISUAL`/`$EDITOR` (fallback: `nvim`,
then `vi`). hoppus suspends its screen, runs the editor, and resumes when you
exit. On return it runs a **link-integrity pass**: every `[[wikilink]]` must
resolve to an existing file. For each unresolved link — in document order —
you get a fuzzy "did you mean?" pick list of near-matches:

- Pick a match → hoppus corrects that occurrence in place (typo repair),
  preserving any `|alias` or `#anchor`.
- Pick "none of these" → hoppus creates an empty note at the vault root, so
  the link resolves and your prose stays exactly as written.

Missing heading/block anchors warn; missing attachments and broken standard
Markdown links are report-only (see `hop audit`). Every check is individually
toggleable in config.

### Keybindings

Common defaults (press `?` in the TUI for the full overlay; all keys are
remappable via the `keymap` config section):

| Key | Action | Key | Action |
|---|---|---|---|
| `?` | Help | `Ctrl+P` | Command palette |
| `Ctrl+O` | Quick switcher | `Ctrl+F` | Search |
| `e` | Open in `$EDITOR` | `O` | Open in system app |
| `b` | Open in browser (local render) | `l` | Link this note to… |
| `g` | Graph view | `+` / `-` | Graph radius up/down |
| `Tab` | Cycle panes | `\` | Toggle backlinks sidebar |
| `t` | Tag pane | `d` | Daily note |
| `n` / `N` | New note / from template | `c` | Quick capture |
| `s` | Star/unstar | `y` | Yank menu |
| `r` | Reindex | `v` | Vault switcher |
| `q` | Quit | | |

## CLI

| Command | What it does |
|---|---|
| `hop` | Launch the TUI on the default vault |
| `hop open [VAULT]` | Launch the TUI on a named vault |
| `hop vaults` | List vaults under the Vaults Root |
| `hop find [QUERY]` | Standalone `fzf`-powered fuzzy search from the shell (the one fzf-dependent command) |
| `hop daily` | Open (creating if needed) today's daily note; prints its path |
| `hop capture [TEXT]` | Quick-capture a timestamped note to the inbox folder |
| `hop preview PATH --browser` | Render a note locally and open it in the browser |
| `hop audit [VAULT]` | Vault **content** health: unresolved wikilinks, broken anchors, missing attachments, broken Markdown links |
| `hop doctor` | **Environment** health: optional binaries, editor detection, config, vault discovery, templates folder |
| `hop mcp` | Run the bundled MCP server over stdio |

## Configuration

Global config lives at `~/.config/hoppus/config.yaml` (XDG-aware); per-vault
overrides at `<vault>/.hoppus/config.yaml` are deep-merged over it. Vaults are
subdirectories of a single configurable **Vaults Root** (default `~/Notes`) —
symlink vaults in from elsewhere if you like.

On a first run with no config, commands that need a vault print exactly what
to do: create the config file with `vaults_root` and `default_vault`, then
run `hop doctor` to verify.

Templates live in the configured `templates.folder` (default `Templates`);
when that folder is absent, hoppus automatically falls back to a vault's
`_templates/` or `templates/` folder, and `hop doctor` reports which folder
was resolved and how many templates it holds.

A few notable keys (missing keys fall back to sensible defaults):

```yaml
vaults_root: ~/Notes
default_vault: Personal

link_integrity:
  prompt_on: editor_return_only   # interactive fixes only after $EDITOR; opens/watcher are report-only

search:
  content_backend: auto           # auto (rg if present) | ripgrep | python

editor_support:
  encourage_obsidian_nvim: true   # set false to silence the nudge

index:
  cache: true                     # persist the index to <vault>/.hoppus/ for instant relaunch
```

With `index.cache` enabled (the default), the TUI persists the built index
to `<vault>/.hoppus/index.<version>.json` and renders instantly from it on
the next launch while a fresh build reconciles in the background. Set it to
`false` to skip all cache reads/writes.

See the full schema in [`spec.md`](spec.md) §12.

## obsidian.nvim

hoppus can't provide as-you-type `[[` completion inside your editor (it is
suspended while the editor runs — that's the editor's job). If your `$EDITOR`
is Neovim, install
[obsidian.nvim](https://github.com/obsidian-nvim/obsidian.nvim) for in-editor
`[[` completion, pointed at the same vault. hoppus shows a gentle, dismissible
tip when it detects nvim without the plugin; disable it with
`editor_support.encourage_obsidian_nvim: false`.

## Obsidian compatibility

A hoppus vault **is** an Obsidian vault: just a folder of `.md` files with
YAML frontmatter and standard attachments. Guarantees:

- `.obsidian/` is never written to, moved, or deleted — it's treated as
  read-only foreign state. hoppus keeps its own state under `.hoppus/`.
- Frontmatter writes are byte-preserving (ruamel.yaml round-trip): key order,
  formatting, and comments survive.
- Links, tags, templates, and daily notes follow Obsidian's conventions.

It is safe to use hoppus and real Obsidian on the same vault, even
concurrently. The one deliberate divergence: hoppus refuses to let dangling
wikilinks persist (it repairs or creates on editor return), which only makes
the files *more* consistent — never less Obsidian-compatible.

## MCP server

`hop mcp` runs a bundled MCP server over stdio so AI agents can read and
write your vault alongside you: listing/searching notes, backlinks, the local
link graph, audits, and note create/update/append/rename/delete. Agent writes
go through the same link-propagation and integrity paths as the TUI — writes
containing unresolved wikilinks are **rejected by default** with near-match
suggestions so the agent can retry (opt in per call to auto-create instead).
Set `mcp.read_only: true` to disable the write tools entirely.

## License

MIT — see [`LICENSE`](LICENSE).
