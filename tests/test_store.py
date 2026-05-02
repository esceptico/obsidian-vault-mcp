import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import hashlib

from obsidian_vault_mcp.index.store import SearchStore, StoredChunk, StoredNote


def _note(**overrides) -> StoredNote:
    path = overrides.get("path", "Alpha.md")
    title = overrides.get("title", "Alpha")
    frontmatter_json = overrides.get("frontmatter_json", "{}")
    body = overrides.get("body", "alpha body")
    tags_text = overrides.get("tags_text", "")
    search_text = overrides.get("search_text", f"{title} {body}")
    content_hash = overrides.get("content_hash", "hash-1")
    chunk = _chunk(
        path=path,
        title=title,
        frontmatter_json=frontmatter_json,
        body=body,
        tags_text=tags_text,
        search_text=search_text,
    )
    base = StoredNote(
        path=path,
        title=title,
        frontmatter_json=frontmatter_json,
        body=body,
        tags_text=tags_text,
        search_text=search_text,
        content_hash=content_hash,
        chunks=(chunk,),
    )
    return replace(base, **overrides)


def _chunk(
    *,
    path: str = "Alpha.md",
    title: str = "Alpha",
    frontmatter_json: str = "{}",
    body: str = "alpha body",
    tags_text: str = "",
    search_text: str = "Alpha alpha body",
    chunk_index: int = 0,
    heading_path: str = "",
) -> StoredChunk:
    return StoredChunk(
        path=path,
        title=title,
        frontmatter_json=frontmatter_json,
        tags_text=tags_text,
        chunk_index=chunk_index,
        chunk_hash=hashlib.sha256(search_text.encode("utf-8")).hexdigest(),
        heading_path=heading_path,
        text=body,
        search_text=search_text,
        start_char=0,
        end_char=len(body),
    )


class StoreTests(unittest.TestCase):
    def test_upsert_then_search_fts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "i.sqlite")
            store.upsert_note(
                _note(
                    body="semantic search notes",
                    search_text="Alpha semantic",
                    tags_text="ai",
                )
            )
            hits = store.search_fts('"semantic"', 10)
        self.assertEqual(hits[0].path, "Alpha.md")
        self.assertEqual(hits[0].title, "Alpha")
        self.assertEqual(hits[0].chunk_index, 0)

    def test_upsert_is_idempotent_when_hash_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "i.sqlite")
            r1 = store.upsert_note(_note())
            r2 = store.upsert_note(_note())
        self.assertEqual(r1, r2)

    def test_upsert_replaces_fts_row_on_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "i.sqlite")
            store.upsert_note(
                _note(body="cucumber", search_text="X cucumber", content_hash="h1")
            )
            store.upsert_note(
                _note(body="zucchini", search_text="X zucchini", content_hash="h2")
            )
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
            store.upsert_note(
                _note(
                    path="AI.md",
                    title="AI",
                    search_text="AI",
                    body="ai",
                    content_hash="ai-h",
                )
            )
            store.upsert_note(
                _note(
                    path="Food.md",
                    title="Food",
                    search_text="Food",
                    body="food",
                    content_hash="food-h",
                )
            )
            pending = store.pending_embedding_chunks("m", None)
            by_path = {chunk.search_text: chunk for chunk in pending}
            store.upsert_embeddings(
                [
                    (by_path["AI"].rowid, by_path["AI"].chunk_hash, [1.0, 0.0]),
                    (by_path["Food"].rowid, by_path["Food"].chunk_hash, [0.0, 1.0]),
                ],
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
            store.upsert_note(_note(content_hash="h1"))
            chunk = store.pending_embedding_chunks("m", None)[0]
            store.upsert_embeddings(
                [(chunk.rowid, chunk.chunk_hash, [1.0, 0.0])], model="m", dimensions=2
            )
            self.assertEqual(len(store.search_vectors([1.0, 0.0], 5, "m", 2)), 1)

            # Re-upsert with new content_hash; embedding should be evicted.
            store.upsert_note(
                _note(body="changed", search_text="Alpha changed", content_hash="h2")
            )
            self.assertEqual(store.search_vectors([1.0, 0.0], 5, "m", 2), [])

            meta = store.all_records()["Alpha.md"]
            self.assertIsNone(meta.embedded_hash)

    def test_legacy_index_file_self_heals_on_init(self) -> None:
        """A stale index.sqlite from an older schema (FTS rows present, no
        note_meta, no schema_version) must not blow up the first upsert with
        a rowid collision — it should be wiped and rebuilt."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "i.sqlite"
            # Manually plant a legacy `notes` FTS table populated up to rowid=3.
            with closing(sqlite3.connect(db_path)) as raw:
                raw.execute(
                    "CREATE VIRTUAL TABLE notes USING fts5("
                    "path UNINDEXED, title, frontmatter, body, tags, tokenize='unicode61')"
                )
                for i in range(1, 4):
                    raw.execute(
                        "INSERT INTO notes(path, title, frontmatter, body, tags) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (f"Old{i}.md", "old", "{}", "old", ""),
                    )
                raw.commit()
            # New SearchStore should detect the missing schema_version, drop
            # the legacy table, and let upsert_note succeed without collision.
            store = SearchStore(db_path)
            rowid = store.upsert_note(_note(path="Fresh.md", content_hash="fresh"))
            self.assertGreater(rowid, 0)
            self.assertEqual(list(store.all_records()), ["Fresh.md"])

    def test_dim_mismatch_recreates_vec_table_and_clears_meta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "i.sqlite")
            store.upsert_note(_note())
            chunk = store.pending_embedding_chunks("m", None)[0]
            store.upsert_embeddings(
                [(chunk.rowid, chunk.chunk_hash, [1.0, 0.0])], model="m", dimensions=2
            )
            self.assertEqual(len(store.search_vectors([1.0, 0.0], 5, "m", 2)), 1)

            # Switching dim from 2 to 3 must drop the table and clear all
            # embedded_* fields so callers know to re-embed.
            store.upsert_embeddings(
                [(chunk.rowid, chunk.chunk_hash, [1.0, 0.0, 0.0])],
                model="m",
                dimensions=3,
            )
            self.assertEqual(len(store.search_vectors([1.0, 0.0, 0.0], 5, "m", 3)), 1)


if __name__ == "__main__":
    unittest.main()
