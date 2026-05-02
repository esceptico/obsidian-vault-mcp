import sqlite3
import struct
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import sqlite_vec

from headless_obsidian_mcp.core.constants import FTS_SNIPPET_LENGTH

# --- FTS5 snippet config ----------------------------------------------------
_SNIPPET_TARGET_COLUMN = (
    5  # 0-indexed: path, title, heading_path, frontmatter, tags, text
)
_SNIPPET_OPEN = "["
_SNIPPET_CLOSE = "]"
_SNIPPET_ELLIPSIS = " ... "

# --- Schema -----------------------------------------------------------------
_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
)

# Bumped because embeddings moved from one vector per note to one vector per
# chunk. The vault is the source of truth, so stale indexes are rebuilt.
_SCHEMA_VERSION = 2

_CREATE_NOTE_META = """
CREATE TABLE IF NOT EXISTS note_meta(
    rowid        INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT    NOT NULL UNIQUE,
    content_hash TEXT    NOT NULL
)
"""

_CREATE_CHUNK_META = """
CREATE TABLE IF NOT EXISTS chunk_meta(
    rowid               INTEGER PRIMARY KEY AUTOINCREMENT,
    note_rowid          INTEGER NOT NULL REFERENCES note_meta(rowid) ON DELETE CASCADE,
    path                TEXT    NOT NULL,
    chunk_index         INTEGER NOT NULL,
    chunk_hash          TEXT    NOT NULL,
    embedded_hash       TEXT,
    embedded_model      TEXT,
    embedded_dimensions INTEGER,
    start_char          INTEGER NOT NULL,
    end_char            INTEGER NOT NULL,
    heading_path        TEXT    NOT NULL,
    text                TEXT    NOT NULL,
    search_text         TEXT    NOT NULL,
    UNIQUE(note_rowid, chunk_index)
)
"""

_CREATE_CHUNK_PATH_INDEX = """
CREATE INDEX IF NOT EXISTS idx_chunk_meta_path ON chunk_meta(path)
"""

_CREATE_CHUNKS_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    path UNINDEXED,
    title,
    heading_path,
    frontmatter,
    tags,
    text,
    tokenize='unicode61'
)
"""

_CREATE_INDEX_META = """
CREATE TABLE IF NOT EXISTS index_meta(
    key   TEXT PRIMARY KEY,
    value TEXT
)
"""

_SELECT_SCHEMA_VERSION = "SELECT value FROM index_meta WHERE key = 'schema_version'"
_UPSERT_SCHEMA_VERSION = """
INSERT OR REPLACE INTO index_meta(key, value) VALUES ('schema_version', ?)
"""

_SELECT_VEC_DIM = "SELECT value FROM index_meta WHERE key = 'vec_dim'"
_CREATE_VEC_TABLE_TEMPLATE = """
CREATE VIRTUAL TABLE {if_not_exists} vec_chunks USING vec0(
    embedding float[{dimensions}] distance_metric=cosine
)
"""
_DROP_VEC_TABLE = "DROP TABLE IF EXISTS vec_chunks"
_INVALIDATE_EMBEDDINGS = """
UPDATE chunk_meta SET embedded_hash = NULL, embedded_model = NULL,
                      embedded_dimensions = NULL
"""
_UPSERT_VEC_DIM = """
INSERT OR REPLACE INTO index_meta(key, value) VALUES ('vec_dim', ?)
"""

_INSERT_NOTE_META = """
INSERT INTO note_meta(path, content_hash) VALUES (?, ?)
"""

_UPDATE_NOTE_META_HASH = """
UPDATE note_meta SET content_hash = ? WHERE rowid = ?
"""

_DELETE_NOTE_META = "DELETE FROM note_meta WHERE rowid = ?"

_SELECT_META_BY_PATH = """
SELECT rowid, content_hash FROM note_meta WHERE path = ?
"""

_SELECT_ALL_META = """
SELECT
    n.rowid AS rowid,
    n.path AS path,
    n.content_hash AS content_hash,
    COUNT(c.rowid) AS chunk_count,
    SUM(CASE WHEN c.embedded_hash = c.chunk_hash THEN 1 ELSE 0 END) AS embedded_count,
    MIN(c.embedded_model) AS embedded_model,
    MIN(c.embedded_dimensions) AS embedded_dimensions
