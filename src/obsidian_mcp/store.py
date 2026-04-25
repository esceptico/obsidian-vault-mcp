import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


PRAGMA_JOURNAL_MODE = "PRAGMA journal_mode=WAL"
PRAGMA_SYNCHRONOUS = "PRAGMA synchronous=NORMAL"

CREATE_NOTES_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes USING fts5(
    path UNINDEXED,
    title,
    frontmatter,
    body,
    tags,
    tokenize = 'unicode61'
)
"""

CREATE_EMBEDDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS note_embeddings(
    path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER,
    vector TEXT NOT NULL
)
"""

DELETE_NOTES = "DELETE FROM notes"

INSERT_NOTE = """
INSERT INTO notes(path, title, frontmatter, body, tags)
VALUES (?, ?, ?, ?, ?)
"""

SEARCH_FTS = """
SELECT
    path,
    title,
    bm25(notes) AS score,
    snippet(notes, 3, '[', ']', ' ... ', 32) AS snippet
FROM notes
WHERE notes MATCH ?
ORDER BY score
LIMIT ?
"""

SELECT_EMBEDDINGS_FOR_SEARCH = """
SELECT e.path, e.vector, n.title, snippet(notes, 3, '[', ']', ' ... ', 32) AS snippet
FROM note_embeddings e
JOIN notes n ON n.path = e.path
WHERE e.model = ? AND (e.dimensions IS ? OR e.dimensions = ?)
"""

SELECT_EMBEDDING_METADATA = """
SELECT path, content_hash, model, dimensions
FROM note_embeddings
WHERE model = ? AND (dimensions IS ? OR dimensions = ?)
"""

UPSERT_EMBEDDING = """
INSERT OR REPLACE INTO note_embeddings(path, content_hash, model, dimensions, vector)
VALUES (?, ?, ?, ?, ?)
"""

DELETE_ALL_EMBEDDINGS = "DELETE FROM note_embeddings"
DELETE_STALE_EMBEDDINGS_TEMPLATE = "DELETE FROM note_embeddings WHERE path NOT IN ({placeholders})"


@dataclass(frozen=True)
class StoredNote:
    path: str
    title: str
    frontmatter_json: str
    body: str
    tags_text: str
    search_text: str
    content_hash: str


@dataclass(frozen=True)
class FtsHit:
    path: str
    score: float
    title: str
    snippet: str


@dataclass(frozen=True)
class StoredEmbedding:
    path: str
    vector: list[float]
    title: str
    snippet: str


@dataclass(frozen=True)
class EmbeddingMetadata:
    path: str
    content_hash: str


class SearchStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(PRAGMA_JOURNAL_MODE)
            connection.execute(PRAGMA_SYNCHRONOUS)
            connection.execute(CREATE_NOTES_TABLE)
            connection.execute(CREATE_EMBEDDINGS_TABLE)

    def replace_notes(self, notes: list[StoredNote]) -> None:
        with self.connect() as connection:
            connection.execute(DELETE_NOTES)
            for note in notes:
                connection.execute(
                    INSERT_NOTE,
                    (note.path, note.title, note.frontmatter_json, note.body, note.tags_text),
                )
            self.delete_stale_embeddings(connection, {note.path for note in notes})

    def search_fts(self, query: str, limit: int) -> list[FtsHit]:
        try:
            with self.connect() as connection:
                rows = connection.execute(SEARCH_FTS, (query, limit)).fetchall()
        except sqlite3.OperationalError as exc:
            if "fts5" in str(exc).lower():
                raise RuntimeError("This Python SQLite build does not include FTS5 support") from exc
            raise

        return [
            FtsHit(
                path=row["path"],
                score=float(row["score"]),
                title=row["title"],
                snippet=row["snippet"],
            )
            for row in rows
        ]

    def embedding_metadata(self, model: str, dimensions: int | None) -> dict[str, EmbeddingMetadata]:
        with self.connect() as connection:
            rows = connection.execute(SELECT_EMBEDDING_METADATA, (model, dimensions, dimensions)).fetchall()
        return {
            row["path"]: EmbeddingMetadata(path=row["path"], content_hash=row["content_hash"])
            for row in rows
        }

    def upsert_embeddings(
        self,
        records: list[StoredNote],
        vectors: list[list[float]],
        model: str,
        dimensions: int | None,
    ) -> None:
        with self.connect() as connection:
            for record, vector in zip(records, vectors, strict=True):
                connection.execute(
                    UPSERT_EMBEDDING,
                    (
                        record.path,
                        record.content_hash,
                        model,
                        dimensions,
                        json.dumps(vector, separators=(",", ":")),
                    ),
                )

    def stored_embeddings(self, model: str, dimensions: int | None) -> list[StoredEmbedding]:
        with self.connect() as connection:
            rows = connection.execute(SELECT_EMBEDDINGS_FOR_SEARCH, (model, dimensions, dimensions)).fetchall()

        return [
            StoredEmbedding(
                path=row["path"],
                vector=json.loads(row["vector"]),
                title=row["title"],
                snippet=row["snippet"],
            )
            for row in rows
        ]

    def delete_stale_embeddings(self, connection: sqlite3.Connection, paths: set[str]) -> None:
        if not paths:
            connection.execute(DELETE_ALL_EMBEDDINGS)
            return
        placeholders = ",".join("?" for _ in paths)
        connection.execute(DELETE_STALE_EMBEDDINGS_TEMPLATE.format(placeholders=placeholders), tuple(paths))

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection
