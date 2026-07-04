"""
Hot-path reindex tests (HOPPUS-84/85/86, HOPPUS-90).

The index/audit/watcher lifecycle must stay off the O(vault) paths once
the launch worker (HOPPUS-78) has populated the cache:

- Opening a note never runs the full-vault audit; the ⚠ N problems
  count stays correct (HOPPUS-84).
- Editor return does exactly one incremental ``reindex_file`` — never
  a whole-vault ``Index.build`` — and the problems count updates
  incrementally (HOPPUS-85).
- A watcher event delivered while a backlink selection is mid-dispatch
  neither crashes the OptionList nor triggers a full rebuild
  (HOPPUS-86).
- A programmatic note creation surfaces in the File Explorer without a
  restart (HOPPUS-90).

All Pilot tests run headless via ``App.run_test`` wrapped in
``asyncio.run``; spies are installed after the launch worker settles
(the autouse conftest fixture), so launch-time builds never count.
"""

import asyncio
from pathlib import Path

import pytest

import hoppus.index.watcher as watcher_module
from hoppus import integrity
from hoppus.config import default_config
from hoppus.index.indexer import Index
from hoppus.tui.app import HoppusApp
from hoppus.tui.panes.backlinks import BacklinksPane
from hoppus.tui.panes.explorer import ExplorerPane


class FakeObserver:
    """
    Observer stand-in so the watcher "starts" without any real thread.
    """

    def schedule(self, handler: object, path: str, recursive: bool = False) -> None:
        """No real scheduling."""

    def start(self) -> None:
        """No thread to start."""

    def stop(self) -> None:
        """No thread to stop."""

    def join(self) -> None:
        """No thread to join."""


def watcherless_config() -> dict:
    """
    A default config with live watching off, so a real watchdog
    observer can never race the deterministic spies in these tests.

    :returns: The config dict.
    """
    config = default_config()
    config["watcher"]["enabled"] = False
    return config