FROM note_meta n
LEFT JOIN chunk_meta c ON c.note_rowid = n.rowid
GROUP BY n.rowid, n.path, n.content_hash
ORDER BY n.path
"""

_COUNT_META = "SELECT COUNT(*) FROM note_meta"

_INSERT_CHUNK_META = """
INSERT INTO chunk_meta(
    note_rowid, path, chunk_index, chunk_hash, start_char, end_char,
    heading_path, text, search_text
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_CHUNK_FTS = """
INSERT INTO chunks(rowid, path, title, heading_path, frontmatter, tags, text)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_CHUNK_ROWIDS_BY_NOTE = """
SELECT rowid FROM chunk_meta WHERE note_rowid = ?
"""

_DELETE_CHUNK_FTS = "DELETE FROM chunks WHERE rowid = ?"
_DELETE_CHUNK_META = "DELETE FROM chunk_meta WHERE rowid = ?"

_SELECT_PENDING_CHUNKS = """
SELECT rowid, chunk_hash, search_text
FROM chunk_meta
WHERE embedded_hash IS NULL
   OR embedded_hash != chunk_hash
   OR embedded_model IS NULL
   OR embedded_model != ?
   OR (? IS NOT NULL AND embedded_dimensions != ?)
ORDER BY path, chunk_index
"""

_SNIPPET_EXPR = (
    f"snippet(chunks, {_SNIPPET_TARGET_COLUMN}, "
    f"'{_SNIPPET_OPEN}', '{_SNIPPET_CLOSE}', '{_SNIPPET_ELLIPSIS}', {FTS_SNIPPET_LENGTH})"
)

_SEARCH_FTS = f"""
SELECT
    chunks.rowid AS rowid,
    chunks.path AS path,
    chunks.title AS title,
    chunks.heading_path AS heading_path,
    bm25(chunks) AS score,
    {_SNIPPET_EXPR} AS snippet,
    meta.chunk_index AS chunk_index,
    meta.start_char AS start_char,
    meta.end_char AS end_char
FROM chunks
JOIN chunk_meta meta ON meta.rowid = chunks.rowid
WHERE chunks MATCH ?
ORDER BY score
LIMIT ?
"""

_SEARCH_VECTORS = """
WITH knn AS (
    SELECT rowid, distance
    FROM vec_chunks
    WHERE embedding MATCH ? AND k = ?
)
SELECT
    meta.rowid AS rowid,
    meta.path AS path,
    chunks.title AS title,
    meta.heading_path AS heading_path,
    meta.chunk_index AS chunk_index,
    meta.start_char AS start_char,
    meta.end_char AS end_char,
    knn.distance AS distance,
    substr(meta.text, 1, ?) AS snippet
FROM knn
JOIN chunk_meta meta ON meta.rowid = knn.rowid
JOIN chunks ON chunks.rowid = knn.rowid
WHERE meta.embedded_model = ?
  AND meta.embedded_hash = meta.chunk_hash
  AND meta.embedded_dimensions = ?
ORDER BY knn.distance
"""

_INSERT_VEC = "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)"
_DELETE_VEC = "DELETE FROM vec_chunks WHERE rowid = ?"
_SELECT_CHUNK_HASH_BY_ROWID = "SELECT chunk_hash FROM chunk_meta WHERE rowid = ?"
_UPDATE_EMBEDDING_META = """
UPDATE chunk_meta
SET embedded_hash = ?, embedded_model = ?, embedded_dimensions = ?
WHERE rowid = ? AND chunk_hash = ?
"""
_SELECT_VEC_TABLE_EXISTS = """
SELECT name FROM sqlite_master WHERE type='table' AND name='vec_chunks'
"""

_VECTOR_SNIPPET_CHARS = 240


# --- Public types -----------------------------------------------------------


@dataclass(frozen=True)
class StoredChunk:
    path: str
    title: str
    frontmatter_json: str
    tags_text: str
    chunk_index: int
    chunk_hash: str
    heading_path: str
    text: str
    search_text: str
    start_char: int
    end_char: int


@dataclass(frozen=True)
class StoredNote:
    """Indexable form of a note. `content_hash` drives change detection."""

    path: str
    title: str
    frontmatter_json: str
    body: str
    tags_text: str
    search_text: str
    content_hash: str
    chunks: tuple[StoredChunk, ...]


@dataclass(frozen=True)
class PendingChunk:
    rowid: int
    chunk_hash: str
    search_text: str


@dataclass(frozen=True)
class FtsHit:
    chunk_id: int
    path: str
    score: float
    title: str
    heading_path: str
    snippet: str
    chunk_index: int
    start_char: int
    end_char: int


@dataclass(frozen=True)
class VectorHit:
    chunk_id: int
    path: str
    distance: float  # cosine distance: 0 == identical, 2 == opposite
    title: str
    heading_path: str
    snippet: str
    chunk_index: int
    start_char: int
    end_char: int


@dataclass(frozen=True)
class RecordMeta:
    """Snapshot of what the index knows about a path. Used for sync diffs."""

    rowid: int
    path: str
    content_hash: str
    embedded_hash: str | None
    embedded_model: str | None
    embedded_dimensions: int | None
    chunk_count: int = 0
    embedded_count: int = 0


# --- Store ------------------------------------------------------------------


class SearchStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._delete_if_stale_schema()
        self._initialize_schema()

    # ----- schema lifecycle -------------------------------------------------

    def _delete_if_stale_schema(self) -> None:
        if not self.database_path.exists():
            return
        conn = sqlite3.connect(self.database_path)
        try:
            try:
                row = conn.execute(_SELECT_SCHEMA_VERSION).fetchone()
            except sqlite3.OperationalError:
                row = None
        finally:
            conn.close()
        if row is not None and int(row[0]) == _SCHEMA_VERSION:
            return
        for suffix in ("", "-wal", "-shm"):
            stale = Path(str(self.database_path) + suffix)
            if stale.exists():
                stale.unlink()

    def _initialize_schema(self) -> None:
        with self.connect() as conn:
            for pragma in _PRAGMAS:
                conn.execute(pragma)
            try:
                conn.execute(_CREATE_CHUNKS_FTS)
            except sqlite3.OperationalError as exc:
                raise RuntimeError(
                    "This Python SQLite build does not include FTS5 support"
                ) from exc
            conn.execute(_CREATE_NOTE_META)
            conn.execute(_CREATE_CHUNK_META)
            conn.execute(_CREATE_CHUNK_PATH_INDEX)
            conn.execute(_CREATE_INDEX_META)
            conn.execute(_UPSERT_SCHEMA_VERSION, (str(_SCHEMA_VERSION),))

    def _ensure_vec_table(self, conn: sqlite3.Connection, dimensions: int) -> None:
        stored = conn.execute(_SELECT_VEC_DIM).fetchone()
        if stored is None:
            conn.execute(
                _CREATE_VEC_TABLE_TEMPLATE.format(
                    if_not_exists="IF NOT EXISTS", dimensions=dimensions
                )
            )
            conn.execute(_UPSERT_VEC_DIM, (str(dimensions),))
            return
        if int(stored["value"]) == dimensions:
            return
        conn.execute(_DROP_VEC_TABLE)
        conn.execute(
            _CREATE_VEC_TABLE_TEMPLATE.format(if_not_exists="", dimensions=dimensions)
        )
        conn.execute(_INVALIDATE_EMBEDDINGS)
        conn.execute(_UPSERT_VEC_DIM, (str(dimensions),))

    # ----- note CRUD --------------------------------------------------------

    def upsert_note(self, note: StoredNote) -> int:
        with self.connect() as conn:
            existing = conn.execute(_SELECT_META_BY_PATH, (note.path,)).fetchone()
            if existing is None:
                cursor = conn.execute(_INSERT_NOTE_META, (note.path, note.content_hash))
                rowid = cursor.lastrowid
                self._insert_chunks(conn, rowid, note)
                return rowid

            rowid = existing["rowid"]
            if existing["content_hash"] == note.content_hash:
                return rowid

            conn.execute(_UPDATE_NOTE_META_HASH, (note.content_hash, rowid))
            self._delete_chunks_for_note(conn, rowid)
            self._insert_chunks(conn, rowid, note)
            return rowid

    def delete_note(self, path: str) -> None:
        with self.connect() as conn:
            row = conn.execute(_SELECT_META_BY_PATH, (path,)).fetchone()
            if row is None:
                return
            rowid = row["rowid"]
            self._delete_chunks_for_note(conn, rowid)
            conn.execute(_DELETE_NOTE_META, (rowid,))

    def all_records(self) -> dict[str, RecordMeta]:
        with self.connect() as conn:
            rows = conn.execute(_SELECT_ALL_META).fetchall()
        return {
            row["path"]: RecordMeta(
                rowid=row["rowid"],
                path=row["path"],
                content_hash=row["content_hash"],
                embedded_hash=row["content_hash"]
                if row["chunk_count"] and row["chunk_count"] == row["embedded_count"]
                else None,
                embedded_model=row["embedded_model"],
                embedded_dimensions=row["embedded_dimensions"],
                chunk_count=row["chunk_count"],
                embedded_count=row["embedded_count"] or 0,
            )
            for row in rows
        }

    def count_notes(self) -> int:
        with self.connect() as conn:
            return conn.execute(_COUNT_META).fetchone()[0]

    def pending_embedding_chunks(
        self, model: str, dimensions: int | None
    ) -> list[PendingChunk]:
        with self.connect() as conn:
            rows = conn.execute(
                _SELECT_PENDING_CHUNKS, (model, dimensions, dimensions)
            ).fetchall()
        return [
            PendingChunk(
                rowid=row["rowid"],
                chunk_hash=row["chunk_hash"],
                search_text=row["search_text"],
            )
            for row in rows
        ]

    # ----- search -----------------------------------------------------------

    def search_fts(self, query: str, limit: int) -> list[FtsHit]:
        with self.connect() as conn:
            rows = conn.execute(_SEARCH_FTS, (query, limit)).fetchall()
        return [
            FtsHit(
                chunk_id=row["rowid"],
                path=row["path"],
                score=float(row["score"]),
                title=row["title"],
                heading_path=row["heading_path"],
                snippet=row["snippet"],
                chunk_index=row["chunk_index"],
                start_char=row["start_char"],
                end_char=row["end_char"],
            )
            for row in rows
        ]

    def search_vectors(
        self,
        query_vector: list[float],
        limit: int,
        model: str,
        dimensions: int,
    ) -> list[VectorHit]:
        if not self._vec_table_exists():
            return []
        query_blob = _serialize(query_vector)
        with self.connect() as conn:
            rows = conn.execute(
                _SEARCH_VECTORS,
                (query_blob, limit, _VECTOR_SNIPPET_CHARS, model, dimensions),
            ).fetchall()
        return [
            VectorHit(
                chunk_id=row["rowid"],
                path=row["path"],
                distance=float(row["distance"]),
                title=row["title"],
                heading_path=row["heading_path"],
                snippet=row["snippet"],
                chunk_index=row["chunk_index"],
                start_char=row["start_char"],
                end_char=row["end_char"],
            )
            for row in rows
        ]

    # ----- embeddings -------------------------------------------------------

    def upsert_embeddings(
        self,
        items: Iterable[tuple[int, str, list[float]]],
        model: str,
        dimensions: int,
    ) -> None:
        materialized = list(items)
        if not materialized:
            return
        with self.connect() as conn:
            self._ensure_vec_table(conn, dimensions)
            for rowid, chunk_hash, vector in materialized:
                if len(vector) != dimensions:
                    raise ValueError(
                        f"vector for chunk rowid={rowid} has dim {len(vector)}, expected {dimensions}"
                    )
                current = conn.execute(_SELECT_CHUNK_HASH_BY_ROWID, (rowid,)).fetchone()
                if current is None or current["chunk_hash"] != chunk_hash:
                    continue
                self._delete_vec_if_present(conn, rowid)
                conn.execute(_INSERT_VEC, (rowid, _serialize(vector)))
                conn.execute(
                    _UPDATE_EMBEDDING_META,
                    (chunk_hash, model, dimensions, rowid, chunk_hash),
                )

    # ----- helpers ----------------------------------------------------------

    def _insert_chunks(
        self, conn: sqlite3.Connection, note_rowid: int, note: StoredNote
    ) -> None:
        for chunk in note.chunks:
            cursor = conn.execute(
                _INSERT_CHUNK_META,
                (
                    note_rowid,
                    chunk.path,
                    chunk.chunk_index,
                    chunk.chunk_hash,
                    chunk.start_char,
                    chunk.end_char,
                    chunk.heading_path,
                    chunk.text,
                    chunk.search_text,
                ),
            )
            chunk_rowid = cursor.lastrowid
            conn.execute(
                _INSERT_CHUNK_FTS,
                (
                    chunk_rowid,
                    chunk.path,
                    chunk.title,
                    chunk.heading_path,
                    chunk.frontmatter_json,
                    chunk.tags_text,
                    chunk.text,
                ),
            )

    def _delete_chunks_for_note(
        self, conn: sqlite3.Connection, note_rowid: int
    ) -> None:
        rows = conn.execute(_SELECT_CHUNK_ROWIDS_BY_NOTE, (note_rowid,)).fetchall()
        for row in rows:
            chunk_rowid = row["rowid"]
            conn.execute(_DELETE_CHUNK_FTS, (chunk_rowid,))
            self._delete_vec_if_present(conn, chunk_rowid)
            conn.execute(_DELETE_CHUNK_META, (chunk_rowid,))

    def _delete_vec_if_present(self, conn: sqlite3.Connection, rowid: int) -> None:
        if not self._vec_table_exists(conn):
            return
        conn.execute(_DELETE_VEC, (rowid,))

    def _vec_table_exists(self, conn: sqlite3.Connection | None = None) -> bool:
        if conn is None:
            with self.connect() as inner:
                return self._vec_table_exists(inner)
        row = conn.execute(_SELECT_VEC_TABLE_EXISTS).fetchone()
        return row is not None

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with closing(self._open_connection()) as conn:
            with conn:
                yield conn

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        _load_vec_extension(conn)
        return conn


def _serialize(vector: list[float]) -> bytes:
    """sqlite-vec accepts float32 little-endian byte sequences."""
    return struct.pack(f"<{len(vector)}f", *vector)


def _load_vec_extension(conn: sqlite3.Connection) -> None:
    if not hasattr(conn, "enable_load_extension"):
        raise RuntimeError(
            "Your Python's sqlite3 module was built without extension loading. "
            "On macOS install Python via Homebrew (`brew install python`); on "
            "Linux ensure libsqlite3-dev is present and Python is built with "
            "--enable-loadable-sqlite-extensions."
        )
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)
