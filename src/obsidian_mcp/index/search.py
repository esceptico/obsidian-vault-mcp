import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import tiktoken
from openai import OpenAI

from obsidian_mcp.core.config import EmbeddingSettings
from obsidian_mcp.core.constants import (
    EMBEDDING_FALLBACK_ENCODING,
    EMBEDDING_MAX_INPUT_TOKENS,
    EMBEDDING_TIMEOUT_SECONDS,
    MAX_SEARCH_LIMIT,
    OPENAI_MAX_RETRIES,
    RRF_CANDIDATE_MULTIPLIER,
    RRF_K,
    SCORE_DECIMALS,
)
from obsidian_mcp.core.types import HitSource, SearchMode
from obsidian_mcp.core.logging import get_logger
from obsidian_mcp.index.store import FtsHit, RecordMeta, SearchStore, StoredNote, VectorHit
from obsidian_mcp.markdown.frontmatter import frontmatter_tags, split_frontmatter


log = get_logger("search")


@dataclass(frozen=True)
class IndexedNote:
    path: str
    content: str


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
        rowid = self.store.upsert_note(record)
        if embed and self.embeddings.enabled:
            try:
                self._embed_and_store([(rowid, record)])
            except Exception:
                log.warning("embedding failed for %s; note remains FTS-indexed", note.path)

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
        all_records = self.store.all_records()
        pending: list[tuple[int, RecordMeta]] = []
        for meta in all_records.values():
            if self._meta_needs_embedding(meta):
                pending.append((meta.rowid, meta))
        if not pending:
            return 0
        # We need search_text + content_hash; pull them by re-reading via FTS.
        # Caller-driven embedding (during sync) avoids this round-trip; this
        # method is the safety net.
        records_by_rowid = self._materialize_records(pending)
        items = [(rowid, records_by_rowid[rowid]) for rowid, _ in pending if rowid in records_by_rowid]
        try:
            return self._embed_and_store(items)
        except Exception:
            log.warning("embedding backfill failed; notes remain FTS-indexed")
            return 0

    # --- search -------------------------------------------------------------

    def search(self, query: str, limit: int, mode: SearchMode) -> dict:
        mode = SearchMode(mode)
        if limit < 1 or limit > MAX_SEARCH_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
        if not query.strip():
            return {"hits": [], "warnings": []}
        return _SEARCH_DISPATCH[mode](self, query, limit)

    def _bm25_only(self, query: str, limit: int) -> dict:
        return {"hits": self._search_fts(query, limit), "warnings": []}

    def _vector_only(self, query: str, limit: int) -> dict:
        if not self.embeddings.enabled:
            return {"hits": [], "warnings": [_VECTOR_DISABLED_WARNING]}
        return {"hits": self._search_vectors(query, limit), "warnings": []}

    def _hybrid(self, query: str, limit: int) -> dict:
        candidate_limit = _candidate_limit(limit)
        fts_hits = self._search_fts(query, candidate_limit)
        if not self.embeddings.enabled:
            return {"hits": fts_hits[:limit], "warnings": [_VECTOR_DISABLED_WARNING]}
        vector_hits = self._search_vectors(query, candidate_limit)
        if not vector_hits:
            return {"hits": fts_hits[:limit], "warnings": ["Hybrid search returned SQLite FTS5 results only."]}
        return {"hits": _fuse_hits(fts_hits, vector_hits, limit), "warnings": []}

    def _search_fts(self, query: str, limit: int) -> list[dict]:
        fts_query = _make_fts_query(query)
        return [_fts_hit_to_dict(hit) for hit in self.store.search_fts(fts_query, limit)]

    def _search_vectors(self, query: str, limit: int) -> list[dict]:
        query_vector = self._embed_texts([query])[0]
        dim = len(query_vector)
        hits = self.store.search_vectors(query_vector, limit, self.embeddings.model, dim)
        return [_vector_hit_to_dict(hit) for hit in hits]

    # --- embedding helpers --------------------------------------------------

    def _meta_needs_embedding(self, meta: RecordMeta) -> bool:
        if meta.embedded_hash != meta.content_hash:
            return True
        if meta.embedded_model != self.embeddings.model:
            return True
        if self.embeddings.dimensions is not None and meta.embedded_dimensions != self.embeddings.dimensions:
            return True
        return False

    def _embed_and_store(self, items: list[tuple[int, StoredNote]]) -> int:
        """items: list of (rowid, StoredNote). Embeds in batches and writes
        into vec_notes + note_meta. Returns the number of records embedded."""
        total = 0
        for batch_start in range(0, len(items), self.embeddings.batch_size):
            batch = items[batch_start : batch_start + self.embeddings.batch_size]
            inputs = [_truncate_for_embedding(record.search_text, self.embeddings.model) for _, record in batch]
            vectors = self._embed_texts(inputs)
            dim = len(vectors[0]) if vectors else 0
            self.store.upsert_embeddings(
                ((rowid, record.content_hash, vector) for (rowid, record), vector in zip(batch, vectors, strict=True)),
                self.embeddings.model,
                dim,
            )
            total += len(batch)
        return total

    def _materialize_records(self, pending: list[tuple[int, RecordMeta]]) -> dict[int, StoredNote]:
        """For records flagged as needing embedding by the meta table alone,
        rehydrate the StoredNote from the FTS row so we have search_text."""
        return self.store.records_by_rowid([rowid for rowid, _ in pending])

    def _client(self) -> OpenAI:
        if self._openai_client is None:
            self._openai_client = OpenAI(
                api_key=self.embeddings.api_key,
                max_retries=OPENAI_MAX_RETRIES,
                timeout=EMBEDDING_TIMEOUT_SECONDS,
            )
        return self._openai_client

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        request: dict = {
            "model": self.embeddings.model,
            "input": texts,
            "encoding_format": "float",
        }
        if self.embeddings.dimensions is not None:
            request["dimensions"] = self.embeddings.dimensions
        response = self._client().embeddings.create(**request)
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]


