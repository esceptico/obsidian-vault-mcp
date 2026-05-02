import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from obsidian_mcp.core.config import EmbeddingSettings
from obsidian_mcp.core.constants import (
    EMBEDDING_TIMEOUT_SECONDS,
    MAX_SEARCH_LIMIT,
    OPENAI_MAX_RETRIES,
    RRF_CANDIDATE_MULTIPLIER,
    RRF_K,
    SCORE_DECIMALS,
)
from obsidian_mcp.core.types import HitSource, SearchMode
from obsidian_mcp.core.logging import get_logger
from obsidian_mcp.index.chunking import TextChunk, chunk_markdown
from obsidian_mcp.index.store import FtsHit, PendingChunk, SearchStore, StoredChunk, StoredNote, VectorHit
from obsidian_mcp.markdown.frontmatter import frontmatter_tags, split_frontmatter


log = get_logger("search")
_VECTOR_DISABLED_WARNING = "Vector search is disabled; set OBSIDIAN_MCP_OPENAI_API_KEY to enable embeddings."
_HYBRID_FTS_ONLY_WARNING = "Hybrid search returned SQLite FTS5 results only."
_EMBEDDING_NOTE_WARNING = "embedding failed for %s (%s: %s); note remains FTS-indexed"
_EMBEDDING_BACKFILL_WARNING = "embedding backfill failed (%s: %s); notes remain FTS-indexed"

SearchHit = dict[str, Any]


@dataclass(frozen=True)
class IndexedNote:
    path: str
    content: str


@dataclass(frozen=True)
class SearchResult:
    hits: Sequence[SearchHit] = ()
    warnings: Sequence[str] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "hits", tuple(self.hits))
        object.__setattr__(self, "warnings", tuple(self.warnings))

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": list(self.hits),
            "warnings": list(self.warnings),
        }


