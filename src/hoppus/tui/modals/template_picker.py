"""
Template picker modal: choose a template for a new note (spec §9.10,
HOPPUS-50).

A minimal ``OptionList`` of the vault's template names (as produced by
:func:`hoppus.templates.list_templates`). Enter (or a click) dismisses
with the chosen template :class:`~pathlib.Path`; ``Escape`` cancels
with ``None``. No logic lives here — the app owns the flow.
"""

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option


class TemplatePickerModal(ModalScreen[Path | None]):
    """
    Pick a template file; dismisses with its path, or None on cancel.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    TemplatePickerModal {
        align: center middle;
    }
    #template-picker {
        width: 60%;
        max-width: 70;
        height: auto;
        max-height: 80%;
        border: round $primary;
        background: $surface;
    }
    #template-picker-list {
        height: auto;
        max-height: 20;
    }
    """

    def __init__(self, templates: list[Path]) -> None:
        """
        Args:
            templates: The candidate template files (non-empty; the
                caller notifies and skips the modal when none exist).
        """
        super().__init__()
        self._templates = templates

    def compose(self) -> ComposeResult:
        """
        Build the title label and the template list.
        """
        with Vertical(id="template-picker"):
            yield Label("New note from template", id="template-picker-title")
            yield OptionList(
                *(Option(path.stem, id=str(path)) for path in self._templates),
                id="template-picker-list",
            )

    def on_mount(self) -> None:
        """
        Focus the list and highlight the first template.
        """
        options = self.query_one("#template-picker-list", OptionList)
        if options.option_count:
            options.highlighted = 0
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """
        Enter on (or a click of) a row dismisses with that template.
        """
        event.stop()
        if 0 <= event.option_index < len(self._templates):
            self.dismiss(self._templates[event.option_index])

    def action_cancel(self) -> None:
        """
        Dismiss with no selection.
        """
        self.dismiss(None)
