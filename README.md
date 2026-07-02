# markdown-hoppus

> A terminal-first, TUI Markdown knowledge base. Think Obsidian, but living
> entirely in the terminal, editing delegated to `$EDITOR`, and storing nothing
> but plain `.md` files on disk.

**Status: placeholder stub (`0.0.1`).** This release only reserves the
`markdown-hoppus` name on PyPI. The real tool is under active development — see
[`spec.md`](spec.md) for the full specification.

- **Distribution:** `markdown-hoppus` (install: `uv tool install markdown-hoppus`)
- **Import package:** `hoppus`
- **Console commands:** `hoppus`, `mark`, `hop` — three interchangeable entry points
- **License:** MIT

## What it will be

`hoppus` reimplements Obsidian's core — a folder of plain-text Markdown files —
as an open-source, MIT-licensed, terminal-native tool. It is **standalone** (never
depends on Obsidian) while remaining **round-trip compatible** with an Obsidian
vault: the same folder opens interchangeably in either tool with no conversion and
no data loss.

Planned MVP highlights (see `spec.md`): a Textual TUI (file explorer, preview,
backlinks, tags, command palette, quick switcher), full Obsidian-Flavored-Markdown
link support, strict link integrity (no dangling wikilinks), a local N-hop graph
view, fuzzy search, daily notes and templates, and a bundled read-write MCP server
so AI agents can operate on the vault alongside you.

## License

MIT — see [`LICENSE`](LICENSE).
