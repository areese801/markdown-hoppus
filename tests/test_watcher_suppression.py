"""
VaultWatcher self-write suppression and debounce tests (HOPPUS-38,
decision D5).

``_handle`` / ``should_process`` are driven directly with a fake
injected clock — no real filesystem watching, threads, or wall-clock
timing. The no-feedback-loop guarantee: hoppus-originated writes
(``suppress`` / ``suppress_many`` / ``self_write``) must NOT re-trigger
the index pass, while external edits still do.
"""

from pathlib import Path

from hoppus.index.indexer import Index
from hoppus.index.watcher import VaultWatcher


class FakeClock:
    """
    A manually-advanced monotonic clock for deterministic tests.
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


class RecordingIndex(Index):
    """
    An ``Index`` that records every ``reindex_file`` call.
    """

    def __init__(self, vault_root: Path) -> None:
        """
        :param vault_root: The vault root directory.
        """
        super().__init__(vault_root)
        self.reindexed: list[Path] = []

    def reindex_file(self, path: Path) -> None:
        """
        Record the call, then delegate.

        :param path: The file being re-indexed.
        """
        self.reindexed.append(path)
        super().reindex_file(path)


def make_vault(tmp_path: Path) -> Path:
    """
    Build a tiny writable vault with a few notes and skip dirs.

    :param tmp_path: pytest's per-test temp dir.
    :returns: The vault root.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Alpha.md").write_text("Link to [[Beta]].\n", encoding="utf-8")
    (vault / "Beta.md").write_text("Beta body.\n", encoding="utf-8")
    (vault / "Gamma.md").write_text("Gamma body.\n", encoding="utf-8")
    (vault / ".hoppus").mkdir()
    (vault / ".obsidian").mkdir()
    return vault


def make_watcher(
    tmp_path: Path, **kwargs: float
) -> tuple[VaultWatcher, RecordingIndex, FakeClock, list[Path]]:
    """
    Build a watcher over a recording index with a fake clock.

    :param tmp_path: pytest's per-test temp dir.
    :param kwargs: Extra ``VaultWatcher`` keyword arguments.
    :returns: ``(watcher, index, clock, on_change_calls)``.
    """
    vault = make_vault(tmp_path)
    index = RecordingIndex.build(vault)
    clock = FakeClock()
    seen: list[Path] = []
    watcher = VaultWatcher(index, on_change=seen.append, clock=clock, **kwargs)
    return watcher, index, clock, seen


# -- Self-write suppression ----------------------------------------------------


def test_suppressed_path_is_not_processed_within_ttl(tmp_path: Path) -> None:
    """
    A hoppus self-write does not re-fire the index pass within the TTL.
    """
    watcher, index, clock, seen = make_watcher(tmp_path)
    alpha = watcher.index.vault_root / "Alpha.md"

    watcher.suppress(alpha, ttl=2.0)
    clock.advance(0.5)
    watcher._handle(alpha, "modified")

    assert index.reindexed == []
    assert seen == []


def test_suppression_covers_multiple_events_within_window(tmp_path: Path) -> None:
    """
    One write emitting create + modify is fully covered (time-window
    semantics, not consumed on first event).
    """
    watcher, index, clock, seen = make_watcher(tmp_path)
    alpha = watcher.index.vault_root / "Alpha.md"

    watcher.suppress(alpha, ttl=2.0)
    watcher._handle(alpha, "created")
    clock.advance(0.05)
    watcher._handle(alpha, "modified")

    assert index.reindexed == []
    assert seen == []


def test_suppressed_path_processes_again_after_ttl(tmp_path: Path) -> None:
    """
    After the TTL elapses, the same path is processed normally again.
    """
    watcher, index, clock, seen = make_watcher(tmp_path)
    alpha = watcher.index.vault_root / "Alpha.md"

    watcher.suppress(alpha, ttl=2.0)
    clock.advance(2.5)
    watcher._handle(alpha, "modified")

    assert index.reindexed == [alpha]
    assert seen == [alpha]


def test_external_path_is_processed_while_another_is_suppressed(
    tmp_path: Path,
) -> None:
    """
    An external (never-suppressed) edit still processes normally.
    """
    watcher, index, clock, seen = make_watcher(tmp_path)
    alpha = watcher.index.vault_root / "Alpha.md"
    beta = watcher.index.vault_root / "Beta.md"

    watcher.suppress(alpha, ttl=2.0)
    watcher._handle(beta, "modified")

    assert index.reindexed == [beta]
    assert seen == [beta]


