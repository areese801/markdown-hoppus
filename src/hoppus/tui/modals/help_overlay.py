"""
Help overlay: the live keymap cheat-sheet (spec §9.16, HOPPUS-97).

A read-only modal listing the app's ACTUAL keybindings — the §9.16
defaults with any ``keymap:`` config overrides already applied — plus
the widget-level bindings (File Explorer CRUD and vim navigation, list
panes). The sections are precomputed by the app (see
``HoppusApp._help_sections``) so this modal only formats and displays.
``?`` or ``Escape`` closes it.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

#: One help section: (title, [(key display, description), ...]).
HelpSection = tuple[str, list[tuple[str, str]]]


class HelpScreen(ModalScreen[None]):
    """
    Read-only keymap cheat-sheet (HOPPUS-97).
    """

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("question_mark", "cancel", "Close", show=False),
    ]

    CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-panel {
        width: 70%;
        max-width: 78;
        height: auto;
        max-height: 85%;
        border: round $primary;
        background: $surface;
        padding: 1 2;
    }
    #help-title {
        text-style: bold;
    }
    .help-section-title {
        text-style: bold;
        margin-top: 1;
    }
    """

    def __init__(self, sections: list[HelpSection]) -> None:
        """
        Args:
            sections: Precomputed (title, rows) pairs, where each row is
                a (key display, description) tuple reflecting the
                current — possibly remapped — keymap.
        """
        super().__init__()
        self._sections = sections

    def compose(self) -> ComposeResult:
        """
        Build the cheat-sheet: a titled row block per section.
        """
        with VerticalScroll(id="help-panel"):
            yield Static("Keymap", id="help-title")
            for title, rows in self._sections:
                yield Static(title, classes="help-section-title")
                for key, description in rows:
                    yield Static(
                        f"  [bold]{key:>12}[/bold]  {description}",
                        classes="help-row",
                    )
            yield Static("[dim]? or Esc to close[/dim]", id="help-hint")

    def action_cancel(self) -> None:
        """
        Close the help overlay.
        """
        self.dismiss(None)