class SearchIndex:
    def __init__(self, database_path: Path, embeddings: EmbeddingSettings):
        self.embeddings = embeddings
        self.store = SearchStore(database_path)
        self._openai_client: OpenAI | None = None

    # --- mutations ----------------------------------------------------------

    def upsert_note(self, note: IndexedNote, *, embed: bool = True) -> None:
        """Index a note. If `embed=False`, the embedding is deferred — the
        caller is expected to flush via embed_pending() after batching many
        upserts (so we make one OpenAI call instead of one per file)."""
        record = _stored_note(note)
        self.store.upsert_note(record)
        if embed and self.embeddings.enabled:
            try:
                self._embed_and_store(self._pending_embedding_chunks())
            except Exception as exc:
                log.warning(_EMBEDDING_NOTE_WARNING, note.path, type(exc).__name__, exc)

    def delete_note(self, path: str) -> None:
        self.store.delete_note(path)

    def content_hash_for(self, note: IndexedNote) -> str:
        return _stored_note(note).content_hash

    def embed_pending(self) -> int:
        """Embed any indexed records that are missing or stale embeddings.
        Returns the number of records embedded. No-op if embeddings disabled.
        Used by startup sync to backfill after a batch of upserts."""
        if not self.embeddings.enabled:
            return 0
        pending = self._pending_embedding_chunks()
        if not pending:
            return 0
        try:
            return self._embed_and_store(pending)
        except Exception as exc:
            log.warning(_EMBEDDING_BACKFILL_WARNING, type(exc).__name__, exc)
            return 0

    # --- search -------------------------------------------------------------

    def search(self, query: str, limit: int, mode: SearchMode) -> SearchResult:
        mode = SearchMode(mode)
        if limit < 1 or limit > MAX_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        if not query.strip():
            return SearchResult(hits=[])
        if mode == SearchMode.BM25:
            return self._bm25_only(query, limit)
        if mode == SearchMode.VECTOR:
            return self._vector_only(query, limit)
        return self._hybrid(query, limit)

    def _bm25_only(self, query: str, limit: int) -> SearchResult:
        return SearchResult(hits=self._search_fts(query, limit))

    def _vector_only(self, query: str, limit: int) -> SearchResult:
        if not self.embeddings.enabled:
            return SearchResult(hits=[], warnings=(_VECTOR_DISABLED_WARNING,))
        return SearchResult(hits=self._search_vectors(query, limit))

    def _hybrid(self, query: str, limit: int) -> SearchResult:
        candidate_limit = _candidate_limit(limit)
        fts_hits = self._search_fts(query, candidate_limit)
        if not self.embeddings.enabled:
            return SearchResult(hits=fts_hits[:limit], warnings=(_VECTOR_DISABLED_WARNING,))
        vector_hits = self._search_vectors(query, candidate_limit)
        if not vector_hits:
            return SearchResult(hits=fts_hits[:limit], warnings=(_HYBRID_FTS_ONLY_WARNING,))
        return SearchResult(hits=_fuse_hits(fts_hits, vector_hits, limit))

    def _search_fts(self, query: str, limit: int) -> list[SearchHit]:
        fts_query = _make_fts_query(query)
        return [_fts_hit_to_dict(hit) for hit in self.store.search_fts(fts_query, limit)]

    def _search_vectors(self, query: str, limit: int) -> list[SearchHit]:
        query_vector = self._embed_texts([query])[0]
        dim = len(query_vector)
        hits = self.store.search_vectors(query_vector, limit, self.embeddings.model, dim)
        return [_vector_hit_to_dict(hit) for hit in hits]

    # --- embedding helpers --------------------------------------------------

    def _pending_embedding_chunks(self) -> list[PendingChunk]:
        return self.store.pending_embedding_chunks(self.embeddings.model, self.embeddings.dimensions)

    def _embed_and_store(self, items: list[PendingChunk]) -> int:
        """Embed chunks and write into vec_chunks + chunk_meta."""
        total = 0
        for batch_start in range(0, len(items), self.embeddings.batch_size):
            batch = items[batch_start : batch_start + self.embeddings.batch_size]
            inputs = [chunk.search_text for chunk in batch]
            vectors = self._embed_texts(inputs)
            dim = len(vectors[0]) if vectors else 0
            self.store.upsert_embeddings(
                ((chunk.rowid, chunk.chunk_hash, vector) for chunk, vector in zip(batch, vectors, strict=True)),
                self.embeddings.model,
                dim,
            )
            total += len(batch)
        return total

    def _client(self) -> OpenAI:
        if self._openai_client is None:
            self._openai_client = OpenAI(
                api_key=self.embeddings.api_key,
                base_url=self.embeddings.base_url,
                max_retries=OPENAI_MAX_RETRIES,
                timeout=EMBEDDING_TIMEOUT_SECONDS,
            )
        return self._openai_client

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        request = self._embedding_request(texts)
        response = self._client().embeddings.create(**request)
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    def _embedding_request(self, texts: list[str]) -> dict:
        request: dict = {
            "model": self.embeddings.model,
            "input": texts,
            "encoding_format": "float",
        }
        if self.embeddings.dimensions is not None:
            request["dimensions"] = self.embeddings.dimensions
        return request


def _stored_note(note: IndexedNote) -> StoredNote:
    frontmatter, body = split_frontmatter(note.content)
    body_start = len(note.content) - len(body)
    title = str(frontmatter.get("title") or Path(note.path).stem)
    frontmatter_json = json.dumps(frontmatter, ensure_ascii=False, sort_keys=True)
    tags_text = " ".join(frontmatter_tags(frontmatter))
    search_text = f"{title}\n{frontmatter_json}\n{tags_text}\n{body}"
    chunks = tuple(
        _stored_chunk(
            path=note.path,
            title=title,
            frontmatter_json=frontmatter_json,
            tags_text=tags_text,
            chunk=chunk,
        )
        for chunk in chunk_markdown(body, body_start=body_start)
    )
    return StoredNote(
        path=note.path,
        title=title,
        frontmatter_json=frontmatter_json,
        body=body,
        tags_text=tags_text,
        search_text=search_text,
        content_hash=hashlib.sha256(note.content.encode("utf-8")).hexdigest(),
        chunks=chunks,
    )


