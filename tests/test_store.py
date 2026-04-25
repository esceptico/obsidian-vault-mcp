import sqlite3
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
    def test_upsert_then_search_fts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "i.sqlite")
            store.upsert_note(_note(body="semantic search notes", search_text="Alpha semantic", tags_text="ai"))
            hits = store.search_fts('"semantic"', 10)
        self.assertEqual(hits[0].path, "Alpha.md")
        self.assertEqual(hits[0].title, "Alpha")

    def test_upsert_is_idempotent_when_hash_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "i.sqlite")
            r1 = store.upsert_note(_note())
            r2 = store.upsert_note(_note())
        self.assertEqual(r1, r2)

    def test_upsert_replaces_fts_row_on_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "i.sqlite")
            store.upsert_note(_note(body="cucumber", search_text="X cucumber", content_hash="h1"))
            store.upsert_note(_note(body="zucchini", search_text="X zucchini", content_hash="h2"))
            hits_old = store.search_fts('"cucumber"', 10)
            hits_new = store.search_fts('"zucchini"', 10)
        self.assertEqual(hits_old, [])
        self.assertEqual(hits_new[0].path, "Alpha.md")

    def test_delete_removes_fts_and_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "i.sqlite")
            store.upsert_note(_note())
            store.delete_note("Alpha.md")
            self.assertEqual(store.search_fts('"alpha"', 10), [])
            self.assertEqual(store.all_records(), {})

    def test_search_fts_propagates_real_syntax_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "i.sqlite")
            store.upsert_note(_note())
            with self.assertRaises(sqlite3.OperationalError):
                store.search_fts("AND", 1)

    def test_vector_upsert_and_knn_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "i.sqlite")
            ai_id = store.upsert_note(_note(path="AI.md", title="AI", search_text="AI", body="ai", content_hash="ai-h"))
            food_id = store.upsert_note(_note(path="Food.md", title="Food", search_text="Food", body="food", content_hash="food-h"))
            store.upsert_embeddings(
                [(ai_id, "ai-h", [1.0, 0.0]), (food_id, "food-h", [0.0, 1.0])],
                model="m",
                dimensions=2,
            )
            hits = store.search_vectors([0.99, 0.10], limit=2, model="m", dimensions=2)
        self.assertEqual(hits[0].path, "AI.md")
        self.assertEqual(hits[1].path, "Food.md")
        self.assertLess(hits[0].distance, hits[1].distance)

    def test_changing_content_hash_drops_stale_embedding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "i.sqlite")
            rowid = store.upsert_note(_note(content_hash="h1"))
            store.upsert_embeddings([(rowid, "h1", [1.0, 0.0])], model="m", dimensions=2)
            self.assertEqual(len(store.search_vectors([1.0, 0.0], 5, "m", 2)), 1)

            # Re-upsert with new content_hash; embedding should be evicted.
            store.upsert_note(_note(body="changed", search_text="Alpha changed", content_hash="h2"))
            self.assertEqual(store.search_vectors([1.0, 0.0], 5, "m", 2), [])

            meta = store.all_records()["Alpha.md"]
            self.assertIsNone(meta.embedded_hash)

    def test_dim_mismatch_recreates_vec_table_and_clears_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "i.sqlite")
            rowid = store.upsert_note(_note())
            store.upsert_embeddings([(rowid, "hash-1", [1.0, 0.0])], model="m", dimensions=2)
            self.assertEqual(len(store.search_vectors([1.0, 0.0], 5, "m", 2)), 1)

            # Switching dim from 2 to 3 must drop the table and clear all
            # embedded_* fields so callers know to re-embed.
            store.upsert_embeddings(
                [(rowid, "hash-1", [1.0, 0.0, 0.0])], model="m", dimensions=3
            )
            self.assertEqual(len(store.search_vectors([1.0, 0.0, 0.0], 5, "m", 3)), 1)


if __name__ == "__main__":
    unittest.main()
