"""
Auto-started vault watcher tests (HOPPUS-77, spec §6.2 / §8 / D5).

Headless Textual Pilot tests (``App.run_test`` wrapped in ``asyncio.run``,
matching the rest of the suite). No real observer thread ever runs: the
watchdog ``Observer`` is monkeypatched with fakes (as in
``test_watcher.py``) and events are driven directly through the
watcher's ``_handle``, with a manually-advanced clock poking the
debounce (as in ``test_watcher_suppression.py``). The app must start a
watcher on mount, fold external changes into the cached index
incrementally on the app thread (HOPPUS-86, never a full rebuild),
keep D5 self-write suppression intact, restart the watcher on vault
switch, honor the ``watcher.enabled`` opt-out, and degrade without
raising when the watcher cannot start.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

import hoppus.index.watcher as watcher_module
from hoppus.config import default_config
from hoppus.index.indexer import Index
from hoppus.tui.app import HoppusApp


class FakeClock:
    """
    A manually-advanced monotonic clock for deterministic debounce.
    """

    def __init__(self) -> None:
        """Start at time zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """:returns: The current fake time."""
        return self.now

    def advance(self, seconds: float) -> None:
        """
        Move the clock forward.

        :param seconds: Amount of fake time to add.
        """
        self.now += seconds


class FakeObserver:
    """
    Observer stand-in recording lifecycle calls (no threads).
    """

    def __init__(self) -> None:
        """Start unscheduled and stopped."""
        self.started = False
        self.stopped = False
        self.scheduled: list[tuple[object, str, bool]] = []

    def schedule(self, handler: object, path: str, recursive: bool = False) -> None:
        """Record the (handler, path, recursive) registration."""
        self.scheduled.append((handler, path, recursive))

    def start(self) -> None:
        """Record the start."""
        self.started = True

    def stop(self) -> None:
        """Record the stop."""
        self.stopped = True

    def join(self) -> None:
        """No thread to join."""


def make_vault(root: Path, name: str = "Personal") -> Path:
    """
    Build a tiny vault under a vaults root.

    :param root: The vaults-root directory (created if needed).
    :param name: The vault directory name.
    :returns: The vault root.
    """
    vault = root / name
    vault.mkdir(parents=True)
    (vault / "Alpha.md").write_text("Link to [[Beta]].\n", encoding="utf-8")
    (vault / "Beta.md").write_text("Beta body.\n", encoding="utf-8")
    return vault


def make_app(vaults_root: Path, config: dict[str, Any] | None = None) -> HoppusApp:
    """
    Build a HoppusApp rooted at the given vaults root, recording notifies.

    :param vaults_root: Directory treated as the vaults root.
    :param config: Optional config dict; defaults to ``default_config()``.
    :returns: An unmounted HoppusApp with a ``recorded_notifications``
        list of message strings.
    """
    app = HoppusApp(config=config or default_config(), vaults_root=vaults_root)
    app.recorded_notifications = []  # type: ignore[attr-defined]
    original_notify = app.notify

    def recording_notify(message: str, **kwargs: object) -> None:
        app.recorded_notifications.append(message)  # type: ignore[attr-defined]
        original_notify(message, **kwargs)  # type: ignore[arg-type]

    app.notify = recording_notify  # type: ignore[method-assign]
    return app


def patch_fake_observers(monkeypatch: pytest.MonkeyPatch) -> list[FakeObserver]:
    """
    Replace the watchdog ``Observer`` with recording fakes.

    :param monkeypatch: pytest's monkeypatch fixture.
    :returns: A list collecting every constructed fake.
    """
    fakes: list[FakeObserver] = []

    def make_fake() -> FakeObserver:
        fake = FakeObserver()
        fakes.append(fake)
        return fake

    monkeypatch.setattr(watcher_module, "Observer", make_fake)
    return fakes


def test_watcher_starts_on_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Mounting with an existing vault starts a watcher on its root.
    """
    vault = make_vault(tmp_path)
    fakes = patch_fake_observers(monkeypatch)

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._watcher is not None
            assert app._watcher.available is True
            assert app._watcher.index.vault_root == vault
            assert len(fakes) == 1
            assert fakes[0].scheduled == [(app._watcher._handler, str(vault), True)]
        assert fakes[0].stopped
        assert app._watcher is None

    asyncio.run(run())


def test_external_change_triggers_incremental_reindex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A note changed on disk folds into the cached index incrementally
    (HOPPUS-86): same Index object, no whole-vault rebuild.
    """
    vault = make_vault(tmp_path)
    patch_fake_observers(monkeypatch)

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._watcher is not None
            # The launch-time worker (HOPPUS-78) pre-populates the cache.
            initial_index = app._index
            assert initial_index is not None
            assert vault / "Gamma.md" not in initial_index.notes_by_path

            builds: list[Path] = []
            real_build = Index.build
            monkeypatch.setattr(
                Index,
                "build",
                classmethod(lambda cls, root: builds.append(root) or real_build(root)),
            )

            gamma = vault / "Gamma.md"
            gamma.write_text("Gamma links to [[Alpha]].\n", encoding="utf-8")
            app._watcher._handle(gamma, "created")
            await pilot.pause()

            assert app._index is initial_index
            assert gamma in app._index.notes_by_path
            assert app._index.backlinks[vault / "Alpha.md"] == {gamma}
            assert builds == []
            assert "Reindexed" not in app.recorded_notifications

    asyncio.run(run())


