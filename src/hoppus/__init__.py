"""
markdown-hoppus — a terminal-first, TUI Markdown knowledge base.

The distribution is named ``markdown-hoppus``; the import package is ``hoppus``.
Distribution and import names are independent by design (spec §1, §15).
"""

__version__ = "0.1.0"

# Imported after __version__ so hoppus.cli can import it back without a cycle.
from hoppus.cli import main as _cli_main  # noqa: E402


def main() -> None:
    """
    Entry point for the ``hoppus`` / ``mark`` / ``hop`` console commands.

    Delegates to the typer CLI in :mod:`hoppus.cli`. All three console
    scripts resolve here (spec §1, §10).
    """
    _cli_main()
