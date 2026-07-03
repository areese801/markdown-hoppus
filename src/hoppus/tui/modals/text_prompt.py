"""
A reusable single-line text prompt modal (HOPPUS-34).

Shows a title, an ``Input`` (optionally pre-filled, e.g. for rename),
and OK/Cancel buttons. Dismisses with the entered string, or ``None``
on cancel/``Escape``. An optional ``validate`` callable (returning the
list of problems in a value, empty = valid) drives a live warning
label and disables OK while the value is invalid — used for the
reserved-character rules in :mod:`hoppus.naming` (spec §5.2).
"""

from collections.abc import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label


class TextPromptModal(ModalScreen[str | None]):
    """
    Title + ``Input`` + OK/Cancel; dismisses with the string or None.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    TextPromptModal {
        align: center middle;
    }
    #text-prompt {
        width: 60%;
        max-width: 70;
        height: auto;
        padding: 1 2;
        border: round $primary;
        background: $surface;
    }
    #text-prompt-warning {
        color: $warning;
        height: auto;
    }
    #text-prompt-buttons {
        height: auto;
        align-horizontal: right;
    }
    #text-prompt-buttons Button {
        margin-left: 2;
    }
    """

    def __init__(
        self,
        title: str,
        *,
        initial: str = "",
        placeholder: str = "",
        validate: Callable[[str], list[str]] | None = None,
    ) -> None:
        """
        Args:
            title: The prompt text shown above the input.
            initial: Initial input value (e.g. the old name for rename).
            placeholder: Input placeholder text.
            validate: Optional callable returning the problems in a
                value (empty list = valid); blocks OK while invalid.
        """
        super().__init__()
        self._title = title
        self._initial = initial
        self._placeholder = placeholder
        self._validate = validate

    def compose(self) -> ComposeResult:
        """
        Build the title, input, warning label, and button row.
        """
        with Vertical(id="text-prompt"):
            yield Label(self._title, id="text-prompt-title")
            yield Input(
                value=self._initial,
                placeholder=self._placeholder,
                id="text-prompt-input",
            )
            yield Label("", id="text-prompt-warning")
            with Horizontal(id="text-prompt-buttons"):
                yield Button("OK", variant="primary", id="text-prompt-ok")
                yield Button("Cancel", id="text-prompt-cancel")

    def on_mount(self) -> None:
        """
        Focus the input and validate the initial value.
        """
        self._refresh_validation(self._initial)
        self.query_one("#text-prompt-input", Input).focus()

    def _problems(self, value: str) -> list[str]:
        """
        Run the validator (if any) against a value.
        """
        return self._validate(value) if self._validate is not None else []

    def _refresh_validation(self, value: str) -> None:
        """
        Update the warning label and OK button for a value.
        """
        problems = self._problems(value)
        warning = self.query_one("#text-prompt-warning", Label)
        ok = self.query_one("#text-prompt-ok", Button)
        if problems:
            warning.update(f"Reserved: {' '.join(problems)}")
            ok.disabled = True
        else:
            warning.update("")
            ok.disabled = False

    def on_input_changed(self, event: Input.Changed) -> None:
        """
        Live-validate as the user types.
        """
        event.stop()
        self._refresh_validation(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """
        Enter accepts the value when it validates.
        """
        event.stop()
        if not self._problems(event.value):
            self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """
        OK dismisses with the (valid) value; Cancel with None.
        """
        event.stop()
        if event.button.id == "text-prompt-ok":
            value = self.query_one("#text-prompt-input", Input).value
            if not self._problems(value):
                self.dismiss(value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        """
        Dismiss with no value.
        """
        self.dismiss(None)
