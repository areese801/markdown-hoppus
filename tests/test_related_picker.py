"""
Headless Pilot tests for the ``## Related`` link picker flow
(spec §7.2, HOPPUS-41): existing-note pick appends and writes, a
new-name pick bootstraps the note at the vault root, and a dedupe pick
leaves the file unchanged.

Where the full key-driven modal round-trip would be timing-sensitive,
the tests stub ``_pick_related_target`` and drive
``_link_note_flow`` directly, asserting on file contents.
"""

import asyncio
from pathlib import Path
from typing import Any

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.modals.related_picker import RelatedChoice, RelatedPickerModal

_CURRENT_MD = """\
# Current

Some prose.
"""


def make_vault(tmp_path: Path) -> Path:
    """
    Build a minimal temp vault with an active note and one candidate.

    :param tmp_path: pytest's per-test temp directory.
    :returns: The vault root.
    """
    (tmp_path / "Current.md").write_text(_CURRENT_MD, encoding="utf-8")
    (tmp_path / "Existing.md").write_text("# Existing\n", encoding="utf-8")
    return tmp_path


def make_app(vault_root: Path) -> HoppusApp:
    """
    Build a HoppusApp whose active vault is the given directory.

    :param vault_root: Directory to use as the vault.
    :returns: An unmounted HoppusApp instance.
    """
    config: dict[str, Any] = default_config()
    return HoppusApp(config=config, vaults_root=vault_root)


def _stub_choice(app: HoppusApp, choice: RelatedChoice | None) -> None:
    """
    Replace the modal round-trip with a canned choice.

    :param app: The app under test.
    :param choice: The choice ``_pick_related_target`` should return.
    """

    async def fake(index: Any, vault_root: Path) -> RelatedChoice | None:
        return choice

    app._pick_related_target = fake  # type: ignore[method-assign]


def test_existing_pick_appends_under_related(tmp_path: Path) -> None:
    """
    Picking an existing note appends ``- [[Existing]]`` and writes.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Current.md", vault_root=vault)
            index = app._active_index(vault)
            note = next(
                n for n in index.notes_by_path.values() if n.title == "Existing"
            )
            _stub_choice(app, RelatedChoice(note=note))
            await app._link_note_flow()
            await pilot.pause()
        text = (vault / "Current.md").read_text(encoding="utf-8")
        assert text == _CURRENT_MD + "\n## Related\n- [[Existing]]\n"

    asyncio.run(run())


def test_new_name_pick_bootstraps_and_links(tmp_path: Path) -> None:
    """
    A new-name pick creates ``New.md`` at the vault root AND links it.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Current.md", vault_root=vault)
            _stub_choice(app, RelatedChoice(new_name="New"))
            await app._link_note_flow()
            await pilot.pause()
        assert (vault / "New.md").is_file()
        text = (vault / "Current.md").read_text(encoding="utf-8")
        assert "## Related\n- [[New]]\n" in text

    asyncio.run(run())


def test_dedupe_pick_leaves_file_unchanged(tmp_path: Path) -> None:
    """
    Picking an already-linked note changes nothing on disk.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        already = _CURRENT_MD + "\n## Related\n- [[Existing]]\n"
        (vault / "Current.md").write_text(already, encoding="utf-8")
        app = make_app(vault)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Current.md", vault_root=vault)
            index = app._active_index(vault)
            note = next(
                n for n in index.notes_by_path.values() if n.title == "Existing"
            )
            _stub_choice(app, RelatedChoice(note=note))
            await app._link_note_flow()
            await pilot.pause()
        assert (vault / "Current.md").read_text(encoding="utf-8") == already

    asyncio.run(run())


def test_cancel_writes_nothing(tmp_path: Path) -> None:
    """
    A cancelled picker (None) leaves the file untouched.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Current.md", vault_root=vault)
            _stub_choice(app, None)
            await app._link_note_flow()
            await pilot.pause()
        assert (vault / "Current.md").read_text(encoding="utf-8") == _CURRENT_MD

    asyncio.run(run())


def test_l_key_opens_picker_and_enter_links(tmp_path: Path) -> None:
    """
    The full key-driven path: ``l`` opens the modal; typing and Enter
    pick the existing note and append the link.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Current.md", vault_root=vault)
            await pilot.press("L")
            await pilot.pause()
            assert isinstance(app.screen, RelatedPickerModal)
            await pilot.press(*"existing")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
        text = (vault / "Current.md").read_text(encoding="utf-8")
        assert "## Related\n- [[Existing]]\n" in text

    asyncio.run(run())


def test_l_without_active_note_notifies(tmp_path: Path) -> None:
    """
    ``l`` with no active note notifies and pushes no modal.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            await pilot.press("L")
            await pilot.pause()
            assert not isinstance(app.screen, RelatedPickerModal)

    asyncio.run(run())


def test_create_row_offered_for_unmatched_query(tmp_path: Path) -> None:
    """
    A query matching no title/alias exactly gets a create-and-link row
    that dismisses with the raw name.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            await app.open_note(vault / "Current.md", vault_root=vault)
            await pilot.press("L")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, RelatedPickerModal)
            await pilot.press(*"Brand New Idea")
            await pilot.pause()
            create_rows = [c for c in screen._results if c.new_name == "Brand New Idea"]
            assert len(create_rows) == 1

            await pilot.press("escape")
            await pilot.pause()
        assert (vault / "Current.md").read_text(encoding="utf-8") == _CURRENT_MD

    asyncio.run(run())


def test_related_choice_defaults() -> None:
    """
    ``RelatedChoice`` fields default to None.
    """
    choice = RelatedChoice()
    assert choice.note is None and choice.new_name is None
