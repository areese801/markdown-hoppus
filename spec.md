# markdown-hoppus — Specification

> A terminal-based, TUI-first Markdown knowledge base. Think Obsidian, but living entirely in the terminal, editing delegated to `$EDITOR`, and storing nothing but plain `.md` files on disk.

**Status:** MVP specification for hand-off to an implementing agent (Claude Code).
**License:** MIT.
**PyPI distribution:** `markdown-hoppus` (install: `uv tool install markdown-hoppus`).
**Import package:** `hoppus` — the tool is called *hoppus*; the distribution is namespaced as `markdown-hoppus`. Distribution and import names are independent by design.
**Console commands:** `hoppus`, `mark`, and `hop` — three interchangeable entry points into the same CLI.
**Name origin:** a nod to Mark Hoppus (Blink-182); `mark` doubles down on the pun, and `hop` also reads as graph-traversal ("hops" between linked notes).

> **Implementation decisions (2026-07-02):** seven post-review decisions (D1–D7) refine
> this spec and take precedence where they touch it. See **§19** at the end. Summary:
> integrity prompts fire on editor-return only (open/watcher are report-only); MCP writes
> reject-and-suggest on unresolved links; Textual can't render OFM links natively (spike
> required); frontmatter uses **ruamel.yaml**; the watcher suppresses hoppus's own writes;
> rename propagation has defined edge-case behavior; the PyPI stub is published by the user.

---

## 1. Purpose & motivation

Obsidian is, at its core, a UI over a folder of plain-text Markdown files. `hoppus` reimplements that core as an open-source, MIT-licensed, terminal-native tool so it can be used in environments where a full desktop app like Obsidian is not approved but open-source CLI tooling is. It must be **completely standalone** — never dependent on Obsidian being installed — while remaining **fully round-trip compatible** with an Obsidian vault, so the same folder can be opened interchangeably in Obsidian or hoppus with no conversion step and no data loss.

Design north stars:

1. **The filesystem is the data model.** No proprietary database, no lock-in. Everything is `.md` files, YAML frontmatter, and standard attachments on disk. If hoppus disappeared, the vault is still perfectly usable plain text.
2. **The TUI is a navigation/linking/search shell, not an editor.** Editing is delegated to the user's `$EDITOR`.
3. **Obsidian-compatible by default; divergence only with a documented reason.**
4. **Pure-Python by default.** External binaries are optional accelerators, never hard requirements (see §3).

---

## 2. Goals & non-goals

### In scope (MVP = all of the following)

- Vault management under a configurable **Vaults Root**, with in-TUI vault switching.
- Robust Textual TUI: file-explorer tree, preview pane, backlinks pane, tag pane, command palette, quick switcher.
- Full CRUD on notes and folders (create, rename, move, delete).
- Complete Obsidian-Flavored-Markdown (OFM) link support and resolution (see §6).
- **Link integrity: no dangling wikilinks.** On return from the editor, every `[[link]]` must resolve; unresolved links are forced to either a correction or a newly-created file (see §7). Deliberate connection-building via a `## Related` picker.
- Backlinks (linked mentions) and unlinked mentions.
- Tags: both inline `#tags` and YAML `tags:` frontmatter.
- Search: in-TUI fuzzy finder (titles + content) with targeted operators, **plus** a standalone `fzf`-powered CLI subcommand.
- **Local graph view**: N-degree ("N-hop") neighborhood of the current note, rendered with box-drawing/tree Unicode, with degree filtering (see §9.7).
- Markdown preview inside the TUI (native), with a raw-syntax-highlighted fallback, plus optional external renderer and local browser render (see §9.5).
- Editing via `$EDITOR` (terminal suspend/resume).
- Open note in the OS default `.md` application (e.g., real Obsidian) via the system opener.
- Daily notes and templates, Obsidian-compatible.
- Quick capture / inbox.
- Bookmarks / starred notes.
- Word count & vault stats.
- Clipboard actions (yank note body / path / `[[wikilink]]`).
- `hop doctor` environment check + gentle obsidian.nvim encouragement when `$EDITOR` is nvim (see §7).
- Bundled **read-write MCP server** so AI agents can operate on the vault alongside the human.
- Distribution via PyPI (`uv tool` / `pipx`).

### Non-goals (explicitly excluded)

