import logging
import unittest

from obsidian_mcp.core.logging import get_logger


class LoggingTests(unittest.TestCase):
    def test_get_logger_returns_namespaced_logger(self) -> None:
        log = get_logger("vault")
        self.assertIsInstance(log, logging.Logger)
        self.assertEqual(log.name, "obsidian_mcp.vault")

    def test_loggers_share_obsidian_mcp_root(self) -> None:
        a = get_logger("vault")
        b = get_logger("server")
        self.assertEqual(a.parent.name, "obsidian_mcp")
        self.assertEqual(b.parent.name, "obsidian_mcp")


if __name__ == "__main__":
    unittest.main()
