import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from openai import OpenAI

from obsidian_mcp.config import EmbeddingSettings
from obsidian_mcp.constants import (
    EMBEDDING_TIMEOUT_SECONDS,
    OPENAI_MAX_RETRIES,
    RRF_K,
    SCORE_DECIMALS,
)
from obsidian_mcp.frontmatter import split_frontmatter
from obsidian_mcp.store import FtsHit, SearchStore, StoredNote
from obsidian_mcp.types import HitSource, SearchMode


@dataclass(frozen=True)
class IndexedNote:
    path: str
    content: str


class SearchIndex:
    def __init__(self, database_path: Path, embeddings: EmbeddingSettings):
        self.embeddings = embeddings
        self.store = SearchStore(database_path)
        self._openai_client: OpenAI | None = None

    def _client(self) -> OpenAI:
        if self._openai_client is None:
            self._openai_client = OpenAI(
                api_key=self.embeddings.api_key,
                max_retries=OPENAI_MAX_RETRIES,
                timeout=EMBEDDING_TIMEOUT_SECONDS,
            )
        return self._openai_client

    def rebuild(self, notes: list[IndexedNote]) -> None:
        records = [_stored_note(note) for note in notes]
        self.store.replace_notes(records)
        if self.embeddings.enabled:
            self._embed_missing(records)

    def upsert_note(self, note: IndexedNote) -> None:
        record = _stored_note(note)
        self.store.upsert_note(record)
        if self.embeddings.enabled:
            self._embed_missing([record])

    def delete_note(self, path: str) -> None:
        self.store.delete_note(path)

    def search(self, query: str, limit: int, mode: SearchMode) -> dict:
        mode = SearchMode(mode)
        if not query.strip():
            return {"hits": [], "warnings": []}
        return _SEARCH_DISPATCH[mode](self, query, limit)

    def _search_fts(self, query: str, limit: int) -> list[dict]:
        fts_query = _make_fts_query(query)
        return [_fts_hit_to_dict(hit) for hit in self.store.search_fts(fts_query, limit)]

    def _search_vectors(self, query: str, limit: int) -> list[dict]:
        query_vector = self._embed_texts([query])[0]
        ranked = [
            {
                "path": row.path,
                "score": round(_dot(query_vector, row.vector), SCORE_DECIMALS),
                "title": row.title,
                "snippet": row.snippet,
                "source": HitSource.VECTOR.value,
            }
            for row in self.store.stored_embeddings(self.embeddings.model, self.embeddings.dimensions)
        ]
        ranked.sort(key=lambda hit: hit["score"], reverse=True)
        return ranked[:limit]

    def _bm25_only(self, query: str, limit: int) -> dict:
        return {"hits": self._search_fts(query, limit), "warnings": []}

    def _vector_only(self, query: str, limit: int) -> dict:
        if not self.embeddings.enabled:
            return {"hits": [], "warnings": [_VECTOR_DISABLED_WARNING]}
        return {"hits": self._search_vectors(query, limit), "warnings": []}

    def _hybrid(self, query: str, limit: int) -> dict:
        fts_hits = self._search_fts(query, limit)
        if not self.embeddings.enabled:
            return {"hits": fts_hits, "warnings": [_VECTOR_DISABLED_WARNING]}
        vector_hits = self._search_vectors(query, limit)
        if not vector_hits:
            return {"hits": fts_hits, "warnings": ["Hybrid search returned SQLite FTS5 results only."]}
        return {"hits": _fuse_hits(fts_hits, vector_hits, limit), "warnings": []}

    def _embed_missing(self, records: list[StoredNote]) -> None:
        existing = self.store.embedding_metadata(self.embeddings.model, self.embeddings.dimensions)
        missing = [
            record
            for record in records
            if record.path not in existing or existing[record.path].content_hash != record.content_hash
        ]
        for batch_start in range(0, len(missing), self.embeddings.batch_size):
            batch = missing[batch_start : batch_start + self.embeddings.batch_size]
            vectors = self._embed_texts([record.search_text for record in batch])
            self.store.upsert_embeddings(batch, vectors, self.embeddings.model, self.embeddings.dimensions)

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
    tags_text = " ".join(_frontmatter_tags(frontmatter))
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


def _make_fts_query(query: str) -> str:
    tokens = [token.replace('"', '""') for token in query.split() if token.strip()]
    return " ".join(f'"{token}"' for token in tokens)


def _frontmatter_tags(frontmatter: dict) -> list[str]:
    tags = frontmatter.get("tags", [])
    if isinstance(tags, str):
        return [tags.lstrip("#")]
    if isinstance(tags, list):
        return [str(tag).lstrip("#") for tag in tags]
    return []


def _fts_hit_to_dict(hit: FtsHit) -> dict:
    return {
        "path": hit.path,
        "score": round(hit.score, SCORE_DECIMALS),
        "title": hit.title,
        "snippet": hit.snippet,
        "source": HitSource.FTS.value,
    }


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


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
