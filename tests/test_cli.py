import argparse
import os
import unittest
from unittest.mock import patch

from obsidian_mcp.app.cli import main


class CliTests(unittest.TestCase):
    def test_serve_port_zero_overrides_environment(self) -> None:
        args = argparse.Namespace(command="serve", host=None, port=0)
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("obsidian_mcp.core.logging.configure_default_logging"),
            patch("obsidian_mcp.transport.http.main") as serve_main,
        ):
            main(args)
            self.assertEqual(os.environ["OBSIDIAN_MCP_PORT"], "0")
            serve_main.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
