import os
import secrets
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from obsidian_mcp.config import EmbeddingSettings, VaultSettings
from obsidian_mcp.frontmatter import (
    patch_frontmatter,
    render_frontmatter,
    split_frontmatter,
    split_frontmatter_raw,
)
from obsidian_mcp.logging import get_logger
from obsidian_mcp.obsidian import block_ids, inline_tags, markdown_links, rewrite_wikilink_targets, wikilinks
from obsidian_mcp.search import IndexedNote, SearchIndex

log = get_logger("vault")


@dataclass(frozen=True)
class VaultEntry:
    path: str
    kind: str
    size: int
    modified_at: str


class Vault:
    def __init__(self, settings: VaultSettings, embeddings: EmbeddingSettings | None = None):
        self.settings = settings
        self.root = settings.root.resolve()
        if not self.root.exists() or not self.root.is_dir():
            raise RuntimeError(f"Vault root does not exist or is not a directory: {self.root}")
        self._index = SearchIndex(self.root / ".obsidian-mcp" / "index.sqlite", embeddings or EmbeddingSettings())
        # Full rebuild only on first start (empty index) or explicit vault_reindex.
        # Subsequent writes go through upsert/delete.
        self._index_dirty = self._index.store.count_notes() == 0
        self._lock = threading.RLock()

    def list(self, path: str = "") -> list[dict[str, Any]]:
        directory = self.resolve(path)
        if not directory.is_dir():
            raise ValueError(f"Not a directory: {path}")
        entries = []
        for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if self._is_ignored_path(child):
                continue
            stat = child.stat()
            entries.append(
                VaultEntry(
                    path=self.relative(child),
                    kind="directory" if child.is_dir() else "file",
                    size=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                ).__dict__
            )
        return entries

    def read(self, path: str) -> dict[str, Any]:
        file_path = self.resolve(path)
        if not file_path.is_file():
            raise ValueError(f"Not a file: {path}")
        content = file_path.read_text(encoding="utf-8")
        frontmatter, body = split_frontmatter(content)
        return {
            "path": self.relative(file_path),
            "frontmatter": frontmatter,
            "body": body,
            "content": content,
            "wikilinks": [link.__dict__ for link in wikilinks(body)],
            "markdown_links": markdown_links(body),
            "tags": sorted(set(_frontmatter_tags(frontmatter) + inline_tags(body))),
            "block_ids": block_ids(body),
        }

    def create_note(
        self,
        path: str,
        content: str = "",
        frontmatter: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            note_path = self.resolve_for_write(_ensure_md(path))
            if note_path.exists() and not overwrite:
                raise FileExistsError(f"Refusing to overwrite existing note: {path}")
            note_path.parent.mkdir(parents=True, exist_ok=True)
            rendered = render_frontmatter(frontmatter or {}, content)
            self._atomic_write(note_path, rendered)
            rel = self.relative(note_path)
            self._index.upsert_note(IndexedNote(path=rel, content=rendered))
            log.info("create_note path=%s", rel)
            return {"ok": True, "path": rel}

    def update_note(
        self,
        path: str,
        content: str | None = None,
        frontmatter_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            note_path = self.resolve(path)
            if not note_path.is_file():
                raise ValueError(f"Not a file: {path}")
            existing = note_path.read_text(encoding="utf-8")
            _, body = split_frontmatter(existing)
            next_content = existing
            if content is not None:
                current_frontmatter, _ = split_frontmatter_raw(existing)
                next_content = render_frontmatter(current_frontmatter, content)
            if frontmatter_patch:
                next_content = patch_frontmatter(next_content, frontmatter_patch)
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
                "previous_body": body,
            }

    def move_path(self, source: str, destination: str, rewrite_links: bool = True, overwrite: bool = False) -> dict[str, Any]:
        with self._lock:
            return self._move_path_locked(source, destination, rewrite_links, overwrite)

    def _move_path_locked(self, source: str, destination: str, rewrite_links: bool, overwrite: bool) -> dict[str, Any]:
        original_source = source
        src = self.resolve(source)
        dst = self.resolve_for_write(destination)
        if not src.exists():
            raise FileNotFoundError(source)
        if dst.exists() and not overwrite:
            raise FileExistsError(f"Destination already exists: {destination}")

        old_rel = self._relative_str(src)
        old_names = self._link_names_for(src)
        is_note_rename = (
            rewrite_links and src.suffix == ".md" and dst.suffix == ".md" and bool(old_names)
        )

        pending_rewrites: list[tuple[Path, str]] = []
        if is_note_rename:
            new_bare = dst.stem
            new_qualified = Path(self._relative_str(dst)).with_suffix("").as_posix()
            same_stem_exists = any(
                other.is_file()
                and other.suffix == ".md"
                and other.stem == new_bare
                and other.resolve() != src.resolve()
                and other.resolve() != dst.resolve()
                and not self._is_ignored_path(other)
                for other in self.root.rglob("*.md")
            )

            def replacement_for(matched_old: str) -> str:
                if "/" in matched_old or same_stem_exists:
                    return new_qualified
                return new_bare

            for path in self.root.rglob("*.md"):
                if not path.is_file() or self._is_ignored_path(path):
                    continue
                original = path.read_text(encoding="utf-8")
                updated = rewrite_wikilink_targets(original, old_names, replacement_for)
                if updated != original:
                    pending_rewrites.append((path, updated))

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        try:
            for path, new_content in pending_rewrites:
                self._atomic_write(path, new_content)
        except BaseException:
            try:
                shutil.move(str(dst), str(src))
            except Exception:
                log.exception("rollback of move %s -> %s failed", original_source, destination)
            raise

        if src.suffix == ".md":
            self._index.delete_note(old_rel)
        if dst.is_file() and dst.suffix == ".md":
            self._index.upsert_note(
                IndexedNote(path=self.relative(dst), content=dst.read_text(encoding="utf-8"))
            )
        for path, content in pending_rewrites:
            self._index.upsert_note(IndexedNote(path=self.relative(path), content=content))

        log.info(
            "move_path source=%s destination=%s rewritten=%d",
            original_source, self.relative(dst), len(pending_rewrites),
        )
        return {
            "ok": True,
            "source": original_source,
            "destination": self.relative(dst),
            "rewritten_files": len(pending_rewrites),
        }

    def delete_path(self, path: str, recursive: bool = False, strategy: str = "trash") -> dict[str, Any]:
        with self._lock:
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

            log.info("delete_path path=%s strategy=%s recursive=%s", path, strategy, recursive)

            affected_md: list[str] = []
            if target.is_file() and target.suffix == ".md":
                affected_md = [self.relative(target)]
            elif target.is_dir():
                affected_md = [
                    self.relative(p)
                    for p in target.rglob("*.md")
                    if p.is_file() and not self._is_ignored_path(p)
                ]

            if strategy == "trash":
                trash = self.resolve_for_write(self.settings.trash_path)
                trash.mkdir(parents=True, exist_ok=True)
                destination = self._unique_trash_destination(trash, target.name)
                shutil.move(str(target), str(destination))
                result = {"ok": True, "path": path, "trashed_to": self.relative(destination)}
            elif strategy == "delete":
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                result = {"ok": True, "path": path, "deleted": True}
            else:
                raise ValueError("strategy must be 'trash' or 'delete'")

            for md in affected_md:
                self._index.delete_note(md)
            return result

    def search(self, query: str, limit: int = 10, mode: str = "hybrid") -> dict[str, Any]:
        with self._lock:
            if self._index_dirty:
                self._index.rebuild(
                    [IndexedNote(path=path, content=content) for path, content in self._markdown_files().items()]
                )
                self._index_dirty = False
        return self._index.search(query=query, limit=limit, mode=mode)  # type: ignore[arg-type]

    def backlinks(self, path: str) -> dict[str, Any]:
        target = self.resolve(path)
        names = self._link_names_for(target)
        hits = []
        for candidate, content in self._markdown_files().items():
            if candidate == self.relative(target):
                continue
            matched = [link.raw for link in wikilinks(content) if link.target in names]
            if matched:
                hits.append({"path": candidate, "links": matched})
        return {"path": self.relative(target), "backlinks": hits}

    def invalidate_index(self) -> None:
        self._index_dirty = True

    def resolve(self, path: str) -> Path:
        clean = _clean_relative_path(path)
        resolved = (self.root / clean).resolve()
        self._ensure_inside_root(resolved)
        return resolved

    def resolve_for_write(self, path: str) -> Path:
        clean = _clean_relative_path(path)
        if _is_internal_path(clean):
            raise ValueError("Path points to internal obsidian-mcp storage")
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
        except (FileNotFoundError, ValueError):
            return path.relative_to(self.root).as_posix()

    def _unique_trash_destination(self, trash_dir: Path, target_name: str) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
        for path in self.root.rglob("*.md"):
            if path.is_file() and not self._is_ignored_path(path):
                files[self.relative(path)] = path.read_text(encoding="utf-8")
        return files

    def _link_names_for(self, path: Path) -> set[str]:
        names = {path.stem}
        if path.suffix == ".md":
            relative = self.relative(path)
            names.add(Path(relative).with_suffix("").as_posix())
            names.add(relative)
        return names

    def _is_ignored_path(self, path: Path) -> bool:
        parts = path.relative_to(self.root).parts
        return bool(parts) and parts[0] in {self.settings.trash_path, ".obsidian-mcp"}

    def _atomic_write(self, path: Path, content: str) -> None:
        tmp = _tmp_name_for(path)
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


def _tmp_name_for(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")


def _clean_relative_path(path: str) -> Path:
    if not path or path == ".":
        return Path()
    candidate = Path(path)
    if candidate.is_absolute():
        raise ValueError("Vault paths must be relative")
    if any(part in {"..", ""} for part in candidate.parts):
        raise ValueError("Vault path contains unsafe segments")
    return candidate


def _ensure_md(path: str) -> str:
    return path if Path(path).suffix else f"{path}.md"


def _is_internal_path(path: Path) -> bool:
    return ".obsidian-mcp" in path.parts


def _frontmatter_tags(frontmatter: dict[str, Any]) -> list[str]:
    tags = frontmatter.get("tags", [])
    if isinstance(tags, str):
        return [tags.lstrip("#")]
    if isinstance(tags, list):
        return [str(tag).lstrip("#") for tag in tags]
    return []
