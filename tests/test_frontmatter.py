import unittest

from obsidian_mcp.frontmatter import patch_frontmatter, split_frontmatter


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


if __name__ == "__main__":
    unittest.main()
