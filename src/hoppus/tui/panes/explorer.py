"""
File Explorer pane: the vault tree with CRUD (spec §9.2, HOPPUS-26/34).

Subclasses Textual's ``DirectoryTree`` rooted at the active vault path.
Dot-directories (``.hoppus/``, ``.obsidian/``, and any other foreign or
state directories) are hidden via ``filter_paths``; attachments
(non-``.md`` files) stay visible but are rendered dimmed so notes read
normally. Selection is surfaced through the standard
``DirectoryTree.FileSelected`` message, which the app handles to open
``.md`` notes in the preview.

CRUD (HOPPUS-34) is bound at the widget level so the keys only fire
while the explorer is focused: ``ctrl+n`` new note (always at the
vault root — GTD, spec §5.2), ``ctrl+shift+n`` new folder, ``f2``
rename, ``delete`` delete (with a ConfirmModal), ``ctrl+m`` move. The
filesystem work lives in :mod:`hoppus.fileops`; rename/move propagate
inbound links (spec §6.2), prompting first when
``files.prompt_before_link_update`` is set and links would change.
"""

from functools import partial
from pathlib import Path
from typing import Any, Iterable, cast

from rich.style import Style
from rich.text import Text
from textual.binding import Binding
from textual.widgets import DirectoryTree
from textual.widgets._tree import TreeNode
from textual.widgets.directory_tree import DirEntry

from hoppus import fileops
from hoppus.index.propagation import plan_rename
from hoppus.naming import validate_note_name
from hoppus.tui.modals.confirm import ConfirmModal
from hoppus.tui.modals.text_prompt import TextPromptModal

_MD_SUFFIX = ".md"