def _stored_chunk(
    *,
    path: str,
    title: str,
    frontmatter_json: str,
    tags_text: str,
    chunk: TextChunk,
) -> StoredChunk:
    search_text = _chunk_search_text(path, title, frontmatter_json, tags_text, chunk.heading_path, chunk.text)
    return StoredChunk(
        path=path,
        title=title,
        frontmatter_json=frontmatter_json,
        tags_text=tags_text,
        chunk_index=chunk.chunk_index,
        chunk_hash=hashlib.sha256(search_text.encode("utf-8")).hexdigest(),
        heading_path=chunk.heading_path,
        text=chunk.text,
        search_text=search_text,
        start_char=chunk.start_char,
        end_char=chunk.end_char,
    )


def _chunk_search_text(path: str, title: str, frontmatter_json: str, tags_text: str, heading_path: str, text: str) -> str:
    metadata = [f"Path: {path}", f"Title: {title}"]

    if heading_path:
        metadata.append(f"Heading: {heading_path}")
    if tags_text:
        metadata.append(f"Tags: {tags_text}")
    if frontmatter_json != "{}":
        metadata.append(f"Frontmatter: {frontmatter_json}")

    return "\n".join(metadata + ["", text])


def _make_fts_query(query: str) -> str:
    tokens = [token.replace('"', '""') for token in query.split() if token.strip()]
    return " ".join(f'"{token}"' for token in tokens)


def _candidate_limit(limit: int) -> int:
    return min(MAX_SEARCH_LIMIT, max(limit, limit * RRF_CANDIDATE_MULTIPLIER))


def _fts_hit_to_dict(hit: FtsHit) -> SearchHit:
    return {
        "chunk_id": hit.chunk_id,
        "path": hit.path,
        "score": round(hit.score, SCORE_DECIMALS),
        "title": hit.title,
        "heading": hit.heading_path,
        "snippet": hit.snippet,
        "chunk_index": hit.chunk_index,
        "start_char": hit.start_char,
        "end_char": hit.end_char,
        "source": HitSource.FTS.value,
    }


def _vector_hit_to_dict(hit: VectorHit) -> SearchHit:
    # cosine distance ∈ [0, 2]; flip to a similarity score ∈ [-1, 1] so
    # higher is better and the result shape matches FTS hits semantically.
    return {
        "chunk_id": hit.chunk_id,
        "path": hit.path,
        "score": round(1.0 - hit.distance, SCORE_DECIMALS),
        "title": hit.title,
        "heading": hit.heading_path,
        "snippet": hit.snippet,
        "chunk_index": hit.chunk_index,
        "start_char": hit.start_char,
        "end_char": hit.end_char,
        "source": HitSource.VECTOR.value,
    }


def _fuse_hits(
    fts_hits: list[SearchHit],
    vector_hits: list[SearchHit],
    limit: int,
) -> list[SearchHit]:
    by_chunk_id: dict[int, SearchHit] = {}
    scores: dict[int, float] = {}
    for hits in (fts_hits, vector_hits):
        for rank, hit in enumerate(hits, start=1):
            chunk_id = hit["chunk_id"]
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (RRF_K + rank)
            by_chunk_id.setdefault(chunk_id, hit.copy())

    fused = []
    for chunk_id, score in scores.items():
        hit = by_chunk_id[chunk_id]
        hit["score"] = round(score, SCORE_DECIMALS)
        hit["source"] = HitSource.HYBRID.value
        fused.append(hit)
    fused.sort(key=lambda hit: hit["score"], reverse=True)
    return fused[:limit]
