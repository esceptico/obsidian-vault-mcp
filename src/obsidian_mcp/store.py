import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import sqlite_vec

from obsidian_mcp.constants import FTS_SNIPPET_LENGTH

# --- FTS5 snippet config ----------------------------------------------------
_SNIPPET_TARGET_COLUMN = 3  # 0-indexed: path, title, frontmatter, body
_SNIPPET_OPEN = "["
_SNIPPET_CLOSE = "]"
_SNIPPET_ELLIPSIS = " ... "

# --- Schema -----------------------------------------------------------------
_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
)

# Source of truth for note identity. rowid is stable per path and shared with
# the FTS5 `notes` table and the sqlite-vec `vec_notes` table.
_CREATE_NOTE_META = """
CREATE TABLE IF NOT EXISTS note_meta(
    rowid               INTEGER PRIMARY KEY AUTOINCREMENT,
    path                TEXT    NOT NULL UNIQUE,
    content_hash        TEXT    NOT NULL,
    embedded_hash       TEXT,
    embedded_model      TEXT,
    embedded_dimensions INTEGER
)
"""

# FTS5 index. We control the rowid via INSERT INTO notes(rowid, ...).
_CREATE_NOTES_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes USING fts5(
    path UNINDEXED,
    title,
    frontmatter,
    body,
    tags,
    tokenize='unicode61'
)
"""

# Per-database key/value bag (currently just the active vec_notes dimension).
_CREATE_INDEX_META = """
CREATE TABLE IF NOT EXISTS index_meta(
    key   TEXT PRIMARY KEY,
    value TEXT
)
"""

# vec0 virtual table is created lazily once we know the embedding dimension
# (either from settings or from the first embedding call). See _ensure_vec_table.

_INSERT_NOTE_FTS = """
INSERT INTO notes(rowid, path, title, frontmatter, body, tags)
VALUES (?, ?, ?, ?, ?, ?)
"""

_DELETE_NOTE_FTS = "DELETE FROM notes WHERE rowid = ?"

_INSERT_NOTE_META = """
INSERT INTO note_meta(path, content_hash) VALUES (?, ?)
"""

_UPDATE_NOTE_META_HASH = """
UPDATE note_meta SET content_hash = ?, embedded_hash = NULL,
                     embedded_model = NULL, embedded_dimensions = NULL
WHERE rowid = ?
"""

_DELETE_NOTE_META = "DELETE FROM note_meta WHERE rowid = ?"

_SELECT_META_BY_PATH = """
SELECT rowid, content_hash, embedded_hash, embedded_model, embedded_dimensions
FROM note_meta WHERE path = ?
"""

_SELECT_ALL_META = """
SELECT rowid, path, content_hash, embedded_hash, embedded_model, embedded_dimensions
FROM note_meta
"""

_COUNT_META = "SELECT COUNT(*) FROM note_meta"

_SNIPPET_EXPR = (
    f"snippet(notes, {_SNIPPET_TARGET_COLUMN}, "
    f"'{_SNIPPET_OPEN}', '{_SNIPPET_CLOSE}', '{_SNIPPET_ELLIPSIS}', {FTS_SNIPPET_LENGTH})"
)

_SEARCH_FTS = f"""
SELECT
    notes.path AS path,
    notes.title AS title,
    bm25(notes) AS score,
    {_SNIPPET_EXPR} AS snippet
FROM notes
WHERE notes MATCH ?
ORDER BY score
LIMIT ?
"""

# Body excerpt for vector hits — we don't have an FTS MATCH context here, so
# snippet() can't highlight; return a plain prefix instead.
_SEARCH_VECTORS = """
WITH knn AS (
    SELECT rowid, distance
    FROM vec_notes
    WHERE embedding MATCH ? AND k = ?
)
SELECT
    meta.path AS path,
    knn.distance AS distance,
    notes.title AS title,
    substr(notes.body, 1, ?) AS snippet
