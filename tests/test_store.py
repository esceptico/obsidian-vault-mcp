import tempfile
import unittest
from pathlib import Path

from obsidian_mcp.store import SearchStore, StoredNote


class StoreTests(unittest.TestCase):
    def test_replace_notes_and_search_fts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SearchStore(Path(tmp) / "index.sqlite")
            store.replace_notes(
                [
                    StoredNote(
                        path="Alpha.md",
                        title="Alpha",
                        frontmatter_json="{}",
                        body="semantic search notes",
                        tags_text="ai",
                        search_text="Alpha semantic search notes",
                        content_hash="hash-1",
                    )
                ]
            )

            hits = store.search_fts('"semantic"', 10)

        self.assertEqual(hits[0].path, "Alpha.md")
        self.assertEqual(hits[0].title, "Alpha")


if __name__ == "__main__":
    unittest.main()
