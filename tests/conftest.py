"""
Shared test fixtures for the markdown-hoppus suite (HOPPUS-64).

The ``sample_vault`` fixture builds a temp-dir, Obsidian-compatible sample
vault exercising the full OFM grammar from spec §6, so parser, resolution,
and index tests across the suite can all rely on a single canonical vault.

Vault contents:

- ``Index.md`` — every link syntax from spec §6.1: ``[[Note]]``,
  ``[[Note|Display]]``, ``[[Note#Heading]]``, ``[[Note#^block-id]]``,
  ``[[#Heading]]``, ``[[#^block-id]]``, ``![[Note]]``, ``![[Note#Heading]]``,
  ``![[image.png]]``, ``![[image.png|300]]``, ``[text](note.md)``, and a
  trailing ``^block-id`` definition.
- ``Target Note.md`` — headings and a ``^quote-1`` block id to resolve
  anchors against; ``aliases:`` and ``tags:`` YAML frontmatter.
- ``Tags Note.md`` — inline ``#tag`` and nested ``#area/sub`` tags, a
  ``#not-a-tag`` token inside a fenced code block (must NOT count as a tag),
  and a heading line starting with ``#`` (also not a tag).
- ``Projects/Alpha.md`` and ``Archive/Alpha.md`` — duplicate titles in
  different folders, for shortest-unique-path disambiguation tests.
- ``attachments/image.png`` — a 1-byte attachment referenced by embeds.
- ``.obsidian/app.json`` — foreign Obsidian state that must stay untouched.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from hoppus.tui.app import HoppusApp


def pytest_configure(config: pytest.Config) -> None:
    """
    Register the suite's custom markers.

    :param config: pytest's config object.
    """
    config.addinivalue_line(
        "markers",
        "no_index_settle: do not wait for the launch-time index worker "
        "inside HoppusApp.run_test (HOPPUS-78 mid-indexing tests)",
    )


@pytest.fixture(autouse=True)
def settle_initial_index(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Make ``HoppusApp.run_test`` wait for the launch-time index worker.

    HOPPUS-78 moved the mount-time index build + audit into a thread
    worker, so a bare ``run_test`` now yields *while indexing is still
    in flight*. The suite's Pilot tests were written against the fully
    launched state (problems count populated, watcher started), so this
    autouse fixture settles that worker deterministically before each
    test body runs. Tests that need to observe the mid-indexing state
    opt out with ``@pytest.mark.no_index_settle``.
    """
    if request.node.get_closest_marker("no_index_settle"):
        return
    original_run_test = HoppusApp.run_test

    @asynccontextmanager
    async def settled_run_test(
        self: HoppusApp, *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]:
        async with original_run_test(self, *args, **kwargs) as pilot:
            if self._launch_worker is not None:
                await self._launch_worker.wait()
            await pilot.pause()
            yield pilot

    monkeypatch.setattr(HoppusApp, "run_test", settled_run_test)


_INDEX_MD = """\
---
tags:
  - index
---

# Index

A plain wikilink: [[Target Note]].
A display-text wikilink: [[Target Note|the target]].
A heading link: [[Target Note#Section One]].
A block link: [[Target Note#^quote-1]].
A same-note heading link: [[#Local Heading]].
A same-note block link: [[#^local-block]].

A note embed: ![[Target Note]]
A heading embed: ![[Target Note#Section One]]
An attachment embed: ![[image.png]]
A sized attachment embed: ![[image.png|300]]

A standard markdown link: [the target](Target Note.md).
A duplicate-title link: [[Projects/Alpha]] vs [[Archive/Alpha]].

## Local Heading

This paragraph defines a block id. ^local-block
"""

_TARGET_NOTE_MD = """\
---
aliases:
  - The Target
  - target-note
tags:
  - reference
  - area/sub
---

# Target Note

Introductory text.

## Section One

Content under section one, with a quotable line. ^quote-1

## Section Two

More content, linking back to [[Index]].
"""

_TAGS_NOTE_MD = """\
---
tags: [frontmatter-tag]
---

# Heading Is Not A Tag

Inline tags here: #inline-tag and nested #area/sub.

```python
# This is a comment, and #not-a-tag must not count as a tag.
print("hello")
```

Closing text with one more #closing-tag.
"""

_ALPHA_PROJECTS_MD = """\
# Alpha (Projects)

The active Alpha project. See also [[Archive/Alpha]].
"""

_ALPHA_ARCHIVE_MD = """\
# Alpha (Archive)

The archived Alpha project. See also [[Projects/Alpha]].
"""


@pytest.fixture(scope="session")
def sample_vault(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    Build the shared sample vault in a session-scoped temp dir.

    :param tmp_path_factory: pytest's session-scoped temp path factory.
    :returns: The vault root as a ``Path``.
    """
    vault = tmp_path_factory.mktemp("sample_vault")

    (vault / "Index.md").write_text(_INDEX_MD, encoding="utf-8")
    (vault / "Target Note.md").write_text(_TARGET_NOTE_MD, encoding="utf-8")
    (vault / "Tags Note.md").write_text(_TAGS_NOTE_MD, encoding="utf-8")

    projects = vault / "Projects"
    projects.mkdir()
    (projects / "Alpha.md").write_text(_ALPHA_PROJECTS_MD, encoding="utf-8")

    archive = vault / "Archive"
    archive.mkdir()
    (archive / "Alpha.md").write_text(_ALPHA_ARCHIVE_MD, encoding="utf-8")

    attachments = vault / "attachments"
    attachments.mkdir()
    (attachments / "image.png").write_bytes(b"\x00")

    obsidian = vault / ".obsidian"
    obsidian.mkdir()
    (obsidian / "app.json").write_text('{"untouched": true}\n', encoding="utf-8")

    return vault
