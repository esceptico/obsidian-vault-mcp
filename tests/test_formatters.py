import unittest
from datetime import datetime, timezone

from headless_obsidian_mcp.core.types import ListSortBy, SortOrder
from headless_obsidian_mcp.transport.formatters import (
    format_list,
    format_move_path,
    format_read,
)
from headless_obsidian_mcp.transport.pagination import page_items


class FormatterTests(unittest.TestCase):
    def test_list_escapes_table_paths(self) -> None:
        page = page_items(
            [
                {
                    "path": "folder/a|b`c.md",
                    "kind": "file",
                    "size": 12,
                    "modified_at": "2026-04-30T00:00:00+00:00",
                }
            ],
            limit=10,
            offset=0,
        )

        rendered = format_list("", page, ListSortBy.NAME, SortOrder.ASC)

        self.assertIn("a\\|b`c.md", rendered)

    def test_read_uses_code_spans_that_survive_backticks(self) -> None:
        rendered = format_read(
            {
                "path": "notes/`quoted`.md",
                "file": {
                    "modified_at": datetime.now(timezone.utc).isoformat(),
                    "size": 1,
                },
                "tags": ["x`y"],
                "wikilinks": [],
                "markdown_links": [],
                "content": "body",
            }
        )

        self.assertIn("``notes/`quoted`.md``", rendered)
        self.assertIn("``x`y``", rendered)
        self.assertIn("just now", rendered)

    def test_move_path_pluralizes_rewrite_count(self) -> None:
        singular = format_move_path(
            {"source": "a.md", "destination": "b.md", "rewritten_files": 1}
        )
        plural = format_move_path(
            {"source": "a.md", "destination": "b.md", "rewritten_files": 2}
        )

        self.assertIn("1 file", singular)
        self.assertIn("2 files", plural)


if __name__ == "__main__":
    unittest.main()
