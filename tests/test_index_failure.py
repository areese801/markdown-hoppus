"""
Index-build failure surfacing tests (HOPPUS-69).

When ``Index.build`` raises, the TUI must not mount looking healthy
while functionally dead: the failure is surfaced via a notification
and a status-line ⚠ index error segment, and index-dependent actions
explain themselves instead of silently no-oping. All Pilot tests run
headless via ``App.run_test`` wrapped in ``asyncio.run``.
"""

import asyncio
from pathlib import Path

import pytest

from hoppus.config import default_config
from hoppus.index.indexer import Index
from hoppus.tui.app import HoppusApp, StatusLine


def make_vault(tmp_path: Path) -> Path:
    """
    Build a minimal vault directory with one note.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Note.md").write_text("Hello.\n", encoding="utf-8")
    return vault


def make_recording_app(vault: Path) -> HoppusApp:
    """
    Build a HoppusApp that records every notification message.

    :param vault: The vault root to mount the app on.
    :returns: An unmounted HoppusApp with a ``recorded_notifications``
        list attribute.
    """
    app = HoppusApp(config=default_config(), vaults_root=vault)
    app.recorded_notifications = []  # type: ignore[attr-defined]
    original_notify = app.notify

    def recording_notify(message: str, **kwargs: object) -> None:
        app.recorded_notifications.append(message)  # type: ignore[attr-defined]
        original_notify(message, **kwargs)  # type: ignore[arg-type]

    app.notify = recording_notify  # type: ignore[method-assign]
    return app


def break_index_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Monkeypatch ``Index.build`` to always raise.
    """

    def boom(cls: type[Index], vault_root: Path) -> Index:
        raise RuntimeError("boom: malformed vault")

    monkeypatch.setattr(Index, "build", classmethod(boom))


def index_failure_messages(app: HoppusApp) -> list[str]:
    """
    Return the recorded index-build failure notifications.
    """
    return [
        message
        for message in app.recorded_notifications  # type: ignore[attr-defined]
        if "Index build failed" in message
    ]


def test_index_build_failure_is_surfaced_on_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A failing index build notifies and flags the status line on mount.
    """
    vault = make_vault(tmp_path)
    break_index_build(monkeypatch)

    async def run() -> None:
        app = make_recording_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            failures = index_failure_messages(app)
            assert failures, "expected an index-build failure notification"
            assert "boom: malformed vault" in failures[0]
            assert app._index_error == "boom: malformed vault"
            assert "⚠ index error" in str(app.query_one(StatusLine).render())

    asyncio.run(run())


def test_index_dependent_actions_explain_instead_of_no_oping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Switcher, search, stats, and reindex notify when the index is dead.
    """
    vault = make_vault(tmp_path)
    break_index_build(monkeypatch)

    async def run() -> None:
        app = make_recording_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            baseline = len(index_failure_messages(app))
            for action in (
                app.action_quick_switcher,
                app.action_search,
                app.action_vault_stats,
                app.action_reindex,
            ):
                action()
                await pilot.pause()
            # Each action reported the failure and pushed no modal.
            assert len(index_failure_messages(app)) == baseline + 4
            assert len(app.screen_stack) == 1

    asyncio.run(run())


def test_index_recovery_clears_the_error_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Once ``Index.build`` succeeds again, reindex clears the ⚠ segment.
    """
    vault = make_vault(tmp_path)
    real_build = Index.__dict__["build"]
    break_index_build(monkeypatch)

    async def run() -> None:
        app = make_recording_app(vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._index_error is not None

            monkeypatch.setattr(Index, "build", real_build)
            app.action_reindex()
            await pilot.pause()
            assert app._index_error is None
            assert "⚠ index error" not in str(app.query_one(StatusLine).render())
            assert "Reindexed" in app.recorded_notifications  # type: ignore[attr-defined]

    asyncio.run(run())