def test_suppress_many_covers_apply_plan_style_batch(tmp_path: Path) -> None:
    """
    A multi-file batch write (rename propagation via ``apply_plan``)
    suppresses every touched path.
    """
    watcher, index, clock, seen = make_watcher(tmp_path)
    root = watcher.index.vault_root
    batch = [root / "Alpha.md", root / "Beta.md", root / "Gamma.md"]

    watcher.suppress_many(batch, ttl=2.0)
    for path in batch:
        watcher._handle(path, "modified")

    assert index.reindexed == []
    assert seen == []

    clock.advance(2.5)
    watcher._handle(batch[0], "modified")
    assert index.reindexed == [batch[0]]


def test_self_write_context_manager_refreshes_window(tmp_path: Path) -> None:
    """
    ``self_write`` suppresses on entry and refreshes on exit, so the
    TTL is measured from write completion.
    """
    watcher, index, clock, seen = make_watcher(tmp_path)
    alpha = watcher.index.vault_root / "Alpha.md"

    with watcher.self_write(alpha, ttl=2.0):
        watcher._handle(alpha, "created")
        clock.advance(1.5)  # slow write; entry window alone would lapse at 2.0

    clock.advance(1.0)  # 2.5 after entry, 1.0 after exit refresh
    watcher._handle(alpha, "modified")
    assert index.reindexed == []

    clock.advance(1.5)  # past the exit-refreshed TTL
    watcher._handle(alpha, "modified")
    assert index.reindexed == [alpha]


# -- Debounce ------------------------------------------------------------------


def test_burst_events_for_same_path_process_once(tmp_path: Path) -> None:
    """
    Two events for the same path within ``debounce_interval`` coalesce
    to a single (leading-edge) reindex.
    """
    watcher, index, clock, seen = make_watcher(tmp_path, debounce_interval=0.1)
    alpha = watcher.index.vault_root / "Alpha.md"

    watcher._handle(alpha, "modified")
    clock.advance(0.05)
    watcher._handle(alpha, "modified")

    assert index.reindexed == [alpha]
    assert seen == [alpha]


def test_event_after_debounce_interval_processes_again(tmp_path: Path) -> None:
    """
    A genuinely new edit after the window is processed again.
    """
    watcher, index, clock, seen = make_watcher(tmp_path, debounce_interval=0.1)
    alpha = watcher.index.vault_root / "Alpha.md"

    watcher._handle(alpha, "modified")
    clock.advance(0.05)
    watcher._handle(alpha, "modified")
    clock.advance(0.2)
    watcher._handle(alpha, "modified")

    assert index.reindexed == [alpha, alpha]
    assert seen == [alpha, alpha]


def test_debounce_is_per_path(tmp_path: Path) -> None:
    """
    Debounce keys on the path: distinct files in one burst all process.
    """
    watcher, index, clock, seen = make_watcher(tmp_path, debounce_interval=0.1)
    alpha = watcher.index.vault_root / "Alpha.md"
    beta = watcher.index.vault_root / "Beta.md"

    watcher._handle(alpha, "modified")
    watcher._handle(beta, "modified")

    assert index.reindexed == [alpha, beta]
    assert seen == [alpha, beta]


# -- Existing behavior still holds ---------------------------------------------


def test_filtering_still_rejects_non_md_and_skip_dirs(tmp_path: Path) -> None:
    """
    The HOPPUS-37 filter seam is intact: non-``.md`` files, skip dirs,
    and out-of-vault paths are rejected.
    """
    watcher, _, _, _ = make_watcher(tmp_path)
    root = watcher.index.vault_root

    assert watcher.should_process(root / "Alpha.md")
    assert not watcher.should_process(root / "notes.txt")
    assert not watcher.should_process(root / ".hoppus" / "cache.md")
    assert not watcher.should_process(root / ".obsidian" / "workspace.md")
    assert not watcher.should_process(tmp_path / "elsewhere.md")


def test_should_process_false_while_suppressed_then_true(tmp_path: Path) -> None:
    """
    ``should_process`` itself reflects the suppression window and prunes
    expired entries.
    """
    watcher, _, clock, _ = make_watcher(tmp_path)
    alpha = watcher.index.vault_root / "Alpha.md"

    watcher.suppress(alpha, ttl=1.0)
    assert not watcher.should_process(alpha)
    clock.advance(1.5)
    assert watcher.should_process(alpha)
    assert watcher._suppressed_until == {}


def test_graceful_degradation_unaffected(monkeypatch, tmp_path: Path) -> None:
    """
    With watchdog unavailable, ``start`` still degrades gracefully and
    suppression APIs keep working.
    """
    import hoppus.index.watcher as watcher_module

    watcher, index, clock, _ = make_watcher(tmp_path)
    monkeypatch.setattr(watcher_module, "Observer", None)

    watcher.start()
    assert watcher.available is False

    alpha = watcher.index.vault_root / "Alpha.md"
    watcher.suppress(alpha)
    watcher._handle(alpha, "modified")
    assert index.reindexed == []

    watcher.stop()
    assert watcher.available is False
