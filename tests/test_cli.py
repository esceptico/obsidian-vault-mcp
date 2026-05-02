import os
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from obsidian_mcp.app.cli import cli, run_server


class CliTests(unittest.TestCase):
    def test_run_port_zero_overrides_environment(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("obsidian_mcp.app.cli.configure_default_logging"),
            patch("obsidian_mcp.app.cli.serve_main") as serve_main,
        ):
            run_server(None, 0)
            self.assertEqual(os.environ["OBSIDIAN_MCP_PORT"], "0")
            serve_main.assert_called_once_with()

    def test_run_delegates_to_server(self) -> None:
        runner = CliRunner()
        with patch("obsidian_mcp.app.cli.run_server") as run:
            result = runner.invoke(cli, ["run", "--port", "0"])

        self.assertEqual(result.exit_code, 0)
        run.assert_called_once_with(None, 0)

    def test_start_delegates_to_daemon(self) -> None:
        runner = CliRunner()
        service = unittest.mock.Mock()
        service.start.return_value = 123
        with patch("obsidian_mcp.app.daemon.DaemonService.from_settings", return_value=service) as factory:
            result = runner.invoke(cli, ["start", "--host", "127.0.0.1", "--port", "9000"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("started, pid=123", result.output)
        factory.assert_called_once_with(host="127.0.0.1", port=9000)
        service.start.assert_called_once_with()

    def test_stop_delegates_to_daemon(self) -> None:
        runner = CliRunner()
        service = unittest.mock.Mock()
        service.stop.return_value = "stopped"
        with patch("obsidian_mcp.app.daemon.DaemonService.from_settings", return_value=service) as factory:
            result = runner.invoke(cli, ["stop", "--timeout", "1.5"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("stopped", result.output)
        factory.assert_called_once_with()
        service.stop.assert_called_once_with(1.5)

    def test_status_delegates_to_daemon(self) -> None:
        runner = CliRunner()
        service = unittest.mock.Mock()
        service.status.return_value = "stopped"
        with patch("obsidian_mcp.app.daemon.DaemonService.from_settings", return_value=service) as factory:
            result = runner.invoke(cli, ["status"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("stopped", result.output)
        factory.assert_called_once_with(host=None, port=None)
        service.status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
