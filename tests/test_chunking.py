import unittest

from obsidian_vault_mcp.index.chunking import chunk_markdown


class ChunkingTests(unittest.TestCase):
    def test_markdown_headings_become_chunk_metadata(self) -> None:
        chunks = chunk_markdown("# Plan\nalpha\n\n## Details\nbeta", body_start=10)

        self.assertEqual(
            [chunk.heading_path for chunk in chunks], ["Plan", "Plan > Details"]
        )
        self.assertEqual(chunks[0].start_char, 10)
        self.assertIn("# Plan", chunks[0].text)
        self.assertIn("beta", chunks[1].text)

    def test_large_section_is_split_without_dropping_late_content(self) -> None:
        body = "# Large\n" + ("alpha sentence. " * 500) + "late-marker"
        chunks = chunk_markdown(body)

        self.assertGreater(len(chunks), 1)
        self.assertIn("late-marker", chunks[-1].text)
        self.assertTrue(all(chunk.start_char < chunk.end_char for chunk in chunks))

    def test_nonleaf_heading_only_chunks_are_not_indexed(self) -> None:
        chunks = chunk_markdown("# Parent\n## Child\nuseful body")

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].heading_path, "Parent > Child")
        self.assertTrue(chunks[0].text.startswith("## Child"))

    def test_split_list_chunks_start_on_list_items_when_possible(self) -> None:
        body = "# Items\n" + "\n".join(
            f"- item {index} {'x' * 50}" for index in range(200)
        )
        chunks = chunk_markdown(body)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks[1:]:
            self.assertTrue(chunk.text.startswith("- item"), chunk.text[:80])


if __name__ == "__main__":
    unittest.main()
