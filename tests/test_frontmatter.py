import unittest

from headless_obsidian_mcp.markdown.frontmatter import (
    frontmatter_tags,
    patch_frontmatter,
    split_frontmatter,
)


class FrontmatterTests(unittest.TestCase):
    def test_crlf_fences_are_recognized(self) -> None:
        content = "---\r\ntitle: Hello\r\n---\r\nbody\r\n"
        fm, body = split_frontmatter(content)
        self.assertEqual(fm["title"], "Hello")
        self.assertEqual(body, "body\r\n")

    def test_patch_preserves_comments_and_order(self) -> None:
        content = "---\n# leading comment\nb: 2\na: 1\n---\nbody"
        patched = patch_frontmatter(content, {"c": 3})
        self.assertIn("# leading comment", patched)
        b_idx = patched.index("b: 2")
        a_idx = patched.index("a: 1")
        c_idx = patched.index("c: 3")
        self.assertLess(b_idx, a_idx)
        self.assertLess(a_idx, c_idx)

    def test_patch_with_none_deletes_key(self) -> None:
        content = "---\na: 1\nb: 2\n---\nbody"
        patched = patch_frontmatter(content, {"a": None})
        self.assertNotIn("a: 1", patched)
        self.assertIn("b: 2", patched)

    def test_malformed_yaml_raises(self) -> None:
        content = "---\n: bad : yaml :\n---\nbody"
        with self.assertRaises(ValueError):
            split_frontmatter(content)

    def test_dates_serialize_as_iso_strings(self) -> None:
        """Obsidian daily-note frontmatter commonly has date/datetime values;
        ruamel parses them as Python date objects which are not JSON
        serializable. The public splitter must coerce them to strings so
        vault_read / vault_search can return them over MCP."""
        import json

        content = (
            "---\n"
            "created: 2024-01-15\n"
            "modified: 2024-01-15T09:30:00\n"
            "tags:\n"
            "  - daily\n"
            "nested:\n"
            "  due: 2025-12-31\n"
            "---\n"
            "body"
        )
        fm, _ = split_frontmatter(content)
        # Must round-trip through json.dumps without TypeError.
        encoded = json.dumps(fm)
        self.assertIn("2024-01-15", encoded)
        self.assertIn("2024-01-15T09:30:00", encoded)
        self.assertIn("2025-12-31", encoded)

    def test_split_frontmatter_returns_plain_nested_dicts(self) -> None:
        fm, _ = split_frontmatter(
            "---\nnested:\n  key: value\nitems:\n  - a\n---\nbody"
        )

        self.assertIs(type(fm), dict)
        self.assertIs(type(fm["nested"]), dict)
        self.assertEqual(fm["items"], ["a"])

    def test_closing_fence_must_be_exact_line(self) -> None:
        content = "---\ntitle: bad\n----\nbody"

        fm, body = split_frontmatter(content)

        self.assertEqual(fm, {})
        self.assertEqual(body, content)

    def test_frontmatter_tags_normalizes_common_obsidian_shapes(self) -> None:
        self.assertEqual(
            frontmatter_tags({"tags": "#project/dex, tag  #daily"}),
            ["project/dex", "tag", "daily"],
        )
        self.assertEqual(
            frontmatter_tags({"tags": ["#a", " b ", None, ""]}), ["a", "b"]
        )


if __name__ == "__main__":
    unittest.main()