_VECTOR_DISABLED_WARNING = "Vector search is disabled; set OPENAI_API_KEY to enable embeddings."

_SEARCH_DISPATCH = {
    SearchMode.BM25: SearchIndex._bm25_only,
    SearchMode.VECTOR: SearchIndex._vector_only,
    SearchMode.HYBRID: SearchIndex._hybrid,
}


def _stored_note(note: IndexedNote) -> StoredNote:
    frontmatter, body = split_frontmatter(note.content)
    title = str(frontmatter.get("title") or Path(note.path).stem)
    frontmatter_json = json.dumps(frontmatter, ensure_ascii=False, sort_keys=True)
    tags_text = " ".join(frontmatter_tags(frontmatter))
    search_text = f"{title}\n{frontmatter_json}\n{tags_text}\n{body}"
    return StoredNote(
        path=note.path,
        title=title,
        frontmatter_json=frontmatter_json,
        body=body,
        tags_text=tags_text,
        search_text=search_text,
        content_hash=hashlib.sha256(search_text.encode("utf-8")).hexdigest(),
    )


@lru_cache(maxsize=4)
def _tokenizer_for(model: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding(EMBEDDING_FALLBACK_ENCODING)


def _truncate_for_embedding(text: str, model: str) -> str:
    """Truncate to EMBEDDING_MAX_INPUT_TOKENS using the model's actual
    tokenizer. Char-based caps are unsafe because code, URLs, and non-English
    can drop to ~1 char/token — well under what a 24k-char budget assumes."""
    encoder = _tokenizer_for(model)
    tokens = encoder.encode(text)
    if len(tokens) <= EMBEDDING_MAX_INPUT_TOKENS:
        return text
    return encoder.decode(tokens[:EMBEDDING_MAX_INPUT_TOKENS])


def _make_fts_query(query: str) -> str:
    tokens = [token.replace('"', '""') for token in query.split() if token.strip()]
    return " ".join(f'"{token}"' for token in tokens)


def _candidate_limit(limit: int) -> int:
    return min(MAX_SEARCH_LIMIT, max(limit, limit * RRF_CANDIDATE_MULTIPLIER))


def _fts_hit_to_dict(hit: FtsHit) -> dict:
    return {
        "path": hit.path,
        "score": round(hit.score, SCORE_DECIMALS),
        "title": hit.title,
        "snippet": hit.snippet,
        "source": HitSource.FTS.value,
    }


def _vector_hit_to_dict(hit: VectorHit) -> dict:
    # cosine distance ∈ [0, 2]; flip to a similarity score ∈ [-1, 1] so
    # higher is better and the result shape matches FTS hits semantically.
    return {
        "path": hit.path,
        "score": round(1.0 - hit.distance, SCORE_DECIMALS),
        "title": hit.title,
        "snippet": hit.snippet,
        "source": HitSource.VECTOR.value,
    }


def _fuse_hits(fts_hits: list[dict], vector_hits: list[dict], limit: int) -> list[dict]:
    by_path: dict[str, dict] = {}
    scores: dict[str, float] = {}
    for hits in (fts_hits, vector_hits):
        for rank, hit in enumerate(hits, start=1):
            path = hit["path"]
            scores[path] = scores.get(path, 0.0) + 1 / (RRF_K + rank)
            by_path.setdefault(path, hit.copy())

    fused = []
    for path, score in scores.items():
        hit = by_path[path]
        hit["score"] = round(score, SCORE_DECIMALS)
        hit["source"] = HitSource.HYBRID.value
        fused.append(hit)
    fused.sort(key=lambda hit: hit["score"], reverse=True)
    return fused[:limit]
