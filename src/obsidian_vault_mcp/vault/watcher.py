"""Filesystem watcher that pushes vault edits into the search index.

The MCP server can keep its index live with no manual `vault_reindex`
even when notes are edited outside the server (Obsidian Desktop, sync
clients, `git pull`, etc.).

Events are debounced per-path because most editors save through several
intermediate FS operations (write tmp, rename, fsync) that fire as
distinct watchdog events. We coalesce them into a single index update
after the path has been quiet for `debounce_seconds`.
"""

import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from obsidian_vault_mcp.core.logging import get_logger

log = get_logger("watcher")

_MARKDOWN_SUFFIX = ".md"


class VaultWatcher:
    """Observes a directory tree and dispatches markdown create/modify/delete
    events to caller-supplied handlers, debounced per-path."""

    def __init__(
        self,
        root: Path,
        on_upsert: Callable[[str], None],
        on_delete: Callable[[str], None],
        is_ignored: Callable[[Path], bool],
        debounce_seconds: float,
    ):
        self._root = root
        self._on_upsert = on_upsert
        self._on_delete = on_delete
        self._is_ignored = is_ignored
        self._debounce = debounce_seconds
        self._timers: dict[str, threading.Timer] = {}
        self._timers_lock = threading.Lock()
        self._observer: Observer | None = None

    def start(self) -> None:
        if self._observer is not None:
            return
        self._observer = Observer()
        self._observer.schedule(_Handler(self), str(self._root), recursive=True)
        self._observer.start()
        log.info("watcher started on %s", self._root)

    def stop(self) -> None:
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=2.0)
        self._observer = None
        with self._timers_lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
        log.info("watcher stopped")

    def _schedule(self, path: Path, deletion: bool) -> None:
        if path.suffix != _MARKDOWN_SUFFIX or self._is_ignored(path):
            return
        try:
            rel = path.relative_to(self._root).as_posix()
        except ValueError:
            return
        with self._timers_lock:
            existing = self._timers.pop(rel, None)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(self._debounce, self._fire, args=(rel, deletion))
            timer.daemon = True
            self._timers[rel] = timer
            timer.start()

    def _fire(self, rel: str, deletion: bool) -> None:
        with self._timers_lock:
            self._timers.pop(rel, None)
        try:
            if deletion:
                self._on_delete(rel)
            else:
                self._on_upsert(rel)
        except Exception:
            log.exception("watcher handler raised for %s", rel)


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: VaultWatcher):
        self._w = watcher

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._w._schedule(Path(event.src_path), deletion=False)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._w._schedule(Path(event.src_path), deletion=False)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._w._schedule(Path(event.src_path), deletion=True)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._w._schedule(Path(event.src_path), deletion=True)
        if event.dest_path:
            self._w._schedule(Path(event.dest_path), deletion=False)
