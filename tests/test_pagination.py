import unittest

from obsidian_vault_mcp.transport.pagination import page_items, validate_page


class PaginationTests(unittest.TestCase):
    def test_page_items_reports_next_offset(self) -> None:
        page = page_items(["a", "b", "c"], limit=2, offset=0)

        self.assertEqual(page.items, ("a", "b"))
        self.assertEqual(page.total, 3)
        self.assertTrue(page.has_more)
        self.assertEqual(page.next_offset, 2)

    def test_validate_page_rejects_bad_bounds(self) -> None:
        with self.assertRaises(ValueError):
            validate_page(0, 0, max_limit=10)
        with self.assertRaises(ValueError):
            validate_page(11, 0, max_limit=10)
        with self.assertRaises(ValueError):
            validate_page(1, -1, max_limit=10)


if __name__ == "__main__":
    unittest.main()
