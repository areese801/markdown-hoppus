"""
In-TUI search screen: live fuzzy finder with targeted operators and a
match preview (spec §9.8, HOPPUS-44).

An ``Input`` on top; every change parses the query
(:func:`hoppus.search.operators.parse_query`) and executes it
(:func:`hoppus.search.engine.search`) against the active index,
repopulating an ``OptionList`` of ranked results — each row shows the
note title plus a dimmed matching snippet or vault-relative path. Enter
(or a click) dismisses with the chosen :class:`SearchResult`; Escape
cancels. Results are capped so typing stays responsive, and query
execution is exception-guarded so a bad query never crashes the screen.
"""

from pathlib import Path

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from hoppus.index.indexer import Index
from hoppus.search.engine import SearchResult, search
from hoppus.search.operators import parse_query

#: Maximum results shown per query, to keep live typing responsive.
RESULT_LIMIT = 50


class SearchScreen(ModalScreen[SearchResult | None]):
    """
    Live search over titles and content with targeted operators
    (spec §9.8): type to search, Enter to open the highlighted note.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("down", "cursor(1)", "Next", show=False),
        Binding("up", "cursor(-1)", "Previous", show=False),
    ]

    CSS = """
    SearchScreen {
        align: center middle;
    }
    #search {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 80%;
        border: round $primary;
        background: $surface;
    }
    #search-results {
        height: auto;
        max-height: 20;
    }
    """

    def __init__(self, index: Index, vault_root: Path, backend: str = "auto") -> None:
        """
        Args:
            index: A built vault index to search.
            vault_root: The vault root, for rendering relative paths.
            backend: Content-search backend (``search.content_backend``).
        """
        super().__init__()
        self._index = index
        self._vault_root = vault_root
        self._backend = backend
        self._results: list[SearchResult] = []

    def compose(self) -> ComposeResult:
        """
        Build the query input and the ranked results list.
        """
        with Vertical(id="search"):
            yield Input(
                placeholder="Search (tag: title: path: key:value, bare = full-text)…",
                id="search-input",
            )
            yield OptionList(id="search-results")

    def on_mount(self) -> None:
        """
        Focus the input; the list starts empty until a query is typed.
        """
        self.query_one("#search-input", Input).focus()

    # -- Searching -----------------------------------------------------------

    def _prompt(self, result: SearchResult) -> str:
        """
        Render one result row: title plus a dim snippet/path hint.

        :param result: The search result to render.
        :returns: A Rich-markup prompt string.
        """
        if result.snippet is not None:
            hint = result.snippet.strip()
        else:
            try:
                hint = str(result.path.relative_to(self._vault_root))
            except ValueError:
                hint = str(result.path)
        return f"{escape(result.title)}  [dim]{escape(hint)}[/dim]"

    def update_results(self, query: str) -> None:
        """
        Parse and execute a query, repopulating the results list.

        Exceptions are swallowed into an empty result set so a bad
        query can never crash the screen.

        :param query: The current input text.
        """
        try:
            self._results = search(
                parse_query(query),
                self._index,
                backend=self._backend,
                limit=RESULT_LIMIT,
            )
        except Exception:
            self._results = []
        options = self.query_one("#search-results", OptionList)
        options.clear_options()
        options.add_options(
            Option(self._prompt(result), id=str(result.path))
            for result in self._results
        )
        if self._results:
            options.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        """
        Re-run the search live as the query changes.
        """
        event.stop()
        self.update_results(event.value)

    # -- Selection -----------------------------------------------------------

    def _highlighted_result(self) -> SearchResult | None:
        """
        Return the currently highlighted result, if any.
        """
        if not self._results:
            return None
        options = self.query_one("#search-results", OptionList)
        highlighted = options.highlighted
        if highlighted is None or not 0 <= highlighted < len(self._results):
            highlighted = 0
        return self._results[highlighted]

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """
        Enter dismisses with the highlighted result.
        """
        event.stop()
        result = self._highlighted_result()
        if result is not None:
            self.dismiss(result)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """
        Clicking (or Enter on) a row dismisses with that result.
        """
        event.stop()
        if event.option_index < len(self._results):
            self.dismiss(self._results[event.option_index])

    def action_cursor(self, delta: int) -> None:
        """
        Move the result highlight up or down while typing.

        :param delta: +1 for down, -1 for up.
        """
        options = self.query_one("#search-results", OptionList)
        if options.option_count == 0:
            return
        current = options.highlighted if options.highlighted is not None else 0
        options.highlighted = (current + delta) % options.option_count

    def action_cancel(self) -> None:
        """
        Dismiss with no selection.
        """
        self.dismiss(None)