- Obsidian's whole-vault global graph visualization (we do **local** N-hop subgraphs only).
- Canvas / infinite spatial boards.
- Sync and Publish services (users bring their own: git, Dropbox, iCloud, Syncthing).
- A theme/plugin ecosystem.
- A built-in text editor (delegated to `$EDITOR`).
- As-you-type `[[` completion inside the editor (architecturally impossible while hoppus is suspended — this is the editor's job; see §7).
- Sending note content over any network for rendering (all rendering is local — see §9.5).

### Post-MVP / future (design for, do not build yet)

- Inline terminal **image rendering** (kitty/iTerm2 graphics protocols, or an optional binary).
- Semantic / RAG search (sqlite-vec + local embeddings) once keyword search is outgrown.
- tmux-aware "open editor in a split pane" mode.
- Optional hoppus-emitted completion feed for editors without an Obsidian plugin (see §7).

---

## 3. Dependency philosophy

**Bias hard toward pure Python.** Reach for an external binary only when it is a clear, meaningful win, and always behind a **detect-and-fallback** pattern: if the binary is present, use it; otherwise fall back to a pure-Python implementation and continue working. Anything `brew install`-able (ideally a formula, not a cask) is acceptable as an *optional* accelerator.

| Concern | Pure-Python default | Optional accelerator (detect-if-present) |
|---|---|---|
| Full-text content search | Python file scan | `ripgrep` (`rg`) |
| In-TUI fuzzy matching | Pure-Python fuzzy matcher | — (kept in-process for a smooth TUI) |
| Standalone shell fuzzy search | — | `fzf` binary (required for the `hop find` CLI subcommand only) |
| Markdown → terminal render | Textual `Markdown`/`MarkdownViewer` + Rich `Syntax` | `glow` (alternate renderer, config-selectable) |
| Markdown → browser render | `markdown-it-py` + `Pygments` → localhost | `go-grip` (self-contained; **never** the Python `grip`, which POSTs to GitHub's API) |
| Clipboard | `pyperclip`, with OSC 52 escape option (works over SSH) | system clipboard binaries pyperclip already wraps |
| Open in system app | `subprocess` → `open` (macOS) / `xdg-open` (Linux) / `start` (Windows) | — |
| In-editor `[[` completion | — (not hoppus's job while suspended) | `obsidian.nvim` when `$EDITOR` is nvim (encouraged, not bundled) |

**Hard rule:** the app must be fully functional with **zero** external binaries installed. Optional binaries only enhance. `hop doctor` (§7.5) reports which are present.

---

## 4. Tech stack

- **Language:** Python 3.11+ (targets `tomllib`-era stdlib; primary parser work uses YAML).
- **TUI framework:** Textual (with Rich). Matches the existing `kanbaroo` stack.
- **Markdown rendering (TUI):** Textual's built-in `Markdown` / `MarkdownViewer` widgets. These render in-process and expose a `LinkClicked` message we intercept to make `[[wikilinks]]` navigable. Raw fallback uses Rich `Syntax`.
- **Markdown parsing (link/tag/frontmatter extraction):** `markdown-it-py` for structure + a dedicated OFM extraction layer for wikilinks/embeds/block-ids/inline-tags (these are Obsidian extensions, not CommonMark). YAML frontmatter via `PyYAML` (or `ruamel.yaml` if round-trip-preserving frontmatter edits are needed).
- **File watching:** `watchdog` (see §8 for degradation behavior).
- **Fuzzy matching / near-match repair:** a pure-Python matcher (e.g., `rapidfuzz` for edit-distance near-match suggestions; the interactive fuzzy finder may use the same or a small fzf-algorithm port). Kept in-process.
- **CLI:** `typer` or `click` (implementer's choice; `typer` preferred for type-driven ergonomics).
- **MCP server:** the Python MCP SDK (`mcp`), same pattern as prior projects (`kanbaroo`, `tasksquatch`, snippets repo).
- **Browser render:** `markdown-it-py` + `Pygments` + a small stdlib `http.server` on `localhost`.
- **Packaging:** `uv` / `hatchling` build backend; distribution `markdown-hoppus` on PyPI; import package `hoppus`; console scripts `hoppus`, `mark`, and `hop`.

---

## 5. Domain model & storage layout

### 5.1 Concepts

- **Vaults Root** — a single directory that contains one or more vaults as immediate subdirectories. Default `~/Notes` (configurable). Users may place vaults elsewhere on disk and **symlink** them into the Vaults Root.
- **Vault** — a subdirectory of the Vaults Root. A self-contained knowledge base of `.md` notes + attachments. Example: `~/Notes/Personal`, `~/Notes/Work`.
- **Note** — a single `.md` file. Its **title** is its filename without extension.
- **Attachment** — any non-`.md` file (image, PDF, etc.) referenced by notes.
- **Link** — a connection from one note to a note/heading/block/attachment (see §6).
- **Tag** — a label, expressed inline (`#tag`) and/or in frontmatter (`tags:`).

### 5.2 On-disk layout

```
~/Notes/                      # Vaults Root (configurable)
├── Personal/                 # a vault
│   ├── .obsidian/            # left UNTOUCHED if present (Obsidian's own config)
│   ├── .hoppus/              # hoppus per-vault state (see below)
│   │   ├── config.yaml       # optional per-vault config overrides
│   │   └── bookmarks.yaml    # starred/bookmarked notes
│   ├── Inbox note.md         # GTD: new/unfiled notes land at vault root
│   ├── People/
│   │   └── John Doe.md
│   ├── Meetings/
│   │   └── 2026-06-30 Standup.md
│   ├── Daily/
│   │   └── 2026-07-02.md
│   ├── Templates/
│   │   └── meeting.md
│   └── attachments/
│       └── diagram.png
└── Work/                     # another vault (or a symlink to elsewhere)
```

**Obsidian compatibility rules:**

- Never write to, move, or delete `.obsidian/`. Treat it as read-only foreign state. (Optionally *read* it later to honor Obsidian settings like daily-note folder, but MVP may ignore it and use hoppus config.)
- All hoppus-specific state lives under `.hoppus/` inside the vault, mirroring how Obsidian isolates its own state under `.obsidian/`.
- Notes, frontmatter, links, and tags use exactly Obsidian's conventions so files remain valid in both tools.
- Respect Obsidian's reserved characters in filenames/link targets: `# | ^ : %% [[ ]]`. Validate on create/rename and warn.

**GTD convention:** newly-created notes (whether from the "create note" action or auto-created by the link-integrity flow in §7) land at the **vault root** by default. The user triages there — adding tags, frontmatter, and detail — before filing into a proper subdirectory later.

### 5.3 Global config & state

- Global config: `~/.config/hoppus/config.yaml` (XDG; honor `$XDG_CONFIG_HOME`).
- Per-vault overrides: `<vault>/.hoppus/config.yaml` (merged over global).
- See §12 for the config schema.

---

## 6. Markdown / OFM parsing & link resolution

All of the following must be parsed, indexed, and navigable. This is the heart of the tool — implement against Obsidian's real grammar (references in §17).

### 6.1 Link syntaxes (all in scope for MVP)

| Syntax | Meaning |
|---|---|
| `[[Note]]` | Link to a note by title |
| `[[Note\|Display]]` | Link with per-link display text |
| `[[Note#Heading]]` | Link to a heading (nested: `[[Note#H1#H2]]`) |
| `[[Note#^block-id]]` | Link to a specific block |
| `[[#Heading]]` | Link to a heading in the current note |
| `[[#^block-id]]` | Link to a block in the current note |
| `![[Note]]` | Embed/transclude an entire note |
| `![[Note#Heading]]` / `![[Note#^block-id]]` | Embed a heading section / block |
| `![[image.png]]`, `![[image.png\|300]]` | Embed an attachment (optional size hint) |
| `[text](note.md)` / `[text](path/to/file.ext)` | Standard Markdown link |
| `^block-id` (trailing on a paragraph/list item) | Defines a block id as a link target |

### 6.2 Resolution rules (match Obsidian)

- **Resolve wikilinks by filename, not path.** No extension needed for `.md`; non-`.md` targets **must** include the extension.
- **Ambiguity → shortest unique path.** If two notes share a filename, disambiguate by adding just enough leading path components to make the target unique (e.g., `[[Projects/Alpha]]` vs `[[Archive/Alpha]]`).
- **Aliases.** A note's `aliases:` frontmatter list provides global alternate names that resolve links to that note and are surfaced in unlinked-mention detection.
- **Rename propagation.** Renaming/moving a note updates all inbound links across the vault (configurable prompt-before-update). This is a required, carefully-tested operation.
- **Heading/block anchors.** Resolve `#Heading` against the target note's heading structure; resolve `#^block-id` against defined block ids.

### 6.3 Deviation from Obsidian: no dangling wikilinks

Obsidian treats an unresolved `[[link]]` as a valid phantom that creates the note when clicked. **hoppus deliberately diverges: an unresolved wikilink is not allowed to persist.** On return from the editor, every wikilink must resolve to an existing file, via correction or file-creation (see §7). This divergence does **not** break Obsidian compatibility — the files on disk remain standard Markdown; hoppus is simply stricter at edit time and repairs anything introduced externally (Obsidian edits, `git pull`) on next open/audit.

### 6.4 Tags

- Parse **inline** `#tag` (including nested `#area/subarea`) from note bodies, excluding `#` inside code fences/inline code and excluding heading `#`.
- Parse **frontmatter** `tags:` (list or space/comma-delimited string).
- The two sets are unioned per note for indexing and filtering.

### 6.5 Frontmatter

- Standard YAML frontmatter delimited by `---` at the top of the file.
- Recognized keys: `aliases`, `tags`, plus arbitrary user keys (e.g., a Person note's `title`, `reports_to`). Arbitrary keys are preserved and indexed as metadata for targeted search (§9.8), never dropped on write.

---

## 7. Link integrity, completion & environment

This section defines how links get authored, validated, and repaired — and how hoppus cooperates with the editor rather than reinventing it. It is the practical guard against the two failure modes the tool exists to prevent: **misspelled links** and **accidental orphan notes**.

### 7.1 The three surfaces (who owns `[[` linking)

Because hoppus **suspends** while `$EDITOR` runs (it is a frozen parent process and cannot see keystrokes or inject into the editor buffer), `[[` linking is solved in three distinct places:

1. **Inside the editor (real writing) → the editor owns it.** When `$EDITOR` is nvim, `obsidian.nvim` provides as-you-type `[[` completion for note references, `[` for markdown links, and `#` for tags, powered by ripgrep, pointed at the same vault directory. hoppus cannot and does not try to provide this; it *encourages* the plugin instead (§7.5).
2. **Inside hoppus's reading view → hoppus owns it.** The `## Related` link picker (§7.2) — a fuzzy picker over titles/aliases that appends a deliberate connection. This is structural insertion, not cursor-positional, so it needs no editor.
3. **After the file is written → hoppus owns it.** The link-integrity pass (§7.3) validates and repairs on return from the editor. This is editor-agnostic and is the real safety net.

### 7.2 The `## Related` link picker (reading-view link authoring)

- Invoked by a keybind (`l`, "link this note to…") while viewing a note.
- Opens a fuzzy picker over the vault's note titles + aliases.
- On selection, appends `- [[Target]]` under a `## Related` heading in the current note:
  - If no `## Related` heading exists, create one at end-of-file.
  - Dedupe: do not append a link that is already present under `## Related`.
- If the picked name does **not** exist yet, also **bootstrap an empty `Target.md` at the vault root** (same as §7.3's create path), so `## Related` never introduces a dangling link.
- Rationale: mirrors the author's real Obsidian habit of maintaining a `## Related` section as the next-best-thing to inline links; ideal for wiring up connections while reviewing (e.g., the person-notes recall workflow).

### 7.3 Link-integrity pass (on return from editor)

Trigger: on return from a `$EDITOR` session (and re-checked via the watcher and on vault open for externally-introduced links). The whole file is parsed and every wikilink resolved. Behavior:

- **Hard rule — the target note must exist.** For each unresolved `[[Note]]` / `[[Note|alias]]` / `[[Note#anchor]]` / `![[Note]]` note-embed, hoppus prompts the user. Prompts happen **one per unresolved link, in document order** (two unresolved links ⇒ two sequential prompts).
- Each prompt shows a **"did you mean?" fuzzy pick list** of near-matches (small edit distance) against titles + aliases, plus a **"none of these"** option:
  - **User picks a match → in-line correction.** hoppus rewrites that specific occurrence to the chosen target, **preserving any `|alias` and `#anchor`**. (Typo repair.)
  - **User picks "none of these" → create an empty file.** hoppus bootstraps `~/<VaultsRoot>/<Vault>/<Target>.md` at the **vault root**, blank by default (optionally template-seeded via config). **The prose is left exactly as written** — the inline occurrence is untouched (Option A); the link now resolves because the file exists. It is **not** moved into `## Related`.
- **Worked example.** Editing `Meeting Notes.md`, the user types: `…reviewed the roadmap with [[Jane Smtih]] and kicked off [[Q3 Planning]].` where `Jane Smith.md` exists and `Q3 Planning.md` does not. On return there are **two** prompts: (1) `[[Jane Smtih]]` → pick `Jane Smith` → in-line correction; (2) `[[Q3 Planning]]` → "none of these" → create `Q3 Planning.md` at vault root, prose unchanged.

### 7.4 Anchors, attachments, and markdown links (softer handling)

- **Heading/block anchors** on an existing note: if `#Heading` or `#^block-id` doesn't resolve, emit a **configurable warning** (default on). No auto-creation — headings/blocks legitimately get added later and can't be sensibly fabricated.
- **Attachment refs** (`![[image.png]]`, `[x](file.pdf)`): **report only** in problems/`hop audit`. Never fabricate a binary.
- **Standard markdown links** `[text](note.md)`: **report only** in `hop audit`. The hard no-dangling rule applies specifically to wikilinks; markdown links are the portable form that may intentionally point outside the vault.

### 7.5 Editor cooperation & environment (`hop doctor`)

- **obsidian.nvim encouragement.** hoppus makes a **best-effort** check of whether `$EDITOR` resolves to nvim and whether `obsidian.nvim` appears installed (e.g., under a LazyVim plugin path). If nvim is the editor and the plugin isn't detected, hoppus shows a **gentle, dismissible** first-launch tip encouraging installation (this is what provides in-editor `[[` completion). The recommendation can be **disabled entirely** via config (default: enabled).
- **`hop doctor`** — a one-stop environment/health command (parallel to obsidian.nvim's own `:Obsidian check`) reporting:
  - Presence/version of optional accelerators: `rg`, `fzf`, `glow`, `go-grip`.
  - Detected `$EDITOR` and obsidian.nvim status (best-effort) with the nudge.
  - Config health (unreadable/unknown keys, missing folders referenced by config).
  - Vaults Root reachability and vault discovery.
  - (Distinct from `hop audit`, which checks vault **content** health — orphans, unresolved links, broken anchors, duplicate titles.)

### 7.6 Configurability

All checks are **per-type toggles, default enabled** (see §12): the hard no-dangling wikilink rule, the fuzzy near-match "did you mean?" suggestions, the anchor-existence warning, the attachment-ref report, and the markdown-link report. The obsidian.nvim nudge is independently toggleable.

---

## 8. Indexing engine

- On vault open, **scan all `.md` files** and build an in-memory index:
  - `notes`: title → path, aliases, headings, block-ids, frontmatter, word count, mtime.
  - `links`: note → outbound resolved/unresolved targets (with anchor + display).
  - `backlinks`: note → inbound linking notes (linked mentions).
  - `tags`: tag → set of notes; note → set of tags.
  - `title_index` / `alias_index`: for fast resolution, quick-switcher, and near-match repair (§7.3).
- **Unlinked mentions** are computed on demand for the active note (search bodies for the note's title/aliases where no explicit link exists), not precomputed for the whole vault.
- **Live updates:** use `watchdog` to update the index incrementally on file create/modify/delete/move — including edits made via the `$EDITOR` handoff *and* external edits (e.g., real Obsidian editing the same vault concurrently). **Graceful degradation:** if `watchdog` is unavailable or a platform issue arises, fall back to (a) re-index on return from the editor, and (b) a manual `reindex` command / keybind. Live watching is desired but must degrade cleanly rather than block core functionality.
- The **link-integrity pass** (§7.3) runs off the same triggers (editor return, watcher event, vault open) and uses the index for resolution + near-match suggestions.
- Index build must be fast for a few thousand notes; do incremental single-file re-index on change rather than full rescans.

---

## 9. TUI specification (Textual)

### 9.1 Overall layout

A three-region layout, all toggleable:

- **Left sidebar** (tabbed): File Explorer tree · Tag pane · Bookmarks. Also hosts search results when searching.
- **Main pane:** the active note's rendered preview (or raw view), or the graph view, or search results — depending on mode.
- **Right sidebar** (toggleable): Backlinks (linked mentions) + Unlinked mentions for the active note.
- **Footer:** key hints; **Header/status line:** current vault name, note title, word count.

Panes are keyboard-navigable; mouse supported but never required.

### 9.2 File Explorer

- Tree of folders + `.md` files (and attachments, visually de-emphasized), mirroring `tree`-style box drawing.
- Actions: create note, create folder, rename, move, delete (with confirm), reveal, open in `$EDITOR`, open in system app.
- Rename/move triggers link propagation (§6.2). New notes default to vault root (§5.2 GTD convention).

### 9.3 Quick Switcher

- Fuzzy jump to any note by title/alias (pure-Python fuzzy matcher). Enter opens in preview; a modifier opens directly in `$EDITOR`.

### 9.4 Command Palette

- Fuzzy-searchable list of all commands (create daily note, insert template, toggle panes, switch vault, reindex, open in browser, yank link, link to…, run doctor, etc.). Textual's command palette provides the base.

### 9.5 Preview / reading

- **Primary:** Textual `MarkdownViewer` — in-process render with TOC, syntax-highlighted code fences, tables, and intercepted links. `[[wikilinks]]` and embeds are wired via `LinkClicked` to navigate within hoppus (resolving per §6).
- **Raw fallback:** Rich `Syntax` view showing the raw Markdown with highlighting (toggleable; also the fallback if a note fails to render).
- **Optional external renderer:** if configured and present, pipe to `glow` for its styling.
- **Browser render:** render the note to HTML **locally** (`markdown-it-py` + `Pygments`, GitHub-like CSS), serve on `localhost`, open the browser. No content ever leaves the machine. Optionally use `go-grip` if present. **Never** use the Python `grip` (it POSTs to GitHub's API).
- Embeds (`![[...]]`) render transcluded content inline in preview where feasible; attachments show a placeholder with an "open in system app" affordance.
- Unresolved anchors and report-only issues (§7.4) may be surfaced inline as subtle indicators.

### 9.6 Editing handoff

- Open the active note in `$EDITOR` (default: `$VISUAL`/`$EDITOR`, fallback `nvim` then `vi`).
- Use Textual's `with app.suspend():` to drop out of the alternate screen, run the editor as a subprocess, and restore the TUI on exit. Works everywhere, including inside tmux.
- On return, run the **link-integrity pass** (§7.3) and re-index the edited file (the watcher also fires).
- **Post-MVP:** optional tmux-aware split-pane mode when `$TMUX` is set.

### 9.7 Local graph view (N-hop neighborhood)

- Renders the **local** subgraph around the **active note** — not the whole vault.
- Traverse both outbound links and backlinks to **N degrees ("hops")** away. Configurable `default_degrees` (default **2**) with a hard cap of **5** for MVP. (The BFS is degree-agnostic; raising the cap later is a one-constant change — see §17.) The traversal must support the real use case: from `John Doe` → the meeting note that links him → the other `Person` notes linked from that meeting, so the user can recover a forgotten name.
- Rendered with Unicode box-drawing / `tree`-style connectors (not a pixel graph). Each node shows the note title; edges indicate link direction where practical.
- Interactive: filter the visible radius **up/down from 1..N** hops live; select a node to jump to it (open in preview) or open in `$EDITOR`.
- **Node budget** guardrail: render up to a configurable max number of nodes, then truncate with a "+N more…" affordance. This — not the degree cap — is the real protection against hub notes (e.g., a Map-of-Content linking hundreds of notes) exploding the view. Optionally exclude notes whose link-count exceeds a threshold.
- Cycles handled gracefully (visit-set; annotate revisited nodes rather than looping).

### 9.8 Search (in-TUI)

- **Fuzzy finder** over titles **and** content (pure-Python matcher, fzf-like feel), live-updating as you type, with a preview of the highlighted match.
- **Targeted operators** (mirroring the snippets repo's search ergonomics):
  - `tag:area/sub` — notes with a tag
  - `title:foo` — title match only
  - `path:Meetings/` — path scope
  - `<key>:<value>` — frontmatter field match (e.g., `reports_to:"Jane Smith"`)
  - bare terms — fuzzy full-text
- Full-text content search uses `ripgrep` if present, else a pure-Python scan (same results, different speed).

### 9.9 Daily notes

- Command/keybind to open (creating if needed) today's daily note.
- Obsidian-compatible: filename format `YYYY-MM-DD.md` (configurable), placed in a configurable daily-notes folder, optionally seeded from a configured daily-note template.

### 9.10 Templates

- Obsidian-compatible core-Templates behavior: templates live in a configurable folder; inserting a template substitutes variables `{{date}}`, `{{time}}`, `{{title}}` (with configurable date/time formats). Templates are plain `.md` and shared verbatim with Obsidian.

### 9.11 Quick capture / inbox

- A global "new quick note" action that drops a timestamped (or prompted-title) note into a configurable **inbox** folder (default: vault root, per the GTD convention) without interrupting the current context. Usable both in-TUI and as a CLI subcommand (§10).

### 9.12 Bookmarks / starred

- Star/unstar notes; bookmarks persist in `<vault>/.hoppus/bookmarks.yaml`; a sidebar tab lists them.

### 9.13 Word count & stats

- Per-note word/char count in the status line.
- A vault stats view: note count, total words, tag counts, orphan count, unresolved-link count.

### 9.14 Clipboard actions

- Yank to clipboard: (a) note body, (b) absolute path, (c) a `[[wikilink]]` to the note. Via `pyperclip`, with an OSC 52 mode (config-selectable) so it works over SSH.

### 9.15 Vault switcher

- Switch the active vault among subdirectories of the Vaults Root without leaving the app; re-scans/indexes the newly selected vault.

### 9.16 Keybindings (initial defaults; all remappable via config)

| Key | Action |
|---|---|
| `?` | Help / keymap overlay |
| `Ctrl+P` | Command palette |
| `Ctrl+O` | Quick switcher (open note) |
| `Ctrl+F` | Search (fuzzy + operators) |
| `e` | Open active note in `$EDITOR` |
| `O` | Open active note in system default app |
| `b` | Open active note in browser (local render) |
| `l` | Link this note to… (append to `## Related`; bootstraps target if new) |
| `g` | Toggle graph view for active note |
| `+` / `-` | Increase / decrease graph hop radius |
| `Tab` | Cycle panes |
| `\` | Toggle right (backlinks) sidebar |
| `t` | Tag pane |
| `d` | Open/create today's daily note |
| `n` | New note · `N` new note from template |
| `c` | Quick capture to inbox |
| `s` | Star/unstar active note |
| `y` | Yank menu (body / path / wikilink) |
| `r` | Reindex vault |
| `v` | Vault switcher |
| `q` | Quit |

The link-integrity prompts (§7.3) are triggered automatically on return from the editor, not by a keybind.

---

## 10. CLI specification

All three console commands — `hoppus`, `mark`, and `hop` — invoke the same CLI and are interchangeable (examples below use `hop` for brevity). Subcommands (names indicative):

| Command | Behavior |
|---|---|
| `hop` (no args) | Launch the TUI on the default/last vault |
| `hop open [VAULT]` | Launch the TUI on a named vault |
| `hop find [QUERY]` | **Standalone `fzf`-powered** fuzzy search from a bare shell — pipes vault notes/content through the real `fzf` binary (with a `bat`/preview-style preview), prints/opens the selection. Requires `fzf`; errors clearly if absent. |
| `hop new [TITLE]` | Create a note at vault root (respects templates/inbox flags) |
| `hop daily` | Create/print path to today's daily note |
| `hop capture [TEXT]` | Quick-capture to the inbox folder |
| `hop preview PATH [--browser]` | Render a note to the terminal, or to the local browser with `--browser` |
| `hop index` / `hop reindex` | Build/refresh the index |
| `hop audit` | Vault **content** health: orphans, unresolved links, broken anchors, broken embeds, attachment-ref issues, markdown-link issues, duplicate titles |
| `hop doctor` | **Environment** health: optional binaries, `$EDITOR`/obsidian.nvim detection + nudge, config health, Vaults Root reachability (§7.5) |
| `hop mcp` | Run the bundled MCP server (§11) |
| `hop vaults` | List vaults under the Vaults Root |

`hop find` is the one place the `fzf` binary is expected (it is the whole point of the subcommand); everywhere else stays pure-Python.

---

## 11. MCP server specification

A bundled, **read-write** MCP server so AI agents can operate on the vault alongside the human. Mirror the shape of the existing snippets MCP, extended with note-graph and write tools. Run via `hop mcp`.

**Read tools**

| Tool | Purpose |
|---|---|
| `list_vaults` | Enumerate vaults under the Vaults Root |
| `list_notes` | List notes (optionally filtered by folder/tag) |
| `get_note` | Return a note's raw content + parsed metadata |
| `search_notes` | Fuzzy + targeted search (same operators as §9.8) |
| `list_tags` | All tags with counts |
| `get_backlinks` | Linked + unlinked mentions for a note |
| `get_links` | Outbound links (resolved/unresolved) for a note |
| `neighbors` | N-degree local subgraph around a note (powers the graph use case for agents) |
| `audit_vault` | Orphans, unresolved links, broken anchors/embeds, duplicate titles |

**Write tools**

| Tool | Purpose |
|---|---|
| `create_note` | Create a note (path/title, optional template, frontmatter, body); defaults to vault root |
| `update_note` | Replace/patch a note's body or frontmatter |
| `append_to_note` | Append content (e.g., agent adds to a daily note, an inbox note, or a `## Related` section) |
| `rename_note` | Rename/move with link propagation (§6.2) |
| `delete_note` | Delete (guarded/confirmable) |

Writes must go through the same link-propagation, link-integrity (§7.3), and index-update paths as the TUI so the human and agent never desync, and so agent-authored links can't dangle either. Consider a config flag to run the server read-only for contexts that want it (parallels prior read-only-MCP patterns), but default is read-write.

---

## 12. Configuration schema (YAML)

Global: `~/.config/hoppus/config.yaml`. Per-vault override: `<vault>/.hoppus/config.yaml` (deep-merged over global). YAML is chosen for symmetry with note frontmatter and because a YAML parser is already a dependency (zero added deps).

```yaml
vaults_root: ~/Notes          # directory containing vaults
default_vault: Personal       # opened when none specified

editor: null                  # null => $VISUAL/$EDITOR, then nvim, then vi

preview:
  renderer: native            # native | glow
  browser_renderer: builtin   # builtin (local http) | go-grip
  raw_fallback: true

search:
  content_backend: auto       # auto (rg if present) | ripgrep | python
  fuzzy: true

graph:
  max_degrees: 5              # hard cap for local subgraph traversal (MVP)
  default_degrees: 2
  max_nodes: 60               # node budget; truncate with "+N more…"
  exclude_hub_threshold: null # optional: skip notes with more than N links

link_integrity:
  enforce_no_dangling_wikilinks: true   # hard rule (§7.3)
  fuzzy_did_you_mean: true              # near-match "did you mean?" prompts
  warn_missing_anchor: true             # heading/block anchor warnings (§7.4)
  report_missing_attachments: true      # report-only (§7.4)
  report_broken_markdown_links: true    # report-only (§7.4)
  new_note_location: vault_root         # where auto-created notes land
  new_note_template: null               # optional template to seed created files
  related_heading: "## Related"         # heading used by the link picker (§7.2)

editor_support:
  encourage_obsidian_nvim: true         # gentle, dismissible nudge when $EDITOR is nvim

clipboard:
  backend: pyperclip          # pyperclip | osc52

daily_notes:
  folder: Daily
  date_format: "%Y-%m-%d"
  template: Templates/daily.md   # optional

templates:
  folder: Templates
  date_format: "%Y-%m-%d"
  time_format: "%H:%M"

inbox:
  folder: "."                 # default: vault root (GTD convention)

files:
  prompt_before_link_update: true   # confirm before rewriting inbound links on rename
  attachments_folder: attachments

keymap: {}                    # overrides for §9.16 defaults

mcp:
  read_only: false
```

Missing keys fall back to documented defaults. Unknown keys are preserved (forward-compat).

---

## 13. Suggested project structure

```
markdown-hoppus/               # repo / PyPI distribution name
├── pyproject.toml             # [project] name = "markdown-hoppus"
├── LICENSE                    # MIT
├── README.md
├── spec.md                    # this document
└── src/hoppus/
    ├── __init__.py
    ├── __main__.py
    ├── cli.py                 # typer app; subcommands (§10) incl. doctor/audit
    ├── config.py              # load/merge global + per-vault YAML (§12)
    ├── vault.py               # Vaults Root / Vault discovery & switching
    ├── model.py               # Note, Link, Tag, dataclasses
    ├── parse/
    │   ├── frontmatter.py
    │   ├── ofm.py             # wikilinks, embeds, block-ids, inline tags
    │   └── links.py           # resolution rules (§6.2)
    ├── integrity/
    │   ├── validate.py        # link-integrity pass (§7.3), anchor/attachment/md-link checks
    │   ├── repair.py          # in-line correction + create-at-root; ## Related picker (§7.2)
    │   └── nearmatch.py       # fuzzy "did you mean?" candidate ranking
    ├── index/
    │   ├── indexer.py         # scan + in-memory index (§8)
    │   └── watcher.py         # watchdog integration + degradation
    ├── search/
    │   ├── fuzzy.py           # pure-Python fuzzy matcher
    │   ├── operators.py       # tag:/title:/path:/<key>: parsing
    │   └── content.py         # ripgrep-or-python full-text
    ├── render/
    │   ├── terminal.py        # native Markdown widget / raw fallback / glow
    │   └── browser.py         # local markdown-it-py + Pygments + http.server
    ├── graph.py               # N-hop neighborhood traversal + node budget (§9.7)
    ├── clipboard.py           # pyperclip / OSC 52
    ├── system.py              # open-in-editor (suspend), open-in-system-app
    ├── environment.py         # hop doctor checks + obsidian.nvim detection (§7.5)
    ├── templates.py           # Obsidian-compatible templates & daily notes
    ├── tui/
    │   ├── app.py             # Textual App, layout, keymap
    │   ├── panes/             # explorer, preview, backlinks, tags, bookmarks
    │   ├── graph_view.py
    │   ├── search_view.py
    │   └── modals/            # quick switcher, command palette, link picker, did-you-mean, confirms
    └── mcp/
        └── server.py          # read-write MCP tools (§11)
```

---

## 14. Testing

- **Parser/resolution unit tests** are the highest priority: every link syntax in §6.1, shortest-unique-path disambiguation, alias resolution, block/heading anchors, reserved-character handling, and inline-vs-frontmatter tag extraction (including code-fence exclusion).
- **Link-integrity tests** (§7): the two-prompt worked example (typo → in-line correction preserving `|alias`/`#anchor`; new link → create-at-root with prose untouched, Option A); "none of these" file creation location = vault root; the `## Related` picker append + dedupe + bootstrap-if-new; anchor warning is report-only; attachment/md-link report-only; per-check config toggles honored.
- **Rename propagation** tests: renaming a note rewrites all inbound `[[...]]`, `[[...|alias]]`, `![[...]]`, and Markdown links correctly, and leaves unrelated text untouched.
- **Index** tests: incremental updates on create/modify/delete/move; watcher degradation path; integrity pass fires on editor-return / watcher / open.
- **Graph** tests: N-hop traversal correctness, hop filtering, node-budget truncation, hub handling, cycle handling, the John-Doe→meeting→person scenario as a fixture.
- **Search** tests: operator parsing; ripgrep-vs-python parity.
- **Environment tests:** `hop doctor` binary detection; best-effort nvim/obsidian.nvim detection; nudge suppression via config.
- **Obsidian round-trip** fixture: a small sample vault that must remain byte-compatible (no unintended rewrites) after hoppus opens/closes it; `.obsidian/` untouched.
- **MCP** tests: each tool; writes go through link-propagation + integrity + index-update (agent links can't dangle).
- Use a temp-dir sample vault fixture across the suite.

---

## 15. Packaging & distribution

- Build with `uv` / `hatchling`; publish to **PyPI** as the distribution `markdown-hoppus`. The Python import package remains `hoppus` (`import hoppus`, `src/hoppus/`) — distribution and import names are independent by design.
- **Reserve the name first.** PyPI has no reservation mechanism separate from uploading, so the very first build step (§16, Phase 0) is to publish a minimal stub release (skeleton `markdown-hoppus`, version `0.0.1`, MIT license, placeholder README; import package `hoppus`) to claim the name before real development so it can't be sniped. `markdown-hoppus` and `markdown_hoppus` normalize to the same project under PEP 503, so claiming one claims both.
- Console entry points: `hoppus`, `mark`, and `hop` — three interchangeable commands. Note `mark` and `hop` each exist as *different, unrelated* PyPI packages; that does not affect our entry-point commands (entry points are independent of package names), but confirm none already shadow these commands on your PATH (`type mark`, `type hop`, `type hoppus`) before relying on them.
- Install paths: `uv tool install markdown-hoppus` / `pipx install markdown-hoppus`.
- Python 3.11+.
- **MIT license** (the entire point — must be OSI MIT so it qualifies as approved open-source software).
- Core dependencies kept lean: Textual, Rich, markdown-it-py, PyYAML (or ruamel.yaml), watchdog, rapidfuzz, pyperclip, typer, mcp. All pure-Python / pip-installable; no compiled system requirements.
- README documents the zero-external-binary guarantee, the optional accelerators (`rg`, `fzf`, `glow`, `go-grip`), and the obsidian.nvim recommendation for in-editor `[[` completion.

---

## 16. Build phasing (for a multi-session implementation)

Ordered so each phase yields something runnable. (These map cleanly onto issue tracking; the author intends to drive the build with Claude Code, using kanbaroo for tracking and trusty-cage for isolation.)

0. **Reserve the name.** Publish a minimal stub `markdown-hoppus` distribution to PyPI (`0.0.1`, MIT, placeholder README; import package `hoppus`) to claim the name before real work begins (§15).
1. **Foundation:** repo scaffold, config, Vaults Root/Vault discovery, model, OFM parser + link resolution, in-memory index (no watcher yet). Unit tests for parsing/resolution.
2. **Read-only TUI:** explorer tree, native preview + raw fallback, quick switcher, command palette, vault switcher.
3. **Links & backlinks:** intercepted `[[wikilink]]`/embed navigation, backlinks pane, unlinked mentions.
4. **CRUD + rename propagation:** create (at vault root)/rename/move/delete with inbound-link rewriting; watcher + degradation.
5. **Link integrity & completion cooperation:** the on-return integrity pass (correct-or-create, two-prompt flow, Option A), the `## Related` link picker, anchor/attachment/md-link checks with config toggles, and `hop doctor` incl. the obsidian.nvim nudge.
6. **Search:** in-TUI fuzzy + operators; `ripgrep`/python content backend; standalone `hop find` (fzf) CLI.
7. **Graph view:** N-hop local subgraph with hop filtering + node budget.
8. **Productivity:** daily notes, templates, quick capture/inbox, bookmarks, word count/stats, clipboard yanks, open-in-system-app, browser render.
9. **MCP server:** read tools, then write tools, wired through the same propagation/integrity/index paths.
10. **Packaging & docs:** finalize PyPI release (supersede the stub), README, keymap docs, MIT license.

---

## 17. Reference material (for the implementer)

- Obsidian Help — Internal links: https://help.obsidian.md/links
- Obsidian Help — Aliases: https://help.obsidian.md/aliases
- Obsidian help source repo (has an `llms.txt` index of all pages): https://github.com/obsidianmd/obsidian-help — internal-links page: `en/Linking notes and files/Internal links.md`
- Community Obsidian-Flavored-Markdown reference (`obsidian-markdown` SKILL.md): https://deepwiki.com/kepano/obsidian-skills
- Textual `Markdown` / `MarkdownViewer` widgets: https://textual.textualize.io/widgets/markdown_viewer/
- Textual content/links (`LinkClicked`): https://textual.textualize.io/widgets/markdown/
- obsidian.nvim (maintained fork — provides in-editor `[[` completion): https://github.com/obsidian-nvim/obsidian.nvim
- glow (optional terminal renderer): https://github.com/charmbracelet/glow
- go-grip (optional, self-contained browser render — **not** the Python `grip`): https://github.com/chrishrb/go-grip

Note to implementer: re-fetch these at build time rather than trusting URLs blindly (doc sites move). The Obsidian help repo's `llms.txt` is the most durable anchor.

---

## 18. Open questions (deferred, not blocking MVP)

- Whether to *read* `.obsidian/` settings (daily-note folder, new-link format) to auto-match an existing Obsidian vault's config, versus always using hoppus config.
- Whether hoppus should emit a titles/aliases completion feed for editors lacking an Obsidian plugin (portable `[[` completion beyond nvim).
- Raising the graph hop cap above 5 (one-constant change; gated only on readability + node budget).
- Inline terminal image rendering approach (kitty/iTerm2 protocol vs. optional binary).
- Semantic/RAG search backend once keyword search is outgrown.
- tmux split-pane editor mode.

---

## 19. Implementation decisions addendum (2026-07-02)

Seven decisions from the post-planning spec review. Where any conflicts with an earlier
section, **this section wins**. Each is tracked on the kanban board via a `[D#]` tag.

### D1 — Link-integrity prompts fire on editor-return only
Amends §6.3, §7.3, §8, §12. The interactive correct-or-create flow (fuzzy "did you
mean?" + create-at-root) runs **only on return from a `$EDITOR` session**. **Vault-open
and watcher (external-edit) events are report-only** — unresolved links surface in
`hop audit` and a TUI "problems" indicator, never as blocking modals. This prevents a
prompt-storm when opening a vault edited externally (Obsidian, `git pull`) and avoids
racing a concurrent Obsidian edit. New config key:
`link_integrity.prompt_on: editor_return_only` (default) `| always`.

### D2 — MCP writes resolve unresolved links non-interactively
Amends §11. Interactive fuzzy prompts can't run for agent writes. Default behavior:
**reject the write and return the unresolved links plus near-match suggestions** so the
agent can retry with a corrected/confirmed target. Write tools (`create_note`,
`update_note`, `append_to_note`) take an opt-in param `on_unresolved: reject | create`
(`reject` = default; `create` bootstraps the missing note at vault root, mirroring the
TUI "none of these" path). No silent orphan notes by default.

### D3 — Textual does not render OFM links natively (build risk → spike)
Amends §9.5; adds a spike card in Epic 2. Textual's `Markdown`/`MarkdownViewer` widgets
are CommonMark; `[[wikilinks]]` and `![[embeds]]` render as literal text and never emit
`LinkClicked`. The implementation must **pre-transform OFM into navigable links (or
custom-render) and handle heading/block-anchor scroll targets itself**. This is scoped as
its own spike + card, not folded into "preview," and gates Epic 3's link navigation.

### D4 — Frontmatter uses ruamel.yaml (round-trip preserving)
Firms up §4 (was "PyYAML or ruamel"). The byte-compatibility promise (§14) requires
order/format/comment-preserving YAML wherever hoppus **writes** frontmatter (alias/tag
edits, template-seeded creates). **ruamel.yaml is the decision.** Config-file YAML (§12)
may use whatever, but note frontmatter round-trips through ruamel.

### D5 — Watcher suppresses hoppus's own writes
Amends §8. hoppus's own file writes (rename propagation, auto-created notes,
frontmatter edits) must not re-trigger the integrity/index pass on their own output. Add
**debounce + self-write path suppression** to the watcher/indexer as an explicit
requirement, with tests covering the no-feedback-loop guarantee.

### D6 — Rename propagation edge cases are defined
Amends §6.2; extra tests in §14. Define and test: (a) **shortest-unique-path
re-simplification** — when a rename changes which filenames are unique, links to
*unrelated* notes may need to gain or shed leading path components to stay correct and
minimal; (b) **rename-into-existing-name collision** — refuse or disambiguate rather than
silently merge. This is the "carefully tested" operation §6.2 flags.

### D7 — PyPI Phase 0 is a prepare/publish split
Amends §15/§16. The outer orchestration **prepares** the stub distribution (pyproject,
MIT license, placeholder README, `src/hoppus/`, version `0.0.1`); the **user runs the
authenticated `uv publish`** with their own PyPI token (irreversible external action, not
automated).
