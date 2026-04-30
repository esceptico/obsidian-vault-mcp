import unittest

from obsidian_mcp.markdown.obsidian import inline_tags, rewrite_wikilink_targets, wikilinks


class ObsidianMarkdownTests(unittest.TestCase):
    def test_links_and_tags_ignore_fenced_code_blocks(self) -> None:
        markdown = "See [[Real Note]] #real\n\n```md\n[[Code Note]] #code\n```\n"

        self.assertEqual([link.target for link in wikilinks(markdown)], ["Real Note"])
        self.assertEqual(inline_tags(markdown), ["real"])

    def test_rewrite_wikilinks_ignores_fenced_code_blocks(self) -> None:
        markdown = "See [[Old]]\n\n```md\n[[Old]]\n```\n"

        rewritten = rewrite_wikilink_targets(markdown, {"Old"}, "New")

        self.assertIn("[[New]]", rewritten)
        self.assertIn("```md\n[[Old]]\n```", rewritten)


if __name__ == "__main__":
    unittest.main()
