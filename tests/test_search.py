import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from obsidian_mcp.core.config import EmbeddingSettings
from obsidian_mcp.core.types import SearchMode
from obsidian_mcp.index.search import IndexedNote, SearchIndex

DEFAULT_LIMIT = 10


class SearchTests(unittest.TestCase):
    def test_vector_and_hybrid_search_use_embeddings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index = SearchIndex(
                Path(tmp) / "index.sqlite",
                EmbeddingSettings(api_key="test-key", model="text-embedding-3-small", batch_size=2),
            )

            def embed(texts: list[str]) -> list[list[float]]:
                vectors = []
                for text in texts:
                    if "semantic" in text:
                        vectors.append([1.0, 0.0])
                    elif "recipe" in text:
                        vectors.append([0.0, 1.0])
                    else:
                        vectors.append([0.8, 0.2])
                return vectors

            with patch.object(index, "_embed_texts", side_effect=embed):
                index.upsert_note(IndexedNote(path="AI.md", content="semantic vector search"))
                index.upsert_note(IndexedNote(path="Food.md", content="recipe notes"))
                vector = index.search("semantic question", limit=DEFAULT_LIMIT, mode=SearchMode.VECTOR)
                hybrid = index.search("semantic question", limit=DEFAULT_LIMIT, mode=SearchMode.HYBRID)

        self.assertEqual(vector["hits"][0]["path"], "AI.md")
        self.assertEqual(vector["hits"][0]["source"], "vector")
        self.assertEqual(hybrid["hits"][0]["path"], "AI.md")
        self.assertEqual(hybrid["hits"][0]["source"], "hybrid")

    def test_openai_client_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            idx = SearchIndex(
                Path(tmp) / "i.sqlite",
                EmbeddingSettings(api_key="k", model="text-embedding-3-small"),
            )
            self.assertIs(idx._client(), idx._client())

    def test_openai_client_uses_configured_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            idx = SearchIndex(
                Path(tmp) / "i.sqlite",
                EmbeddingSettings(
                    api_key="k",
                    base_url="https://openrouter.ai/api/v1",
                    model="text-embedding-3-small",
                ),
            )
            with patch("obsidian_mcp.index.search.OpenAI") as openai:
                self.assertIs(idx._client(), openai.return_value)

        openai.assert_called_once()
        self.assertEqual(openai.call_args.kwargs["base_url"], "https://openrouter.ai/api/v1")

    def test_upsert_does_not_full_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index = SearchIndex(Path(tmp) / "i.sqlite", EmbeddingSettings())
            index.upsert_note(IndexedNote(path="A.md", content="alpha"))
            index.upsert_note(IndexedNote(path="B.md", content="beta"))
            beta_hits = [hit["path"] for hit in index.search("beta", limit=DEFAULT_LIMIT, mode=SearchMode.BM25)["hits"]]
            alpha_hits = [hit["path"] for hit in index.search("alpha", limit=DEFAULT_LIMIT, mode=SearchMode.BM25)["hits"]]
            self.assertIn("B.md", beta_hits)
            self.assertIn("A.md", alpha_hits)

    def test_embed_pending_backfills_missing_embeddings(self) -> None:
        """A note indexed while embeddings were disabled (or before they were
        configured) should get embedded by a later embed_pending() call."""
        with tempfile.TemporaryDirectory() as tmp:
            no_key = EmbeddingSettings()
            with_key = EmbeddingSettings(api_key="k", model="text-embedding-3-small", batch_size=4)

            index = SearchIndex(Path(tmp) / "i.sqlite", no_key)
            index.upsert_note(IndexedNote(path="A.md", content="hello"))

            # Re-open with embeddings enabled.
            index = SearchIndex(Path(tmp) / "i.sqlite", with_key)
            with patch.object(index, "_embed_texts", return_value=[[1.0, 0.0]]):
                count = index.embed_pending()
            self.assertEqual(count, 1)

    def test_embed_pending_rebuilds_when_dimensions_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "i.sqlite"
            index = SearchIndex(
                database,
                EmbeddingSettings(api_key="k", model="text-embedding-3-small", dimensions=2),
            )
            with patch.object(index, "_embed_texts", return_value=[[1.0, 0.0]]):
                index.upsert_note(IndexedNote(path="A.md", content="semantic note"))

            index = SearchIndex(
                database,
                EmbeddingSettings(api_key="k", model="text-embedding-3-small", dimensions=3),
            )
            with patch.object(index, "_embed_texts", side_effect=[[[1.0, 0.0, 0.0]], [[1.0, 0.0, 0.0]]]):
                self.assertEqual(index.embed_pending(), 1)
                hits = index.search("semantic", limit=DEFAULT_LIMIT, mode=SearchMode.VECTOR)["hits"]

            self.assertEqual(hits[0]["path"], "A.md")

    def test_invalid_search_limit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index = SearchIndex(Path(tmp) / "i.sqlite", EmbeddingSettings())
            index.upsert_note(IndexedNote(path="A.md", content="alpha"))
            with self.assertRaises(ValueError):
                index.search("alpha", limit=-1, mode=SearchMode.BM25)


if __name__ == "__main__":
    unittest.main()
