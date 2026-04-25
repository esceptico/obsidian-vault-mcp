import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from obsidian_mcp.store import SearchStore, StoredNote


def _note(**overrides) -> StoredNote:
    base = StoredNote(
        path="Alpha.md",
        title="Alpha",
        frontmatter_json="{}",
        body="alpha body",
        tags_text="",
        search_text="Alpha alpha body",
        content_hash="hash-1",
    )
    return replace(base, **overrides)


class StoreTests(unittest.TestCase):
    def test_replace_notes_and_search_fts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "index.sqlite")
            store.replace_notes([_note(body="semantic search notes", search_text="Alpha semantic search notes", tags_text="ai")])
            hits = store.search_fts('"semantic"', 10)

        self.assertEqual(hits[0].path, "Alpha.md")
        self.assertEqual(hits[0].title, "Alpha")

    def test_upsert_then_delete_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "i.sqlite")
            store.upsert_note(_note())
            self.assertEqual(store.search_fts('"alpha"', 10)[0].path, "Alpha.md")
            store.delete_note("Alpha.md")
            self.assertEqual(store.search_fts('"alpha"', 10), [])

    def test_replace_notes_evicts_changed_embeddings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "i.sqlite")
            note = _note()
            store.replace_notes([note])
            store.upsert_embeddings([note], [[0.1, 0.2]], "m", 2)
            self.assertEqual(set(store.embedding_metadata("m", 2)), {"Alpha.md"})

            new = _note(body="y", search_text="Alpha y", content_hash="hash-2")
            store.replace_notes([new])
            self.assertEqual(store.embedding_metadata("m", 2), {})


if __name__ == "__main__":
    unittest.main()
