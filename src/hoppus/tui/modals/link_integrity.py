"""
Link-integrity prompt modal: correct-or-create for one unresolved
wikilink (spec §7.3, §19 D1, HOPPUS-39).

Shown sequentially — one per unresolved link, in document order — on
return from a ``$EDITOR`` session. The modal presents the "did you
mean?" suggestions plus a create row and a skip row; it holds no
business logic, dismissing with a :data:`hoppus.integrity.Decision`
that ``hoppus.integrity.apply_integrity_decisions`` consumes.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from hoppus.integrity import Decision, UnresolvedWikilink


class LinkIntegrityModal(ModalScreen[Decision]):
    """
    One §7.3 prompt: pick a near-match, create the note, or skip.
    """

    BINDINGS = [
        Binding("escape", "skip", "Skip"),
    ]

    CSS = """
    LinkIntegrityModal {
        align: center middle;
    }
    #integrity {
        width: 70%;
        max-width: 90;
        height: auto;
        max-height: 80%;
        border: round $warning;
        background: $surface;
    }
    #integrity-title {
        padding: 0 1;
        text-style: bold;
    }
    #integrity-options {
        height: auto;
        max-height: 20;
    }
    """

    def __init__(self, unresolved: UnresolvedWikilink) -> None:
        """
        :param unresolved: The unresolved occurrence to prompt about.
        """
        super().__init__()
        self._unresolved = unresolved

    def compose(self) -> ComposeResult:
        """
        Build the unresolved-link label and the choice list.
        """
        link = self._unresolved.link
        bang = "!" if link.is_embed else ""
        with Vertical(id="integrity"):
            yield Static(f"Unresolved: {bang}[[{link.target}]]", id="integrity-title")
            options = [
                Option(note.title, id=f"suggestion-{index}")
                for index, note in enumerate(self._unresolved.suggestions)
            ]
            options.append(Option(f"＋ Create ‘{link.target}’", id="create"))
            options.append(Option("Skip", id="skip"))
            yield OptionList(*options, id="integrity-options")

    def on_mount(self) -> None:
        """
        Focus the choice list with the best suggestion highlighted.
        """
        options = self.query_one("#integrity-options", OptionList)
        options.highlighted = 0
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """
        Dismiss with the decision for the chosen row.
        """
        event.stop()
        option_id = event.option.id
        if option_id == "create":
            self.dismiss(("create", None))
        elif option_id == "skip":
            self.dismiss(("skip", None))
        else:
            index = event.option_index
            self.dismiss(("correct", self._unresolved.suggestions[index]))

    def action_skip(self) -> None:
        """
        Escape leaves this occurrence as-is (spec §7.3).
        """
        self.dismiss(("skip", None))