FROM knn
JOIN note_meta meta ON meta.rowid = knn.rowid
JOIN notes ON notes.rowid = knn.rowid
WHERE meta.embedded_model = ?
  AND (meta.embedded_dimensions IS ? OR meta.embedded_dimensions = ?)
ORDER BY knn.distance
"""

_INSERT_VEC = "INSERT INTO vec_notes(rowid, embedding) VALUES (?, ?)"
_DELETE_VEC = "DELETE FROM vec_notes WHERE rowid = ?"

_VECTOR_SNIPPET_CHARS = 240  # chars of body shown alongside vector hits


# --- Public types -----------------------------------------------------------


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


@dataclass(frozen=True)
class FtsHit:
    path: str
    score: float
    title: str
    snippet: str


@dataclass(frozen=True)
class VectorHit:
    path: str
    distance: float  # cosine distance: 0 == identical, 2 == opposite
    title: str
    snippet: str


@dataclass(frozen=True)
class RecordMeta:
    """Snapshot of what the index knows about a path. Used for sync diffs."""
    rowid: int
    path: str
    content_hash: str
    embedded_hash: str | None
    embedded_model: str | None
    embedded_dimensions: int | None


# --- Store ------------------------------------------------------------------


class SearchStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    # ----- schema lifecycle -------------------------------------------------

    def _initialize_schema(self) -> None:
        with self.connect() as conn:
            for pragma in _PRAGMAS:
                conn.execute(pragma)
            try:
                conn.execute(_CREATE_NOTES_FTS)
            except sqlite3.OperationalError as exc:
                raise RuntimeError(
                    "This Python SQLite build does not include FTS5 support"
                ) from exc
            conn.execute(_CREATE_NOTE_META)
            conn.execute(_CREATE_INDEX_META)

    def _ensure_vec_table(self, conn: sqlite3.Connection, dimensions: int) -> None:
        """Create or recreate the vec0 table for the given dimension. If a
        previous run used a different dimension, drop the table and clear
        every note's embedded_* fields so the next embed pass repopulates."""
        stored = conn.execute(
            "SELECT value FROM index_meta WHERE key = 'vec_dim'"
        ).fetchone()
        if stored is None:
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_notes USING vec0("
                f"embedding float[{dimensions}] distance_metric=cosine)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO index_meta(key, value) VALUES ('vec_dim', ?)",
                (str(dimensions),),
            )
            return
        if int(stored["value"]) == dimensions:
            return
        # Dim changed: drop, recreate, invalidate all existing embeddings.
        conn.execute("DROP TABLE IF EXISTS vec_notes")
        conn.execute(
            f"CREATE VIRTUAL TABLE vec_notes USING vec0("
            f"embedding float[{dimensions}] distance_metric=cosine)"
        )
        conn.execute(
            "UPDATE note_meta SET embedded_hash = NULL, embedded_model = NULL, "
            "embedded_dimensions = NULL"
        )
        conn.execute(
            "INSERT OR REPLACE INTO index_meta(key, value) VALUES ('vec_dim', ?)",
            (str(dimensions),),
        )

    # ----- note CRUD --------------------------------------------------------

    def upsert_note(self, note: StoredNote) -> int:
        """Insert or update a note. Returns the stable rowid.
        If the content_hash changed, drops any existing embedding for this row
        (the next embed pass will repopulate)."""
        with self.connect() as conn:
            existing = conn.execute(_SELECT_META_BY_PATH, (note.path,)).fetchone()
            if existing is None:
                cursor = conn.execute(_INSERT_NOTE_META, (note.path, note.content_hash))
                rowid = cursor.lastrowid
                conn.execute(
                    _INSERT_NOTE_FTS,
                    (rowid, note.path, note.title, note.frontmatter_json, note.body, note.tags_text),
                )
                return rowid
            rowid = existing["rowid"]
            if existing["content_hash"] == note.content_hash:
                return rowid
            conn.execute(_UPDATE_NOTE_META_HASH, (note.content_hash, rowid))
            conn.execute(_DELETE_NOTE_FTS, (rowid,))
            conn.execute(
                _INSERT_NOTE_FTS,
                (rowid, note.path, note.title, note.frontmatter_json, note.body, note.tags_text),
            )
            self._delete_vec_if_present(conn, rowid)
            return rowid

    def delete_note(self, path: str) -> None:
        with self.connect() as conn:
            row = conn.execute(_SELECT_META_BY_PATH, (path,)).fetchone()
            if row is None:
                return
            rowid = row["rowid"]
            conn.execute(_DELETE_NOTE_FTS, (rowid,))
            self._delete_vec_if_present(conn, rowid)
            conn.execute(_DELETE_NOTE_META, (rowid,))

    def all_records(self) -> dict[str, RecordMeta]:
        """Return path → RecordMeta for every indexed note. Used by the
        startup sync to compute add/modify/delete diffs against disk."""
        with self.connect() as conn:
            rows = conn.execute(_SELECT_ALL_META).fetchall()
        return {
            row["path"]: RecordMeta(
                rowid=row["rowid"],
                path=row["path"],
                content_hash=row["content_hash"],
                embedded_hash=row["embedded_hash"],
                embedded_model=row["embedded_model"],
                embedded_dimensions=row["embedded_dimensions"],
            )
            for row in rows
        }

    def count_notes(self) -> int:
        with self.connect() as conn:
            return conn.execute(_COUNT_META).fetchone()[0]

    # ----- search -----------------------------------------------------------

    def search_fts(self, query: str, limit: int) -> list[FtsHit]:
        with self.connect() as conn:
            rows = conn.execute(_SEARCH_FTS, (query, limit)).fetchall()
        return [
            FtsHit(
                path=row["path"],
                score=float(row["score"]),
                title=row["title"],
                snippet=row["snippet"],
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
                (query_blob, limit, _VECTOR_SNIPPET_CHARS, model, dimensions, dimensions),
            ).fetchall()
        return [
            VectorHit(
                path=row["path"],
                distance=float(row["distance"]),
                title=row["title"],
                snippet=row["snippet"],
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
        """items: iterable of (rowid, content_hash, vector). All vectors must
        have length == dimensions. Creates vec_notes on first call (or
        recreates if the stored dim differs)."""
        materialized = list(items)
        if not materialized:
            return
        with self.connect() as conn:
            self._ensure_vec_table(conn, dimensions)
            for rowid, content_hash, vector in materialized:
                if len(vector) != dimensions:
                    raise ValueError(
                        f"vector for rowid={rowid} has dim {len(vector)}, expected {dimensions}"
                    )
                self._delete_vec_if_present(conn, rowid)
                conn.execute(_INSERT_VEC, (rowid, _serialize(vector)))
                conn.execute(
                    "UPDATE note_meta SET embedded_hash = ?, embedded_model = ?, "
                    "embedded_dimensions = ? WHERE rowid = ?",
                    (content_hash, model, dimensions, rowid),
                )

    # ----- helpers ----------------------------------------------------------

    def _delete_vec_if_present(self, conn: sqlite3.Connection, rowid: int) -> None:
        if not self._vec_table_exists(conn):
            return
        conn.execute(_DELETE_VEC, (rowid,))

    def _vec_table_exists(self, conn: sqlite3.Connection | None = None) -> bool:
        if conn is None:
            with self.connect() as inner:
                return self._vec_table_exists(inner)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vec_notes'"
        ).fetchone()
        return row is not None

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        _load_vec_extension(conn)
        return conn


def _serialize(vector: list[float]) -> bytes:
    """sqlite-vec accepts float32 little-endian byte sequences."""
    return struct.pack(f"{len(vector)}f", *vector)


def _load_vec_extension(conn: sqlite3.Connection) -> None:
    if not hasattr(conn, "enable_load_extension"):
        raise RuntimeError(
            "Your Python's sqlite3 module was built without extension loading. "
            "On macOS install Python via Homebrew (`brew install python`); on "
            "Linux ensure libsqlite3-dev is present and Python is built with "
            "--enable-loadable-sqlite-extensions."
        )
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
