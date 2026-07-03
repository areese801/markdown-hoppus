"""
Vault stats modal: render a ``VaultStats`` snapshot (spec §9.13,
HOPPUS-53).

A thin, read-only panel of Static lines — note count, total words,
distinct tags, top tags, orphans, and unresolved links. All aggregation
happens in :mod:`hoppus.stats` before the screen is pushed; this modal
only formats and displays. ``Escape`` closes it.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from hoppus.stats import VaultStats


class StatsScreen(ModalScreen[None]):
    """
    Read-only vault statistics panel (spec §9.13).
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
    ]

    CSS = """
    StatsScreen {
        align: center middle;
    }
    #stats-panel {
        width: 60%;
        max-width: 70;
        height: auto;
        max-height: 80%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    #stats-title {
        text-style: bold;
    }
    """

    def __init__(self, stats: VaultStats) -> None:
        """
        Args:
            stats: The precomputed vault statistics to display.
        """
        super().__init__()
        self._stats = stats

    def _top_tags_line(self) -> str:
        """
        Format the top-tags summary line.

        :returns: ``"tag (n), tag (n), …"`` or ``"(none)"`` when the
            vault has no tags.
        """
        if not self._stats.top_tags:
            return "(none)"
        return ", ".join(f"{tag} ({count})" for tag, count in self._stats.top_tags)

    def compose(self) -> ComposeResult:
        """
        Build the stats panel: one Static line per statistic.
        """
        stats = self._stats
        with Vertical(id="stats-panel"):
            yield Static("Vault stats", id="stats-title")
            yield Static(f"Notes: {stats.note_count}", id="stats-notes")
            yield Static(f"Total words: {stats.total_words}", id="stats-words")
            yield Static(f"Distinct tags: {stats.tag_count}", id="stats-tags")
            yield Static(f"Top tags: {self._top_tags_line()}", id="stats-top-tags")
            yield Static(f"Orphans: {stats.orphan_count}", id="stats-orphans")
            yield Static(
                f"Unresolved links: {stats.unresolved_link_count}",
                id="stats-unresolved",
            )
            yield Static("[dim]Esc to close[/dim]", id="stats-hint")

    def action_cancel(self) -> None:
        """
        Close the stats view.
        """
        self.dismiss(None)