def test_burst_events_reindex_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Rapid repeated events for one path coalesce (leading-edge debounce)
    into a single pass through the incremental reindex path.
    """
    vault = make_vault(tmp_path)
    patch_fake_observers(monkeypatch)

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            watcher = app._watcher
            assert watcher is not None
            clock = FakeClock()
            watcher.clock = clock

            reindexed: list[Path] = []
            real_reindex = Index.reindex_file

            def spy_reindex(self: Index, path: Path) -> set[Path]:
                reindexed.append(path)
                return real_reindex(self, path)

            monkeypatch.setattr(Index, "reindex_file", spy_reindex)

            alpha = vault / "Alpha.md"
            watcher._handle(alpha, "modified")
            clock.advance(0.05)
            watcher._handle(alpha, "modified")
            await pilot.pause()
            assert reindexed == [alpha]

            clock.advance(1.0)
            watcher._handle(alpha, "modified")
            await pilot.pause()
            assert reindexed == [alpha, alpha]

    asyncio.run(run())


def test_self_write_does_not_trigger_reindex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A hoppus self-write (D5 suppression) never re-fires the reindex.
    """
    vault = make_vault(tmp_path)
    patch_fake_observers(monkeypatch)

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._watcher is not None
            initial_index = app._index

            alpha = vault / "Alpha.md"
            app._suppress_self_write(alpha)
            alpha.write_text("Rewritten by hoppus.\n", encoding="utf-8")
            app._watcher._handle(alpha, "modified")
            await pilot.pause()

            # No reindex fired: the launch-time cache is untouched.
            assert app._index is initial_index
            assert "Reindexed" not in app.recorded_notifications

    asyncio.run(run())


def test_watcher_failure_degrades_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    An observer that cannot start leaves the app running, watcher-less,
    with a quiet notification instead of a traceback.
    """
    make_vault(tmp_path)

    class ExplodingObserver(FakeObserver):
        def start(self) -> None:
            raise OSError("inotify limit reached")

    monkeypatch.setattr(watcher_module, "Observer", ExplodingObserver)

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._watcher is None
            assert any(
                "Live vault watching unavailable" in message
                for message in app.recorded_notifications
            )

    asyncio.run(run())


def test_missing_watchdog_degrades_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    With watchdog unavailable (``Observer is None``), mount still works.
    """
    make_vault(tmp_path)
    monkeypatch.setattr(watcher_module, "Observer", None)

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._watcher is None
            assert any(
                "Live vault watching unavailable" in message
                for message in app.recorded_notifications
            )

    asyncio.run(run())


def test_watcher_enabled_false_skips_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The ``watcher.enabled`` opt-out skips the watcher entirely.
    """
    make_vault(tmp_path)
    fakes = patch_fake_observers(monkeypatch)
    config = default_config()
    config["watcher"]["enabled"] = False

    async def run() -> None:
        app = make_app(tmp_path, config=config)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._watcher is None
            assert fakes == []
            assert not any(
                "watching unavailable" in message
                for message in app.recorded_notifications
            )

    asyncio.run(run())


def test_no_vault_directory_skips_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Without a vault directory there is nothing to watch — no watcher,
    no crash.
    """
    fakes = patch_fake_observers(monkeypatch)

    async def run() -> None:
        app = make_app(tmp_path / "missing")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._watcher is None
            assert fakes == []

    asyncio.run(run())


def test_switch_vault_restarts_watcher_on_new_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Switching vaults stops the old watcher and watches the new root.
    """
    make_vault(tmp_path, "Personal")
    work = make_vault(tmp_path, "Work")
    fakes = patch_fake_observers(monkeypatch)

    async def run() -> None:
        app = make_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            first = app._watcher
            assert first is not None

            await app.switch_vault("Work")
            await pilot.pause()

            assert fakes[0].stopped
            assert first.available is False
            assert app._watcher is not None
            assert app._watcher is not first
            assert app._watcher.index.vault_root == work
            assert len(fakes) == 2
            assert fakes[1].scheduled[0][1] == str(work)

    asyncio.run(run())
