import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from obsidian_mcp.config import EmbeddingSettings
from obsidian_mcp.search import IndexedNote, SearchIndex


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
                index.rebuild(
                    [
                        IndexedNote(path="AI.md", content="semantic vector search"),
                        IndexedNote(path="Food.md", content="recipe notes"),
                    ]
                )
                vector = index.search("semantic question", mode="vector")
                hybrid = index.search("semantic question", mode="hybrid")

        self.assertEqual(vector["hits"][0]["path"], "AI.md")
        self.assertEqual(vector["hits"][0]["source"], "vector")
        self.assertEqual(hybrid["hits"][0]["path"], "AI.md")
        self.assertEqual(hybrid["hits"][0]["source"], "hybrid")


if __name__ == "__main__":
    unittest.main()