class ExplorerPane(DirectoryTree):
    """
    The File Explorer tree for a single vault, with CRUD (spec §9.2).
    """

    BINDINGS = [
        Binding("ctrl+n", "new_note", "New note"),
        Binding("ctrl+shift+n", "new_folder", "New folder"),
        Binding("f2", "rename_entry", "Rename"),
        Binding("delete", "delete_entry", "Delete"),
        Binding("ctrl+m", "move_entry", "Move"),
    ]

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        """
        Hide dot-entries so ``.hoppus/``/``.obsidian/`` never appear.

        Args:
            paths: Candidate directory entries from the loader.

        Returns:
            The entries whose names do not start with a dot.
        """
        return [path for path in paths if not path.name.startswith(".")]

    def render_label(
        self, node: TreeNode[DirEntry], base_style: Style, style: Style
    ) -> Text:
        """
        Render a node label, dimming attachments (non-``.md`` files).

        Args:
            node: The tree node being rendered.
            base_style: The base style of the widget.
            style: The style of the label.

        Returns:
            The styled label text.
        """
        label = super().render_label(node, base_style, style)
        entry = node.data
        if (
            entry is not None
            and not entry.path.is_dir()
            and entry.path.suffix.lower() != _MD_SUFFIX
        ):
            label.stylize("dim")
        return label

    # -- CRUD helpers ---------------------------------------------------------

    @property
    def _hoppus(self) -> Any:
        """
        The owning ``HoppusApp``, untyped to avoid a circular import.
        """
        return cast(Any, self.app)

    @property
    def vault_root(self) -> Path:
        """The vault directory this tree is rooted at."""
        return Path(self.path)

    def _selected_path(self) -> Path | None:
        """
        The path of the cursor node, or None when nothing is selected.
        """
        node = self.cursor_node
        if node is None or node.data is None:
            return None
        return Path(node.data.path)

    def _selected_note(self) -> Path | None:
        """
        The selected ``.md`` note path, notifying and returning None
        when the selection is missing, a folder, or an attachment.
        """
        path = self._selected_path()
        if path is None or path == self.vault_root:
            self.app.notify("No note selected", severity="information", timeout=3)
            return None
        if path.is_dir() or path.suffix.lower() != _MD_SUFFIX:
            self.app.notify(
                f"Not a note: {path.name}", severity="information", timeout=3
            )
            return None
        return path

    async def _refresh_after_change(self) -> None:
        """
        Rebuild the vault index after a CRUD op.

        The shared refresh path (``action_reindex``) also reloads this
        tree (HOPPUS-90), so no separate reload happens here.
        """
        self._hoppus.action_reindex()

    def _prompt_before_link_update(self) -> bool:
        """
        Whether config asks to confirm before rewriting inbound links.
        """
        files = self._hoppus.config.get("files", {})
        return bool(files.get("prompt_before_link_update", True))

    # -- Create ---------------------------------------------------------------

    def action_new_note(self) -> None:
        """
        Prompt for a new note name; the note lands at the vault root
        (GTD convention, spec §5.2).
        """
        self.app.push_screen(
            TextPromptModal(
                "New note (created at vault root)",
                placeholder="Note title",
                validate=validate_note_name,
            ),
            self._do_create_note,
        )

    async def _do_create_note(self, name: str | None) -> None:
        """
        Create the note at the vault root and open it in the preview.

        Args:
            name: The entered title, or None on cancel.
        """
        if name is None:
            return
        try:
            path = fileops.create_note(self.vault_root, name)
        except (FileExistsError, ValueError) as error:
            self.app.notify(str(error), severity="error", timeout=5)
            return
        await self._refresh_after_change()
        await self._hoppus.open_note(path, vault_root=self.vault_root)
        self.app.notify(f"Created {path.name}", severity="information", timeout=3)

    def action_new_folder(self) -> None:
        """
        Prompt for a folder name, created under the selected directory
        (or the selected file's directory, or the vault root).
        """
        selected = self._selected_path()
        if selected is None:
            parent = self.vault_root
        elif selected.is_dir():
            parent = selected
        else:
            parent = selected.parent
        self.app.push_screen(
            TextPromptModal(
                f"New folder in {parent.name or parent}",
                placeholder="Folder name",
                validate=validate_note_name,
            ),
            partial(self._do_create_folder, parent),
        )

    async def _do_create_folder(self, parent: Path, name: str | None) -> None:
        """
        Create the folder and refresh the tree.

        Args:
            parent: The directory to create the folder in.
            name: The entered name, or None on cancel.
        """
        if name is None:
            return
        try:
            path = fileops.create_folder(parent, name)
        except (FileExistsError, ValueError) as error:
            self.app.notify(str(error), severity="error", timeout=5)
            return
        await self._refresh_after_change()
        self.app.notify(f"Created {path.name}/", severity="information", timeout=3)

    # -- Rename ---------------------------------------------------------------

    def action_rename_entry(self) -> None:
        """
        Prompt to rename the selected note (link-propagating, §6.2).
        """
        path = self._selected_note()
        if path is None:
            return
        self.app.push_screen(
            TextPromptModal(
                f"Rename {path.name}",
                initial=path.stem,
                validate=validate_note_name,
            ),
            partial(self._do_rename, path),
        )

    async def _do_rename(self, old_path: Path, new_name: str | None) -> None:
        """
        Plan the rename and gate the link rewrites on config/confirm.

        Args:
            old_path: The note's current path.
            new_name: The entered new title, or None on cancel.
        """
        if new_name is None or new_name.strip() in ("", old_path.stem):
            return
        if validate_note_name(new_name.strip()):
            self.app.notify(f"Invalid name: {new_name}", severity="error", timeout=5)
            return
        new_path = old_path.with_name(f"{new_name.strip()}{_MD_SUFFIX}")
        if new_path.exists():
            self.app.notify(
                f"Note already exists: {new_path.name}", severity="error", timeout=5
            )
            return
        await self._gate_links_then(
            old_path, new_path, partial(self._finish_rename, old_path, new_name)
        )

    async def _finish_rename(
        self, old_path: Path, new_name: str, apply_links: bool | None
    ) -> None:
        """
        Perform the rename with the decided link policy.

        Args:
            old_path: The note's current path.
            new_name: The new title.
            apply_links: Whether to rewrite inbound links.
        """
        index = self._hoppus._active_index(self.vault_root)
        try:
            new_path, plan = fileops.rename_note(
                old_path,
                new_name,
                index,
                self.vault_root,
                apply_links=bool(apply_links),
            )
        except (FileExistsError, ValueError) as error:
            self.app.notify(str(error), severity="error", timeout=5)
            return
        await self._refresh_after_change()
        self.app.notify(
            f"Renamed to {new_path.name} ({plan.link_count} link(s))",
            severity="information",
            timeout=3,
        )

    # -- Move -----------------------------------------------------------------

    def action_move_entry(self) -> None:
        """
        Prompt for a vault-relative destination folder for the
        selected note (link-propagating, §6.2).
        """
        path = self._selected_note()
        if path is None:
            return
        self.app.push_screen(
            TextPromptModal(
                f"Move {path.name} to folder (vault-relative path)",
                placeholder="e.g. Projects or . for the vault root",
            ),
            partial(self._do_move, path),
        )

    async def _do_move(self, old_path: Path, relative: str | None) -> None:
        """
        Resolve the destination and gate the link rewrites.

        Args:
            old_path: The note's current path.
            relative: The entered vault-relative folder, or None.
        """
        if relative is None:
            return
        relative = relative.strip().strip("/")
        dest_dir = (
            self.vault_root if relative in ("", ".") else self.vault_root / relative
        )
        if not dest_dir.is_dir() or not dest_dir.resolve().is_relative_to(
            self.vault_root.resolve()
        ):
            self.app.notify(f"No such folder: {relative}", severity="error", timeout=5)
            return
        new_path = dest_dir / old_path.name
        if new_path == old_path:
            return
        if new_path.exists():
            self.app.notify(
                f"Note already exists: {new_path}", severity="error", timeout=5
            )
            return
        await self._gate_links_then(
            old_path, new_path, partial(self._finish_move, old_path, dest_dir)
        )

    async def _finish_move(
        self, old_path: Path, dest_dir: Path, apply_links: bool | None
    ) -> None:
        """
        Perform the move with the decided link policy.

        Args:
            old_path: The note's current path.
            dest_dir: The destination directory.
            apply_links: Whether to rewrite inbound links.
        """
        index = self._hoppus._active_index(self.vault_root)
        try:
            new_path, plan = fileops.move_note(
                old_path,
                dest_dir,
                index,
                self.vault_root,
                apply_links=bool(apply_links),
            )
        except (FileExistsError, ValueError) as error:
            self.app.notify(str(error), severity="error", timeout=5)
            return
        await self._refresh_after_change()
        self.app.notify(
            f"Moved to {new_path.relative_to(self.vault_root)}"
            f" ({plan.link_count} link(s))",
            severity="information",
            timeout=3,
        )

    async def _gate_links_then(
        self, old_path: Path, new_path: Path, finish: Any
    ) -> None:
        """
        Decide the ``apply_links`` flag, prompting when configured.

        Computes the rename plan for its ``link_count``; when links
        would change and ``files.prompt_before_link_update`` is set,
        pushes a ConfirmModal whose answer flows into ``finish``.
        Otherwise calls ``finish(True)`` directly.

        Args:
            old_path: The note's current path.
            new_path: The note's prospective path.
            finish: Async callable taking the ``apply_links`` bool.
        """
        index = self._hoppus._active_index(self.vault_root)
        plan = plan_rename(
            old_path,
            new_path,
            index.notes_by_path.values(),
            self.vault_root,
            attachments=index._attachments,
        )
        if plan.link_count > 0 and self._prompt_before_link_update():
            self.app.push_screen(
                ConfirmModal(f"Update {plan.link_count} inbound link(s)?"), finish
            )
        else:
            await finish(True)

    # -- Delete ---------------------------------------------------------------

    def action_delete_entry(self) -> None:
        """
        Confirm, then delete the selected note or folder (spec §9.2).
        """
        path = self._selected_path()
        if path is None or path == self.vault_root:
            self.app.notify("Nothing selected", severity="information", timeout=3)
            return
        kind = "folder" if path.is_dir() else "file"
        self.app.push_screen(
            ConfirmModal(f"Delete {kind} '{path.name}'?"),
            partial(self._do_delete, path),
        )

    async def _do_delete(self, path: Path, confirmed: bool | None) -> None:
        """
        Delete the path when confirmed, then refresh.

        Args:
            path: The file or folder to delete.
            confirmed: The ConfirmModal answer.
        """
        if not confirmed:
            return
        try:
            fileops.delete_path(path)
        except (ValueError, FileNotFoundError) as error:
            self.app.notify(str(error), severity="error", timeout=5)
            return
        await self._refresh_after_change()
        self.app.notify(f"Deleted {path.name}", severity="information", timeout=3)
