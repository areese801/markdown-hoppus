"""
A reusable yes/no confirmation modal (HOPPUS-34).

Shows a message with Yes/No buttons and dismisses with a bool. Used
for the delete confirmation (spec §9.2) and the optional inbound-link
update prompt (``files.prompt_before_link_update``). ``Escape`` (or
``n``) answers No; ``y`` answers Yes.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmModal(ModalScreen[bool]):
    """
    Message + Yes/No; dismisses with True (Yes) or False (No).
    """

    BINDINGS = [
        Binding("escape", "answer(False)", "No"),
        Binding("n", "answer(False)", "No", show=False),
        Binding("y", "answer(True)", "Yes", show=False),
    ]

    CSS = """
    ConfirmModal {
        align: center middle;
    }
    #confirm {
        width: auto;
        min-width: 40;
        max-width: 70;
        height: auto;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }
    #confirm-buttons {
        height: auto;
        align-horizontal: right;
    }
    #confirm-buttons Button {
        margin-left: 2;
    }
    """

    def __init__(self, message: str) -> None:
        """
        Args:
            message: The question to show (e.g. "Delete 'Note.md'?").
        """
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        """
        Build the message and the Yes/No button row.
        """
        with Vertical(id="confirm"):
            yield Label(self._message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", variant="primary", id="confirm-yes")
                yield Button("No", id="confirm-no")

    def on_mount(self) -> None:
        """
        Focus No, so a stray Enter never confirms a destructive action.
        """
        self.query_one("#confirm-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """
        Dismiss with the pressed button's answer.
        """
        event.stop()
        self.dismiss(event.button.id == "confirm-yes")

    def action_answer(self, answer: bool) -> None:
        """
        Dismiss with a keyboard-chosen answer.

        Args:
            answer: True for Yes, False for No.
        """
        self.dismiss(answer)
