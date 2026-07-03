"""
Yank menu modal (spec §9.14, HOPPUS-54).

A small ``OptionList`` modal offering the three clipboard yanks for the
active note — Body, Path, and Wikilink — and dismissing with the chosen
kind (``"body"`` | ``"path"`` | ``"wikilink"``) or ``None`` on cancel.
The actual content building and copying live in ``hoppus.clipboard``;
this screen only reports the user's choice.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

YANK_BODY = "body"
YANK_PATH = "path"
YANK_WIKILINK = "wikilink"


class YankMenuModal(ModalScreen[str | None]):
    """
    Choose what to yank: note body, absolute path, or a wikilink.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    YankMenuModal {
        align: center middle;
    }
    #yank-menu {
        width: auto;
        min-width: 30;
        max-width: 60;
        height: auto;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }
    #yank-options {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        """
        Build the title label and the three yank choices.
        """
        with Vertical(id="yank-menu"):
            yield Label("Yank to clipboard", id="yank-title")
            yield OptionList(
                Option("Body", id=YANK_BODY),
                Option("Path", id=YANK_PATH),
                Option("Wikilink", id=YANK_WIKILINK),
                id="yank-options",
            )

    def on_mount(self) -> None:
        """
        Focus the list with the first choice highlighted.
        """
        options = self.query_one("#yank-options", OptionList)
        options.highlighted = 0
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """
        Dismiss with the selected yank kind.
        """
        event.stop()
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        """
        Dismiss with no selection.
        """
        self.dismiss(None)
