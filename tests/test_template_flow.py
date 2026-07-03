"""
Headless Pilot tests for the new-note-from-template flow (spec §9.10,
HOPPUS-50): the ``N``-bound action pushes the picker, and the full flow
(stubbed modal results, per the related-picker pattern) creates the
note at the vault root with substituted content and opens it in the
preview.
"""

import asyncio
from pathlib import Path
from typing import Any

from hoppus.config import default_config
from hoppus.tui.app import HoppusApp
from hoppus.tui.modals.template_picker import TemplatePickerModal

_TEMPLATE_MD = "# {{title}}\n{{date}}\n"


def make_vault(tmp_path: Path) -> Path:
    """
    Build a minimal temp vault with a ``Templates/note.md`` template.

    :param tmp_path: pytest's per-test temp directory.
    :returns: The vault root.
    """
    folder = tmp_path / "Templates"
    folder.mkdir()
    (folder / "note.md").write_text(_TEMPLATE_MD, encoding="utf-8")
    return tmp_path


def make_app(vault_root: Path) -> HoppusApp:
    """
    Build a HoppusApp whose active vault is the given directory.

    :param vault_root: Directory to use as the vault.
    :returns: An unmounted HoppusApp instance.
    """
    config: dict[str, Any] = default_config()
    return HoppusApp(config=config, vaults_root=vault_root)


def _stub_modals(app: HoppusApp, template: Path | None, title: str | None) -> None:
    """
    Replace both modal round-trips with canned results.

    :param app: The app under test.
    :param template: What ``_pick_template`` should return.
    :param title: What ``_prompt_template_title`` should return.
    """

    async def fake_pick(choices: list[Path]) -> Path | None:
        return template

    async def fake_title() -> str | None:
        return title

    app._pick_template = fake_pick  # type: ignore[method-assign]
    app._prompt_template_title = fake_title  # type: ignore[method-assign]


def test_flow_creates_substituted_note_and_opens_it(tmp_path: Path) -> None:
    """
    The stubbed flow creates ``Fresh.md`` at the root with the
    template's variables substituted, and opens it in the preview.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            _stub_modals(app, vault / "Templates" / "note.md", "Fresh")
            await app._new_note_from_template_flow(vault)
            await pilot.pause()
            assert app.preview.note_path == vault / "Fresh.md"
        text = (vault / "Fresh.md").read_text(encoding="utf-8")
        assert text.startswith("# Fresh\n")
        assert "{{" not in text

    asyncio.run(run())


def test_cancelled_picker_creates_nothing(tmp_path: Path) -> None:
    """
    Cancelling the template picker leaves the vault untouched.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            _stub_modals(app, None, "Fresh")
            await app._new_note_from_template_flow(vault)
            await pilot.pause()
        assert not (vault / "Fresh.md").exists()

    asyncio.run(run())


def test_cancelled_title_creates_nothing(tmp_path: Path) -> None:
    """
    Cancelling the title prompt leaves the vault untouched.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            _stub_modals(app, vault / "Templates" / "note.md", None)
            await app._new_note_from_template_flow(vault)
            await pilot.pause()
        assert list(vault.glob("*.md")) == []

    asyncio.run(run())


def test_collision_notifies_and_preserves_existing(tmp_path: Path) -> None:
    """
    A title colliding with an existing note refuses and keeps the
    original content.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        (vault / "Taken.md").write_text("original", encoding="utf-8")
        app = make_app(vault)
        async with app.run_test() as pilot:
            _stub_modals(app, vault / "Templates" / "note.md", "Taken")
            await app._new_note_from_template_flow(vault)
            await pilot.pause()
        assert (vault / "Taken.md").read_text(encoding="utf-8") == "original"

    asyncio.run(run())


def test_capital_n_opens_template_picker(tmp_path: Path) -> None:
    """
    The ``N`` keybind pushes the template picker modal.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        async with app.run_test() as pilot:
            await pilot.press("N")
            await pilot.pause()
            assert isinstance(app.screen, TemplatePickerModal)
            await pilot.press("escape")
            await pilot.pause()

    asyncio.run(run())


def test_capital_n_with_no_templates_notifies(tmp_path: Path) -> None:
    """
    ``N`` in a vault without templates notifies instead of pushing an
    empty picker.
    """

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.press("N")
            await pilot.pause()
            assert not isinstance(app.screen, TemplatePickerModal)

    asyncio.run(run())


def test_picker_enter_selects_template(tmp_path: Path) -> None:
    """
    Enter in the picker dismisses with the highlighted template and
    the flow proceeds to the title prompt.
    """

    async def run() -> None:
        vault = make_vault(tmp_path)
        app = make_app(vault)
        picked: list[Path | None] = []

        original = TemplatePickerModal.dismiss

        async with app.run_test() as pilot:

            async def fake_title() -> str | None:
                return None

            app._prompt_template_title = fake_title  # type: ignore[method-assign]

            def spy(self: TemplatePickerModal, result: Path | None = None) -> Any:
                picked.append(result)
                return original(self, result)

            TemplatePickerModal.dismiss = spy  # type: ignore[method-assign]
            try:
                await pilot.press("N")
                await pilot.pause()
                assert isinstance(app.screen, TemplatePickerModal)
                await pilot.press("enter")
                await pilot.pause()
            finally:
                TemplatePickerModal.dismiss = original  # type: ignore[method-assign]
        assert picked == [vault / "Templates" / "note.md"]

    asyncio.run(run())
