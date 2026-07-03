"""
The hoppus command palette provider (spec §9.4, HOPPUS-29).

Builds on Textual's command palette: a registry of hoppus commands maps
human-readable titles to app action names, and ``HoppusCommandProvider``
surfaces them via fuzzy search (with a query) or discovery (no query).
Selecting a command runs the corresponding ``action_*`` method on
``HoppusApp`` — commands whose feature hasn't landed yet still appear,
and simply run the app's current stub action. Later stories extend the
palette by appending ``HoppusCommand`` specs to ``HOPPUS_COMMANDS``.
"""

from dataclasses import dataclass
from functools import partial

from textual.command import DiscoveryHit, Hit, Hits, Provider


@dataclass(frozen=True)
class HoppusCommand:
    """
    A command palette entry: a title mapped to an app action name.
    """

    title: str
    action: str
    help: str | None = None


HOPPUS_COMMANDS: tuple[HoppusCommand, ...] = (
    HoppusCommand(
        title="Open daily note",
        action="daily_note",
        help="Create or open today's daily note",
    ),
    HoppusCommand(
        title="New note from template",
        action="new_note_from_template",
        help="Create a note from a template",
    ),
    HoppusCommand(
        title="New note",
        action="new_note",
        help="Create a new note",
    ),
    HoppusCommand(
        title="Quick capture",
        action="quick_capture",
        help="Append a quick thought to the inbox",
    ),
    HoppusCommand(
        title="Toggle right sidebar",
        action="toggle_right_sidebar",
        help="Show or hide the backlinks sidebar",
    ),
    HoppusCommand(
        title="Cycle panes",
        action="cycle_pane",
        help="Move focus to the next visible pane",
    ),
    HoppusCommand(
        title="Toggle raw view",
        action="toggle_raw",
        help="Switch the preview between rendered and raw Markdown",
    ),
    HoppusCommand(
        title="Show tags tab",
        action="tag_pane",
        help="Switch the left sidebar to the Tags tab",
    ),
    HoppusCommand(
        title="Switch vault",
        action="vault_switcher",
        help="Switch to another vault",
    ),
    HoppusCommand(
        title="Reindex vault",
        action="reindex",
        help="Rescan the vault and rebuild the index",
    ),
    HoppusCommand(
        title="Search",
        action="search",
        help="Search titles and content",
    ),
    HoppusCommand(
        title="Quick switcher",
        action="quick_switcher",
        help="Fuzzy jump to any note",
    ),
    HoppusCommand(
        title="Open in $EDITOR",
        action="open_editor",
        help="Edit the active note in your editor",
    ),
    HoppusCommand(
        title="Open in system app",
        action="open_system",
        help="Open the active file with the system default app",
    ),
    HoppusCommand(
        title="Open in browser",
        action="open_browser",
        help="Render the active note locally and open it in a browser",
    ),
    HoppusCommand(
        title="Yank link",
        action="yank_menu",
        help="Copy a link to the active note",
    ),
    HoppusCommand(
        title="Link to…",
        action="link_note",
        help="Link the active note to another note",
    ),
    HoppusCommand(
        title="Star/unstar note",
        action="toggle_star",
        help="Toggle a bookmark on the active note",
    ),
    HoppusCommand(
        title="Graph view",
        action="toggle_graph",
        help="Show the local graph around the active note",
    ),
    HoppusCommand(
        title="Vault stats",
        action="vault_stats",
        help="Show statistics for the active vault",
    ),
    HoppusCommand(
        title="Help",
        action="help",
        help="Show the help and keymap overlay",
    ),
)


class HoppusCommandProvider(Provider):
    """
    Command palette source for hoppus commands (spec §9.4).
    """

    async def search(self, query: str) -> Hits:
        """
        Fuzzy-match the query against every registered command title.

        Args:
            query: The user's palette input.

        Yields:
            A scored ``Hit`` per matching command; selecting it runs the
            command's app action.
        """
        matcher = self.matcher(query)
        for spec in HOPPUS_COMMANDS:
            score = matcher.match(spec.title)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(spec.title),
                    partial(self.app.run_action, spec.action),
                    help=spec.help,
                )

    async def discover(self) -> Hits:
        """
        List every registered command when the palette opens empty.

        Yields:
            A ``DiscoveryHit`` per command.
        """
        for spec in HOPPUS_COMMANDS:
            yield DiscoveryHit(
                spec.title,
                partial(self.app.run_action, spec.action),
                help=spec.help,
            )
