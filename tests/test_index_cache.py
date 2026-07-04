"""
Persistent index cache tests (HOPPUS-79, F19).

Covers the ``hoppus.index.cache`` module (round-trip fidelity, vault
fingerprinting, atomic writes, and graceful degradation over missing/
corrupt/old-schema caches) and the TUI launch flow: rendering instantly
from a cached index while the background build reconciles, the
``index.cache: false`` opt-out, and persisting incremental changes on
shutdown.
"""

import asyncio
import json
import os
import threading
import time
from pathlib import Path

import pytest

from hoppus.config import default_config
from hoppus.index import cache as index_cache
from hoppus.index.indexer import Index
from hoppus.tui.app import HoppusApp, StatusLine


def make_vault(tmp_path: Path) -> Path:
    """
    Build a small vault exercising links, tags, aliases, duplicate
    titles, an attachment embed, and one unresolved wikilink.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Alpha.md").write_text(
        "---\naliases:\n  - The Alpha\ntags:\n  - project\n---\n"
        "# Alpha\n\n"
        "See [[Beta]] and [[Nowhere]] and ![[image.png]].\n\n"
        "## Section\n\nA block. ^block-1\n",
        encoding="utf-8",
    )
    (vault / "Beta.md").write_text(
        "# Beta\n\nBack to [[Alpha#Section]] and #inline/tag here.\n",
        encoding="utf-8",
    )
    projects = vault / "Projects"
    projects.mkdir()
    (projects / "Alpha.md").write_text("Duplicate title.\n", encoding="utf-8")
    attachments = vault / "attachments"
    attachments.mkdir()
    (attachments / "image.png").write_bytes(b"\x00")
    return vault


def assert_indexes_equal(loaded: Index, fresh: Index) -> None:
    """
    Assert two indexes are structurally equal (never object-identical).
    """
    assert loaded is not fresh
    assert loaded.vault_root == fresh.vault_root
    assert loaded.notes_by_path == fresh.notes_by_path
    assert loaded.notes == fresh.notes
    assert loaded.links == fresh.links
    assert loaded.backlinks == fresh.backlinks
    assert loaded.tags == fresh.tags
    assert loaded.note_tags == fresh.note_tags
    assert loaded.title_index == fresh.title_index
    assert loaded.alias_index == fresh.alias_index
    assert loaded._attachments == fresh._attachments


# -- cache.py: round-trip, fingerprint, robustness ---------------------------


def test_save_then_load_round_trips_a_built_index(tmp_path: Path) -> None:
    """
    ``save_index`` then ``load_index`` equals a fresh build of the vault.
    """
    vault = make_vault(tmp_path)
    built = Index.build(vault)
    index_cache.save_index(built, vault)
    loaded = index_cache.load_index(vault)
    assert loaded is not None
    assert_indexes_equal(loaded, Index.build(vault))


def test_loaded_links_keep_resolution_state(tmp_path: Path) -> None:
    """
    Resolved and unresolved links survive the round trip as-is.
    """
    vault = make_vault(tmp_path)
    index_cache.save_index(Index.build(vault), vault)
    loaded = index_cache.load_index(vault)
    assert loaded is not None
    links = {link.target: link for link in loaded.links[vault / "Alpha.md"]}
    assert links["Beta"].resolved == vault / "Beta.md"
    assert links["Nowhere"].resolved is None
    assert links["image.png"].resolved == vault / "attachments" / "image.png"
    assert links["image.png"].is_embed is True


def test_fingerprint_changes_on_add_remove_modify(tmp_path: Path) -> None:
    """
    The fingerprint reacts to added, removed, and modified notes.
    """
    vault = make_vault(tmp_path)
    original = index_cache.compute_fingerprint(vault)
    assert index_cache.compute_fingerprint(vault) == original

    new_note = vault / "Gamma.md"
    new_note.write_text("New.\n", encoding="utf-8")
    added = index_cache.compute_fingerprint(vault)
    assert added != original

    beta = vault / "Beta.md"
    beta.write_text("# Beta\n\nModified body, different size.\n", encoding="utf-8")
    modified = index_cache.compute_fingerprint(vault)
    assert modified != added

    # A pure mtime bump (same size) also changes the fingerprint.
    stat = beta.stat()
    os.utime(beta, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert index_cache.compute_fingerprint(vault) != modified

    new_note.unlink()
    assert index_cache.compute_fingerprint(vault) not in {original, added, modified}


def test_stale_cache_is_detected_via_fingerprint(tmp_path: Path) -> None:
    """
    Changing the vault after a save makes the stored fingerprint stale.
    """
    vault = make_vault(tmp_path)
    index_cache.save_index(Index.build(vault), vault)
    stored = index_cache.cached_fingerprint(vault)
    assert stored == index_cache.compute_fingerprint(vault)

    (vault / "Gamma.md").write_text("New note.\n", encoding="utf-8")
    assert index_cache.compute_fingerprint(vault) != stored
    # The stale cache still loads (it is provisional by contract) but
    # lacks the new note; a rebuild reconciles.
    loaded = index_cache.load_index(vault)
    assert loaded is not None
    assert vault / "Gamma.md" not in loaded.notes_by_path
    assert vault / "Gamma.md" in Index.build(vault).notes_by_path


def test_missing_corrupt_and_old_schema_caches_load_as_none(tmp_path: Path) -> None:
    """
    Missing, corrupt, truncated, and wrong-schema caches never raise.
    """
    vault = make_vault(tmp_path)
    assert index_cache.load_index(vault) is None
    assert index_cache.cached_fingerprint(vault) is None

    file = index_cache.cache_file(vault)
    file.parent.mkdir(parents=True, exist_ok=True)

    file.write_text("{not json", encoding="utf-8")
    assert index_cache.load_index(vault) is None

    index_cache.save_index(Index.build(vault), vault)
    full = file.read_text(encoding="utf-8")
    file.write_text(full[: len(full) // 2], encoding="utf-8")
    assert index_cache.load_index(vault) is None

    data = json.loads(full)
    data["schema"] = index_cache.SCHEMA_VERSION + 1
    file.write_text(json.dumps(data), encoding="utf-8")
    assert index_cache.load_index(vault) is None

    # Structurally broken index data is also "no cache".
    data["schema"] = index_cache.SCHEMA_VERSION
    data["index"] = {"notes": [{"bogus": True}], "attachments": []}
    file.write_text(json.dumps(data), encoding="utf-8")
    assert index_cache.load_index(vault) is None


def test_save_is_atomic_and_failure_leaves_old_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Writes go through temp + ``os.replace``; a failed replace leaves the
    previous cache intact and no temp file behind.
    """
    vault = make_vault(tmp_path)
    index_cache.save_index(Index.build(vault), vault)
    file = index_cache.cache_file(vault)
    before = file.read_text(encoding="utf-8")
    assert not list(file.parent.glob("*.tmp"))

    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def failing_replace(src: str, dst: str) -> None:
        replaced.append((str(src), str(dst)))
        raise OSError("disk full")

    monkeypatch.setattr(index_cache.os, "replace", failing_replace)
    (vault / "Gamma.md").write_text("New.\n", encoding="utf-8")
    index_cache.save_index(Index.build(vault), vault)  # must not raise
    monkeypatch.setattr(index_cache.os, "replace", real_replace)

    assert replaced, "save_index did not go through os.replace"
    assert file.read_text(encoding="utf-8") == before
    assert not list(file.parent.glob("*.tmp"))


