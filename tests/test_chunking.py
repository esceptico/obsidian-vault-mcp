import unittest

from obsidian_mcp.index.chunking import chunk_markdown


class ChunkingTests(unittest.TestCase):
    def test_markdown_headings_become_chunk_metadata(self) -> None:
        chunks = chunk_markdown("# Plan\nalpha\n\n## Details\nbeta", body_start=10)

        self.assertEqual([chunk.heading_path for chunk in chunks], ["Plan", "Plan > Details"])
        self.assertEqual(chunks[0].start_char, 10)
        self.assertIn("# Plan", chunks[0].text)
        self.assertIn("beta", chunks[1].text)

    def test_large_section_is_split_without_dropping_late_content(self) -> None:
        body = "# Large\n" + ("alpha sentence. " * 500) + "late-marker"
        chunks = chunk_markdown(body)

        self.assertGreater(len(chunks), 1)
        self.assertIn("late-marker", chunks[-1].text)
        self.assertTrue(all(chunk.start_char < chunk.end_char for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
