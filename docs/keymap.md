# Keymap Reference

Default keybindings for the markdown-hoppus TUI (spec §9.16). Every binding
has a stable action name and can be remapped via your config file (see
[Remapping](#remapping) below). The tables reflect `DEFAULT_KEYMAP` in
`src/hoppus/tui/keymap.py`. Press `?` in the app for a live cheat-sheet
that shows your actual (possibly remapped) keys.

Note: note editing happens in your `$EDITOR`, not inside the TUI.

Single-character keys never hijack text Inputs: while a search box or
prompt is focused, printable keys go to the Input, not to these actions.

## Navigation & search

| Action | Default key | Description |
|---|---|---|
| `help` | `?` | Help overlay (keymap cheat-sheet); `?` or `Esc` closes |
| `command_palette` | `p` | Palette |
| `quick_switcher` | `o` | Open note |
| `search` | `f` | Search |

## Panes & layout

| Action | Default key | Description |
|---|---|---|
| `cycle_pane` | `Tab` | Cycle panes |
| `focus_pane_1` | `1` | Focus the File Explorer |
| `focus_pane_2` | `2` | Focus the main (preview) pane |
| `focus_pane_3` | `3` | Focus the Backlinks pane |
| `toggle_right_sidebar` | `\` | Backlinks |
| `tag_pane` | `t` | Tags |

## Notes & productivity

| Action | Default key | Description |
|---|---|---|
| `open_editor` | `e` | Editor |
| `open_system` | `O` | Open in app |
| `open_browser` | `b` | Browser |
| `link_note` | `L` | Link to… |
| `daily_note` | `d` | Daily note |
| `new_note` | `n` | New note |
| `new_note_from_template` | `N` | New from template |
| `quick_capture` | `c` | Quick capture |
| `toggle_star` | `s` | Star |
| `yank_menu` | `y` | Yank |

## Graph

| Action | Default key | Description |
|---|---|---|
| `toggle_graph` | `g` | Graph |
| `graph_radius_up` | `+` | Hops + |
| `graph_radius_down` | `-` | Hops - |

## App

| Action | Default key | Description |
|---|---|---|
| `reindex` | `r` | Reindex |
| `vault_switcher` | `v` | Vault |
| `quit` | `q` | Quit (two-step; see below) |

### Quitting

A single `q` arms the quit and shows "Press q again to quit"; a second `q`
within ~2 seconds exits immediately (so `qq` is a fast exit). Pressing any
other key — `Esc` included — cancels the armed quit, as does letting the
window expire. Textual's built-in `Ctrl+Q` follows the same two-step flow.

## Vim-style navigation (widget-level)

These are bound directly on the panes, so they only fire while that pane
is focused and are not remappable via the `keymap:` config section.

### File Explorer (focused)

| Key | Description |
|---|---|
| `j` / `k` | Move the cursor down / up |
| `h` | Collapse the folder under the cursor, or jump to its parent |
| `l` | Expand the folder under the cursor, or open the file |
| `g` / `G` | Jump to the first / last entry |
| `Ctrl+N` | New note (at the vault root) |
| `Ctrl+Shift+N` | New folder |
| `F2` | Rename |
| `Delete` | Delete (with confirmation) |
| `Ctrl+M` | Move |

Note: while the explorer is focused, `g` jumps to the first entry instead
of toggling the graph — press `g` with another pane focused (or remap
`toggle_graph`) to open the graph from the explorer context.

### Backlinks & Bookmarks lists (focused)

| Key | Description |
|---|---|
| `j` / `k` | Move the selection down / up |
| `g` / `G` | Jump to the first / last entry |

## Other app keys

| Key | Description |
|---|---|
| `m` | Toggle raw/rendered preview |
| `u` | Unlinked mentions |
| `w` | Vault stats |

## Remapping

Override any binding under the `keymap:` section of your config, mapping an
action name (the first column above) to a new key:

```yaml
keymap:
  quit: ctrl+q
  search: slash
  focus_pane_3: "9"
```

Keys use Textual's key names (e.g. `ctrl+p`, `tab`). Single-character
punctuation may be written either literally (`?`, `\`, `+`, `-`, `|`, `/`,
`.`, `,`) or with the long Textual name (`question_mark`, `backslash`,
`plus`, `minus`, `vertical_line`, `slash`, `full_stop`, `comma`) — both
forms are normalized automatically. Entries for unknown action names are
ignored, so stale config entries cannot break app startup.

## Footer visibility

Only a subset of bindings is shown in the app footer: `help`,
`command_palette`, `quick_switcher`, `search`, `cycle_pane`,
`toggle_right_sidebar`, and `quit`. All other bindings work but stay
hidden from the footer.
