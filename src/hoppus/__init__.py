"""
markdown-hoppus — a terminal-first, TUI Markdown knowledge base.

This is a placeholder stub release (0.0.1) that reserves the ``markdown-hoppus``
distribution name on PyPI. The real tool — an Obsidian-compatible, standalone
terminal knowledge base (see ``spec.md``) — lands in later releases.

The distribution is named ``markdown-hoppus``; the import package is ``hoppus``.
Distribution and import names are independent by design (spec §1, §15).
"""

__version__ = "0.0.1"


def main() -> None:
    """
    Stub entry point for the ``hoppus`` / ``mark`` / ``hop`` console commands.

    Prints a short notice until the real CLI (spec §10) is implemented. All three
    console scripts resolve here so the commands exist as soon as the stub is
    installed.
    """
    print(
        f"markdown-hoppus {__version__} — placeholder stub. "
        "The terminal Markdown knowledge base is under construction."
    )
