import tempfile
import time
import unittest
from pathlib import Path
from threading import Event

from obsidian_vault_mcp.vault.watcher import VaultWatcher

# Watcher tests poll for FS events; give the OS a real (small) timeout
# rather than guess timing. Total per test stays well under a second.
_DEBOUNCE = 0.05
_WAIT_TIMEOUT = 2.0


def _wait_for(event: Event, *, timeout: float = _WAIT_TIMEOUT) -> bool:
    return event.wait(timeout=timeout)


class WatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # macOS tempdirs live under /var → /private/var; resolve so watchdog's
        # FSEvents observer sees the same path the test is writing to.
        self.root = Path(self._tmp.name).resolve()
        self.upserts: list[str] = []
        self.deletes: list[str] = []
        self.upsert_event = Event()
        self.delete_event = Event()

        def on_upsert(rel: str) -> None:
            self.upserts.append(rel)
            self.upsert_event.set()

        def on_delete(rel: str) -> None:
            self.deletes.append(rel)
            self.delete_event.set()

        def is_ignored(_path: Path) -> bool:
            return False

        self.watcher = VaultWatcher(
            root=self.root,
            on_upsert=on_upsert,
            on_delete=on_delete,
            is_ignored=is_ignored,
            debounce_seconds=_DEBOUNCE,
        )
        self.watcher.start()
        self.addCleanup(self.watcher.stop)

    def test_create_event_triggers_upsert(self) -> None:
        (self.root / "Note.md").write_text("hi", encoding="utf-8")
        self.assertTrue(_wait_for(self.upsert_event), "expected upsert event")
        self.assertIn("Note.md", self.upserts)

    def test_delete_event_triggers_delete(self) -> None:
        target = self.root / "Note.md"
        target.write_text("hi", encoding="utf-8")
        self.assertTrue(_wait_for(self.upsert_event))
        self.upsert_event.clear()
        target.unlink()
        self.assertTrue(_wait_for(self.delete_event))
        self.assertIn("Note.md", self.deletes)

    def test_ignored_extensions_are_skipped(self) -> None:
        (self.root / "skipme.txt").write_text("nope", encoding="utf-8")
        # No event should fire within a debounce-sized window.
        self.assertFalse(self.upsert_event.wait(timeout=_DEBOUNCE * 4))

    def test_burst_of_modifications_debounces_to_one_upsert(self) -> None:
        target = self.root / "Note.md"
        # Many writes in rapid succession should coalesce into a single
        # upsert (within the debounce window). We can't *fully* guarantee
        # the OS doesn't deliver gaps long enough to break debouncing in
        # CI, so we just assert "much fewer than the number of writes."
        for i in range(20):
            target.write_text(f"v{i}", encoding="utf-8")
            time.sleep(_DEBOUNCE / 10)
        time.sleep(_DEBOUNCE * 4)
        # Allow up to a couple of upserts for unlucky timing, but never 20.
        self.assertGreaterEqual(len(self.upserts), 1)
        self.assertLess(len(self.upserts), 5)


class WatcherIgnoreTests(unittest.TestCase):
    def test_is_ignored_predicate_blocks_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            ignored_dir = root / ".obsidian-vault-mcp"
            ignored_dir.mkdir()

            seen: list[str] = []
            event = Event()

            def on_upsert(rel: str) -> None:
                seen.append(rel)
                event.set()

            watcher = VaultWatcher(
                root=root,
                on_upsert=on_upsert,
                on_delete=lambda _r: None,
                is_ignored=lambda p: ".obsidian-vault-mcp" in p.parts,
                debounce_seconds=_DEBOUNCE,
            )
            watcher.start()
            try:
                (ignored_dir / "internal.md").write_text("x", encoding="utf-8")
                self.assertFalse(event.wait(timeout=_DEBOUNCE * 4))
            finally:
                watcher.stop()


if __name__ == "__main__":
    unittest.main()