# -- TUI launch flow ---------------------------------------------------------


def test_launch_worker_writes_the_cache(tmp_path: Path) -> None:
    """
    A normal launch persists the built index to ``.hoppus``.
    """
    vault = make_vault(tmp_path)

    async def run() -> None:
        app = HoppusApp(config=default_config(), vaults_root=vault)
        async with app.run_test():
            assert index_cache.cache_file(vault).is_file()
            assert index_cache.cached_fingerprint(
                vault
            ) == index_cache.compute_fingerprint(vault)

    asyncio.run(run())


@pytest.mark.no_index_settle
def test_cached_launch_is_interactive_before_the_build_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    With a valid cache the app renders and serves the index immediately,
    then reconciles (atomic swap) when the background build lands.
    """
    vault = make_vault(tmp_path)
    index_cache.save_index(Index.build(vault), vault)

    real_build = Index.build
    gate = threading.Event()

    def gated_build(cls: type[Index], vault_root: Path) -> Index:
        assert gate.wait(timeout=10), "index-build gate was never released"
        return real_build(vault_root)

    monkeypatch.setattr(Index, "build", classmethod(gated_build))

    async def run() -> None:
        app = HoppusApp(config=default_config(), vaults_root=vault)
        async with app.run_test() as pilot:
            await pilot.pause()
            # The build is still gated, yet the cached index already
            # serves the UI: interactive at once, no waiting.
            assert app._indexing is True
            assert app._cache_provisional is True
            cached = app._index
            assert cached is not None
            assert app._index_root == vault
            assert app._active_index(vault) is cached
            assert vault / "Alpha.md" in cached.notes_by_path
            status = str(app.query_one(StatusLine).render())
            assert "⟳ indexing…" in status

            gate.set()
            assert app._launch_worker is not None
            await app._launch_worker.wait()
            await pilot.pause()
            # Reconciled: the fresh build atomically replaced the
            # provisional cache and the indicator cleared.
            assert app._indexing is False
            assert app._cache_provisional is False
            assert app._index is not None
            assert app._index is not cached
            assert "indexing" not in str(app.query_one(StatusLine).render())

    asyncio.run(run())


def test_stale_cache_reconciles_after_background_build(tmp_path: Path) -> None:
    """
    A fingerprint-mismatched cache is provisional only: the launch
    build folds in vault changes made since the cache was written.
    """
    vault = make_vault(tmp_path)
    index_cache.save_index(Index.build(vault), vault)
    (vault / "Gamma.md").write_text("Added after the cache.\n", encoding="utf-8")

    async def run() -> None:
        app = HoppusApp(config=default_config(), vaults_root=vault)
        async with app.run_test():
            assert app._index is not None
            assert vault / "Gamma.md" in app._index.notes_by_path

    asyncio.run(run())


def test_cache_opt_out_disables_reads_and_writes(tmp_path: Path) -> None:
    """
    With ``index.cache: false`` no cache is read or written.
    """
    vault = make_vault(tmp_path)
    index_cache.save_index(Index.build(vault), vault)
    # Plant a poisoned cache: if the app read it, the marker note would
    # appear in the provisional index.
    file = index_cache.cache_file(vault)
    data = json.loads(file.read_text(encoding="utf-8"))
    mtime_before = file.stat().st_mtime_ns
    config = default_config()
    config["index"]["cache"] = False

    async def run() -> None:
        app = HoppusApp(config=config, vaults_root=vault)
        async with app.run_test():
            assert app._cache_provisional is False
            assert app._index is not None
        # No rewrite on the launch worker or shutdown.
        assert file.stat().st_mtime_ns == mtime_before
        assert json.loads(file.read_text(encoding="utf-8")) == data

    asyncio.run(run())


def test_incremental_changes_persist_to_the_cache_on_shutdown(
    tmp_path: Path,
) -> None:
    """
    Hot-path index changes mark the cache dirty and are flushed on a
    clean shutdown, so the next launch sees them.
    """
    vault = make_vault(tmp_path)
    new_note = vault / "Gamma.md"

    async def run() -> None:
        app = HoppusApp(config=default_config(), vaults_root=vault)
        async with app.run_test() as pilot:
            worker_write = index_cache.cache_file(vault).stat().st_mtime_ns
            new_note.write_text("Captured mid-session.\n", encoding="utf-8")
            assert app._apply_note_change(vault, new_note) is not None
            assert app._cache_dirty is True
            await pilot.pause()
            # Not rewritten per change — only at the shutdown save point.
            assert index_cache.cache_file(vault).stat().st_mtime_ns == worker_write

    asyncio.run(run())
    loaded = index_cache.load_index(vault)
    assert loaded is not None
    assert new_note in loaded.notes_by_path


def test_provisional_cache_is_never_rewritten_from_itself(tmp_path: Path) -> None:
    """
    A provisional (unreconciled) cached index is never re-persisted.
    """
    vault = make_vault(tmp_path)
    index_cache.save_index(Index.build(vault), vault)

    app = HoppusApp(config=default_config(), vaults_root=vault)
    app._index = index_cache.load_index(vault)
    app._index_root = vault
    app._cache_provisional = True
    app._cache_dirty = True
    mtime_before = index_cache.cache_file(vault).stat().st_mtime_ns
    time.sleep(0.01)
    app._flush_index_cache()
    assert index_cache.cache_file(vault).stat().st_mtime_ns == mtime_before
