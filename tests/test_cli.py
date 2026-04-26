import argparse
import contextlib
import io
import os
import unittest
from unittest.mock import patch

from obsidian_mcp.app.cli import main


class CliTests(unittest.TestCase):
    def test_run_port_zero_overrides_environment(self) -> None:
        args = argparse.Namespace(command="run", host=None, port=0)
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("obsidian_mcp.core.logging.configure_default_logging"),
            patch("obsidian_mcp.transport.http.main") as serve_main,
        ):
            main(args)
            self.assertEqual(os.environ["OBSIDIAN_MCP_PORT"], "0")
            serve_main.assert_called_once_with()

    def test_start_delegates_to_daemon(self) -> None:
        args = argparse.Namespace(command="start", host="127.0.0.1", port=9000)
        with patch("obsidian_mcp.app.daemon.start_daemon", return_value=123) as start, contextlib.redirect_stdout(io.StringIO()):
            main(args)
        start.assert_called_once_with("127.0.0.1", 9000)

    def test_stop_delegates_to_daemon(self) -> None:
        args = argparse.Namespace(command="stop", timeout=1.5)
        with patch("obsidian_mcp.app.daemon.stop_daemon", return_value="stopped") as stop, contextlib.redirect_stdout(io.StringIO()):
            main(args)
        stop.assert_called_once_with(1.5)

    def test_status_delegates_to_daemon(self) -> None:
        args = argparse.Namespace(command="status", host=None, port=None)
        with patch("obsidian_mcp.app.daemon.daemon_status", return_value="stopped") as status, contextlib.redirect_stdout(io.StringIO()):
            main(args)
        status.assert_called_once_with(None, None)


if __name__ == "__main__":
    unittest.main()
