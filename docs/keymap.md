# Keymap Reference

Default keybindings for the markdown-hoppus TUI (spec §9.16). Every binding
has a stable action name and can be remapped via your config file (see
[Remapping](#remapping) below). The tables reflect `DEFAULT_KEYMAP` in
`src/hoppus/tui/keymap.py`.

Note: note editing happens in your `$EDITOR`, not inside the TUI.

## Navigation & search

| Action | Default key | Description |
|---|---|---|
| `help` | `?` | Help |
| `command_palette` | `Ctrl+P` | Palette |
| `quick_switcher` | `Ctrl+O` | Open note |
| `search` | `Ctrl+F` | Search |

## Panes & layout

| Action | Default key | Description |
|---|---|---|
| `cycle_pane` | `Tab` | Cycle panes |
| `toggle_right_sidebar` | `\` | Backlinks |
| `tag_pane` | `t` | Tags |

## Notes & productivity

| Action | Default key | Description |
|---|---|---|
| `open_editor` | `e` | Editor |
| `open_system` | `O` | Open in app |
| `open_browser` | `b` | Browser |
| `link_note` | `l` | Link to… |
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
| `quit` | `q` | Quit |

## Remapping

Override any binding under the `keymap:` section of your config, mapping an
action name (the first column above) to a new key:

```yaml
keymap:
  quit: ctrl+q
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