def forbid_full_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Make any further ``Index.build`` fail the test loudly.

    Installed after the launch worker settles, so it proves a code path
    stayed on the incremental ``reindex_file`` route.

    :param monkeypatch: pytest's monkeypatch fixture.
    """

    def explode(cls: type[Index], vault_root: Path) -> Index:
        raise AssertionError(f"full Index.build on a hot path for {vault_root}")

    monkeypatch.setattr(Index, "build", classmethod(explode))


def spy_reindex_file(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """
    Record every ``Index.reindex_file`` call, delegating to the real one.

    :param monkeypatch: pytest's monkeypatch fixture.
    :returns: The list collecting each reindexed path, in call order.
    """
    calls: list[Path] = []
    real_reindex = Index.reindex_file

    def spy(self: Index, path: Path) -> set[Path]:
        calls.append(Path(path))
        return real_reindex(self, path)

    monkeypatch.setattr(Index, "reindex_file", spy)
    return calls


def test_open_note_never_runs_full_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Opening a note is audit-free (HOPPUS-84): ``audit_vault`` is never
    called on the open path, yet the ⚠ count stays correct.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Alpha.md").write_text("See [[Nowhere]].\n", encoding="utf-8")
    (vault / "Beta.md").write_text("Link to [[Alpha]].\n", encoding="utf-8")

    async def run() -> None:
        app = HoppusApp(config=watcherless_config(), vaults_root=vault)
        async with app.run_test() as pilot:
            # The launch worker computed the count once (HOPPUS-78).
            assert app._problem_count == 1

            audits: list[Index] = []

            def spy_audit(index: Index, **kwargs: object) -> integrity.AuditReport:
                audits.append(index)
                raise AssertionError("full audit_vault on the note-open hot path")

            monkeypatch.setattr(integrity, "audit_vault", spy_audit)
            forbid_full_build(monkeypatch)

            await app.open_note(vault / "Alpha.md", vault_root=vault)
            await pilot.pause()
            await app.open_note(vault / "Beta.md", vault_root=vault)
            await pilot.pause()

            assert audits == []
            assert app._problem_count == 1

    asyncio.run(run())


def test_editor_return_is_one_incremental_reindex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Editor return folds the edit in with a single ``reindex_file`` —
    zero ``Index.build`` calls — and the problems count updates
    incrementally (HOPPUS-85).
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    alpha = vault / "Alpha.md"
    alpha.write_text("Link to [[Beta]].\n", encoding="utf-8")
    (vault / "Beta.md").write_text("Beta body.\n", encoding="utf-8")

    async def run() -> None:
        app = HoppusApp(config=watcherless_config(), vaults_root=vault)

        async def skip_all(unresolved: integrity.UnresolvedWikilink):
            return ("skip", None)

        app._prompt_unresolved = skip_all  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            assert app._problem_count == 0

            forbid_full_build(monkeypatch)
            reindexed = spy_reindex_file(monkeypatch)

            # Simulate the $EDITOR run: the edit adds a dangling link.
            alpha.write_text("Link to [[Beta]] and [[Nowhere]].\n", encoding="utf-8")
            await app._after_editor(alpha)
            await pilot.pause()

            assert reindexed == [alpha]
            assert app._index is not None
            assert app._index.backlinks[vault / "Beta.md"] == {alpha}
            # The incremental per-note audit caught the new problem.
            assert app._problem_count == 1
            assert app.preview.note_path == alpha

    asyncio.run(run())


def test_watcher_event_during_backlink_selection_is_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A watcher event landing while a backlink selection is mid-dispatch
    neither clears the OptionList under the in-flight selection nor
    triggers a whole-vault rebuild (HOPPUS-86); the deferred refresh
    lands afterwards with the up-to-date backlinks.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    alpha = vault / "Alpha.md"
    beta = vault / "Beta.md"
    gamma = vault / "Gamma.md"
    alpha.write_text("Link to [[Beta]].\n", encoding="utf-8")
    beta.write_text("Link to [[Alpha]].\n", encoding="utf-8")
    monkeypatch.setattr(watcher_module, "Observer", FakeObserver)

    async def run() -> None:
        app = HoppusApp(config=default_config(), vaults_root=vault)
        async with app.run_test() as pilot:
            assert app._watcher is not None
            await app.open_note(beta, vault_root=vault)
            await pilot.pause()
            pane = app.query_one("#backlinks-pane", BacklinksPane)
            assert pane.option_count == 1  # Alpha links to Beta

            forbid_full_build(monkeypatch)
            reindexed = spy_reindex_file(monkeypatch)
            mid_dispatch_counts: list[int] = []
            real_open_note = app.open_note

            async def open_note_with_racing_watcher_event(
                path: Path, vault_root: Path | None = None
            ) -> None:
                # A new note appears on disk exactly while the backlink
                # selection is being dispatched.
                if not gamma.exists():
                    gamma.write_text("See [[Alpha]].\n", encoding="utf-8")
                    assert app._watcher is not None
                    app._watcher._handle(gamma, "created")
                    # The pane must NOT have been cleared mid-dispatch.
                    mid_dispatch_counts.append(pane.option_count)
                await real_open_note(path, vault_root=vault_root)

            app.open_note = open_note_with_racing_watcher_event  # type: ignore[method-assign]

            # Select the "Alpha" backlink through the real message flow.
            pane.focus()
            await pilot.pause()
            pane.highlighted = 0
            pane.action_select()
            await pilot.pause()

            assert mid_dispatch_counts == [1]
            assert reindexed == [gamma]
            assert app._index is not None
            assert gamma in app._index.notes_by_path
            assert app.preview.note_path == alpha
            # The deferred refresh applied after the dispatch: Alpha's
            # backlinks now include the racing Gamma note.
            prompts = {
                str(pane.get_option_at_index(index).prompt)
                for index in range(pane.option_count)
            }
            assert prompts == {"Beta", "Gamma"}

    asyncio.run(run())


def test_programmatic_create_surfaces_in_explorer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A note created through the shared refresh path appears in the File
    Explorer tree without a restart (HOPPUS-90) — and without a full
    rebuild.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Alpha.md").write_text("Alpha body.\n", encoding="utf-8")

    async def run() -> None:
        app = HoppusApp(config=watcherless_config(), vaults_root=vault)
        async with app.run_test() as pilot:
            explorer = app.query_one("#explorer-pane", ExplorerPane)
            names = {child.data.path.name for child in explorer.root.children}
            assert names == {"Alpha.md"}

            forbid_full_build(monkeypatch)
            zeta = vault / "Zeta.md"
            zeta.write_text("Zeta body.\n", encoding="utf-8")
            assert app._apply_note_change(vault, zeta) is not None
            for _ in range(5):
                await pilot.pause()
                names = {child.data.path.name for child in explorer.root.children}
                if "Zeta.md" in names:
                    break
            assert names == {"Alpha.md", "Zeta.md"}
            assert app._index is not None
            assert zeta in app._index.notes_by_path

    asyncio.run(run())
