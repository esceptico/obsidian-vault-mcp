import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from obsidian_mcp.core.config import EmbeddingSettings, VaultSettings
from obsidian_mcp.core.constants import (
    MAX_SEARCH_LIMIT,
    TRASH_TIMESTAMP_FORMAT,
    WATCHER_DEBOUNCE_SECONDS,
)
from obsidian_mcp.markdown.obsidian import wikilinks
from obsidian_mcp.core.logging import get_logger
from obsidian_mcp.core.types import DeleteStrategy, ListSortBy, SearchMode, SortOrder
from obsidian_mcp.index.search import IndexedNote, SearchIndex, SearchResult
from obsidian_mcp.vault.links import link_names_for, plan_wikilink_rewrites
from obsidian_mcp.vault.listing import entry_for, sort_entries
from obsidian_mcp.vault.notes import read_note, render_new_note, render_updated_note
from obsidian_mcp.vault.paths import (
    clean_relative_path,
    ensure_markdown_extension,
    temporary_write_path,
)
from obsidian_mcp.vault.policy import STORAGE_DIR, is_ignored_relative_path
from obsidian_mcp.vault.sync import sync_index
from obsidian_mcp.vault.watcher import VaultWatcher

log = get_logger("vault")


class Vault:
    def __init__(
        self, settings: VaultSettings, embeddings: EmbeddingSettings | None = None
    ):
        self.settings = settings
        self.root = settings.root.resolve()
        if not self.root.exists() or not self.root.is_dir():
            raise RuntimeError(
                f"Vault root does not exist or is not a directory: {self.root}"
            )
        self._index = SearchIndex(
            self.root / STORAGE_DIR / "index.sqlite", embeddings or EmbeddingSettings()
        )
        self._lock = threading.RLock()
        self._watcher: VaultWatcher | None = None
        # Reconcile the index against disk at startup so the first search
        # doesn't pay rebuild latency. Subsequent writes go through
        # upsert_note / delete_note. Out-of-band edits are picked up by
        # the file watcher (start_watching) or, as a fallback, by an
        # explicit vault_reindex call.
        self.sync_from_disk()

    # ----- file watcher lifecycle ------------------------------------------

    def start_watching(self) -> None:
        """Begin observing the vault for out-of-band edits and apply them
        through the regular upsert/delete index paths. Idempotent."""
        if self._watcher is not None:
            return
        self._watcher = VaultWatcher(
            root=self.root,
            on_upsert=self._apply_external_upsert,
            on_delete=self._apply_external_delete,
            is_ignored=self._is_ignored_path,
            debounce_seconds=WATCHER_DEBOUNCE_SECONDS,
        )
        self._watcher.start()

    def stop_watching(self) -> None:
        if self._watcher is None:
            return
        self._watcher.stop()
        self._watcher = None

    def _apply_external_upsert(self, rel: str) -> None:
        full = self.root / rel
        if not full.is_file():
            # Race: the file was deleted between the event firing and now.
            self._apply_external_delete(rel)
            return
        try:
            content = full.read_text(encoding="utf-8")
        except OSError:
            log.warning("watcher could not read %s; skipping", rel)
            return
        with self._lock:
            self._index.upsert_note(IndexedNote(path=rel, content=content))
        log.info("watcher upsert path=%s", rel)

    def _apply_external_delete(self, rel: str) -> None:
        if (self.root / rel).exists():
            # Editor save patterns can fire delete-then-create; if the file
            # is back, treat it as an upsert.
            self._apply_external_upsert(rel)
            return
        with self._lock:
            self._index.delete_note(rel)
        log.info("watcher delete path=%s", rel)

    def list(
        self,
        path: str,
        sort_by: ListSortBy = ListSortBy.NAME,
        sort_order: SortOrder = SortOrder.ASC,
    ) -> list[dict[str, Any]]:
        sort_by = ListSortBy(sort_by)
        sort_order = SortOrder(sort_order)
        directory = self.resolve(path)
        if not directory.is_dir():
            raise ValueError(f"Not a directory: {path}")
        if directory != self.root and self._is_ignored_path(directory):
            raise ValueError(f"Refusing to list hidden path: {path}")
        entries = []
        for child in directory.iterdir():
            if self._is_ignored_path(child):
                continue
            entries.append(entry_for(child, self.relative(child)))
        return sort_entries(entries, sort_by, sort_order)

    def read(self, path: str) -> dict[str, Any]:
        file_path = self.resolve(path)
        if not file_path.is_file():
            raise ValueError(f"Not a file: {path}")
        if self._is_ignored_path(file_path):
            raise ValueError(f"Refusing to read hidden path: {path}")
        return read_note(self.root, file_path)

    def create_note(
        self,
        path: str,
        content: str,
        frontmatter: dict[str, Any] | None,
        overwrite: bool,
    ) -> dict[str, Any]:
        with self._lock:
            note_path = self.resolve_for_write(ensure_markdown_extension(path))
            if note_path.exists() and not overwrite:
                raise FileExistsError(f"Refusing to overwrite existing note: {path}")
            note_path.parent.mkdir(parents=True, exist_ok=True)
            rendered = render_new_note(content, frontmatter)
            self._atomic_write(note_path, rendered)
            rel = self.relative(note_path)
            self._index.upsert_note(IndexedNote(path=rel, content=rendered))
            log.info("create_note path=%s", rel)
            return {"ok": True, "path": rel}

    def update_note(
        self,
        path: str,
        content: str | None,
        frontmatter_patch: dict[str, Any] | None,
    ) -> dict[str, Any]:
        with self._lock:
            note_path = self.resolve(path)
            if not note_path.is_file():
                raise ValueError(f"Not a file: {path}")
            if self._is_ignored_path(note_path):
                raise ValueError(f"Refusing to update hidden path: {path}")
            existing = note_path.read_text(encoding="utf-8")
            next_content, previous_body = render_updated_note(
                existing, content, frontmatter_patch
            )
            changed = next_content != existing
            if changed:
                self._atomic_write(note_path, next_content)
                rel = self.relative(note_path)
                self._index.upsert_note(IndexedNote(path=rel, content=next_content))
                log.info("update_note path=%s", rel)
            return {
                "ok": True,
                "path": self.relative(note_path),
                "changed": changed,
                "previous_body": previous_body,
            }

    def move_path(
        self, source: str, destination: str, rewrite_links: bool, overwrite: bool
    ) -> dict[str, Any]:
        with self._lock:
            return self._move_path_locked(source, destination, rewrite_links, overwrite)

    def _move_path_locked(
        self, source: str, destination: str, rewrite_links: bool, overwrite: bool
    ) -> dict[str, Any]:
        original_source = source
        src = self.resolve(source)
        clean_destination = clean_relative_path(destination)
        dst = self.resolve_for_write(destination)
        if not src.exists():
            raise FileNotFoundError(source)
        if self._is_ignored_path(src):
            raise ValueError(f"Refusing to move hidden path: {source}")
        if self._is_ignored_relative_path(clean_destination, is_directory=src.is_dir()):
            raise ValueError(f"Refusing to move to hidden path: {destination}")
        if dst.exists() and not overwrite:
            raise FileExistsError(f"Destination already exists: {destination}")

        old_rel = self._relative_str(src)
        old_names = self._link_names_for(src)
        pending_rewrites = []
        if rewrite_links and src.suffix == ".md" and dst.suffix == ".md" and old_names:
            pending_rewrites = plan_wikilink_rewrites(
                root=self.root,
                src=src,
                dst=dst,
                old_names=old_names,
                relative_str=self._relative_str,
                is_ignored=self._is_ignored_path,
            )

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        applied_rewrites: list[tuple[Path, str]] = []
        try:
            for path, new_content in pending_rewrites:
                if path.resolve() == src.resolve():
                    write_path = dst
                elif overwrite and path.resolve() == dst.resolve():
                    continue
                else:
                    write_path = path
                self._atomic_write(write_path, new_content)
                applied_rewrites.append((write_path, new_content))
        except BaseException:
            try:
                shutil.move(str(dst), str(src))
            except Exception:
                log.exception(
                    "rollback of move %s -> %s failed", original_source, destination
                )
            raise

        if src.suffix == ".md":
            self._index.delete_note(old_rel)
        if dst.is_file() and dst.suffix == ".md":
            self._index.upsert_note(
                IndexedNote(
                    path=self.relative(dst), content=dst.read_text(encoding="utf-8")
                )
            )
        for path, content in applied_rewrites:
            self._index.upsert_note(
                IndexedNote(path=self.relative(path), content=content)
            )

        log.info(
            "move_path source=%s destination=%s rewritten=%d",
            original_source,
            self.relative(dst),
            len(pending_rewrites),
        )
        return {
            "ok": True,
            "source": original_source,
            "destination": self.relative(dst),
            "rewritten_files": len(applied_rewrites),
        }

    def delete_path(
        self, path: str, recursive: bool, strategy: DeleteStrategy
    ) -> dict[str, Any]:
        strategy = DeleteStrategy(strategy)
        with self._lock:
            target = self._validated_delete_target(path, recursive)
            log.info(
                "delete_path path=%s strategy=%s recursive=%s",
                path,
                strategy.value,
                recursive,
            )
            affected_md = self._affected_markdown_paths(target)
            result = _DELETE_DISPATCH[strategy](self, path, target)
            for md in affected_md:
                self._index.delete_note(md)
            return result

    def _validated_delete_target(self, path: str, recursive: bool) -> Path:
        target = self.resolve(path)
        if target == self.root:
            raise ValueError("Refusing to delete the vault root")
        if self._is_ignored_path(target):
            raise ValueError(f"Refusing to delete reserved path: {path}")
        if not target.exists():
            raise FileNotFoundError(path)
        if target.is_dir():
            visible = [c for c in target.iterdir() if not self._is_ignored_path(c)]
            if visible and not recursive:
                raise ValueError("Directory is not empty; pass recursive=True")
        return target

    def _affected_markdown_paths(self, target: Path) -> list[str]:
        if target.is_file() and target.suffix == ".md":
            return [self.relative(target)]
        if target.is_dir():
            return [
                self.relative(p)
                for p in target.rglob("*.md")
                if p.is_file() and not self._is_ignored_path(p)
            ]
        return []

    def _delete_to_trash(self, path: str, target: Path) -> dict[str, Any]:
        trash = self._trash_dir()
        trash.mkdir(parents=True, exist_ok=True)
        destination = self._unique_trash_destination(trash, target.name)
        shutil.move(str(target), str(destination))
        return {"ok": True, "path": path, "trashed_to": self.relative(destination)}

    def _delete_permanently(self, path: str, target: Path) -> dict[str, Any]:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"ok": True, "path": path, "deleted": True}

    def search(self, query: str, limit: int, mode: SearchMode) -> SearchResult:
        if limit < 1 or limit > MAX_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        return self._index.search(query=query, limit=limit, mode=SearchMode(mode))

    def sync_from_disk(self) -> dict[str, int]:
        """Reconcile the index against the current contents of the vault.

        Walks every .md file, upserts changed/new notes, deletes index entries
        whose files are gone, and (if embeddings are enabled) backfills any
        notes whose embedding is missing or stale.

        Returns a small summary dict so callers can log the diff.
        """
        with self._lock:
            on_disk = self._markdown_files()
            summary = sync_index(self._index, on_disk)
            log.info(
                "sync_from_disk +%d ~%d -%d (unchanged=%d, embedded=%d)",
                summary["added"],
                summary["modified"],
                summary["removed"],
                summary["unchanged"],
                summary["embedded"],
            )
            return summary

    def backlinks(self, path: str) -> dict[str, Any]:
        target = self.resolve(path)
        if not target.exists():
            raise FileNotFoundError(path)
        if self._is_ignored_path(target):
            raise ValueError(f"Refusing to inspect hidden path: {path}")
        names = self._link_names_for(target)
        hits = []
        for candidate, content in self._markdown_files().items():
            if candidate == self.relative(target):
                continue
            matched = [link.raw for link in wikilinks(content) if link.target in names]
            if matched:
                hits.append({"path": candidate, "links": matched})
        return {"path": self.relative(target), "backlinks": hits}

    def reindex(self) -> dict[str, int]:
        """Force a full reconciliation against disk. Used by vault_reindex
        when the user knows files changed out-of-band."""
        return self.sync_from_disk()

    def resolve(self, path: str) -> Path:
        clean = clean_relative_path(path)
        resolved = (self.root / clean).resolve()
        self._ensure_inside_root(resolved)
        return resolved

    def resolve_for_write(self, path: str) -> Path:
        clean = clean_relative_path(path)
        if self._is_ignored_relative_path(clean, is_directory=False):
            raise ValueError("Path points to hidden or reserved vault storage")
        candidate = self.root / clean
        parent = candidate.parent.resolve()
        self._ensure_inside_root(parent)
        return parent / candidate.name

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _relative_str(self, path: Path) -> str:
        """Like relative() but does not require the path to exist on disk."""
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except FileNotFoundError, ValueError:
            return path.relative_to(self.root).as_posix()

    def _unique_trash_destination(self, trash_dir: Path, target_name: str) -> Path:
        timestamp = datetime.now(timezone.utc).strftime(TRASH_TIMESTAMP_FORMAT)
        base = trash_dir / f"{timestamp}-{target_name}"
        candidate = base
        suffix = 1
        while candidate.exists():
            candidate = base.with_name(f"{base.stem}-{suffix}{base.suffix}")
            suffix += 1
        return candidate

    def _ensure_inside_root(self, path: Path) -> None:
        if os.path.commonpath([self.root, path]) != str(self.root):
            raise ValueError("Path escapes vault root")

    def _markdown_files(self) -> dict[str, str]:
        files = {}
        for path in self._iter_markdown_paths():
            files[self.relative(path)] = path.read_text(encoding="utf-8")
        return files

    def _iter_markdown_paths(self) -> list[Path]:
        return [
            path
            for path in self.root.rglob("*.md")
            if path.is_file() and not self._is_ignored_path(path)
        ]

    def _link_names_for(self, path: Path) -> set[str]:
        return link_names_for(self.relative(path), path.stem, path.suffix)

    def _is_ignored_path(self, path: Path) -> bool:
        relative = path.relative_to(self.root)
        return self._is_ignored_relative_path(relative, is_directory=path.is_dir())

    def _is_ignored_relative_path(self, path: Path, *, is_directory: bool) -> bool:
        return is_ignored_relative_path(
            path, trash_path=self.settings.trash_path, is_directory=is_directory
        )

    def _trash_dir(self) -> Path:
        clean = clean_relative_path(self.settings.trash_path)
        candidate = self.root / clean
        resolved_parent = candidate.parent.resolve()
        self._ensure_inside_root(resolved_parent)
        return resolved_parent / candidate.name

    def _atomic_write(self, path: Path, content: str) -> None:
        tmp = temporary_write_path(path)
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(path)
        except BaseException:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise


_DELETE_DISPATCH = {
    DeleteStrategy.TRASH: Vault._delete_to_trash,
    DeleteStrategy.DELETE: Vault._delete_permanently,
}
