import os
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from obsidian_mcp.app.daemon import (
    DaemonPaths,
    daemon_status,
    is_process_alive,
    read_pid,
    start_daemon,
    stop_daemon,
    write_pid,
)


class DaemonTests(unittest.TestCase):
    def test_read_pid_handles_missing_and_invalid_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.pid"
            self.assertIsNone(read_pid(path))
            path.write_text("not-a-pid", encoding="utf-8")
            self.assertIsNone(read_pid(path))

    def test_write_pid_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "server.pid"
            write_pid(path, 123)
            self.assertEqual(read_pid(path), 123)

    def test_process_alive_false_for_unlikely_pid(self) -> None:
        self.assertFalse(is_process_alive(99999999))

    def test_start_refuses_live_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = DaemonPaths(Path(tmp), Path(tmp) / "server.pid", Path(tmp) / "server.log")
            write_pid(paths.pid_file, os.getpid())
            with (
                patch("obsidian_mcp.app.daemon.daemon_paths", return_value=paths),
                self.assertRaises(RuntimeError),
            ):
                start_daemon(None, 8000, timeout=0.01)

    def test_start_removes_stale_pid_and_writes_child_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = DaemonPaths(Path(tmp), Path(tmp) / "server.pid", Path(tmp) / "server.log")
            write_pid(paths.pid_file, 99999999)
            process = Mock(pid=456)
            with (
                patch("obsidian_mcp.app.daemon.daemon_paths", return_value=paths),
                patch("obsidian_mcp.app.daemon.subprocess.Popen", return_value=process),
                patch("obsidian_mcp.app.daemon.HealthClient.wait", return_value=True),
            ):
                pid = start_daemon("127.0.0.1", 8000)

            self.assertEqual(pid, 456)
            self.assertEqual(read_pid(paths.pid_file), 456)

    def test_start_health_uses_effective_settings_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = DaemonPaths(Path(tmp), Path(tmp) / "server.pid", Path(tmp) / "server.log")
            process = Mock(pid=456)
            settings = SimpleNamespace(
                host="127.0.0.1",
                port=8008,
                vault=SimpleNamespace(root=Path(tmp)),
            )
            with (
                patch("obsidian_mcp.app.daemon.daemon_paths", return_value=paths),
                patch("obsidian_mcp.app.daemon.load_settings", return_value=settings),
                patch("obsidian_mcp.app.daemon.subprocess.Popen", return_value=process),
                patch("obsidian_mcp.app.daemon.HealthClient.wait", return_value=True) as wait,
            ):
                start_daemon(None, None)

            wait.assert_called_once_with("127.0.0.1", 8008, 10.0)

    def test_start_rejects_ephemeral_port(self) -> None:
        with self.assertRaises(ValueError):
            start_daemon(None, 0)

    def test_stop_removes_stale_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = DaemonPaths(Path(tmp), Path(tmp) / "server.pid", Path(tmp) / "server.log")
            write_pid(paths.pid_file, 99999999)
            with patch("obsidian_mcp.app.daemon.daemon_paths", return_value=paths):
                self.assertIn("stale", stop_daemon())
            self.assertFalse(paths.pid_file.exists())

    def test_status_reports_stale_pid_without_mutating_pidfile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = DaemonPaths(Path(tmp), Path(tmp) / "server.pid", Path(tmp) / "server.log")
            write_pid(paths.pid_file, 99999999)
            with patch("obsidian_mcp.app.daemon.daemon_paths", return_value=paths):
                self.assertIn("stale", daemon_status(None, None))
            self.assertTrue(paths.pid_file.exists())

    def test_status_probes_effective_health_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = DaemonPaths(Path(tmp), Path(tmp) / "server.pid", Path(tmp) / "server.log")
            write_pid(paths.pid_file, os.getpid())
            settings = SimpleNamespace(
                host="0.0.0.0",
                port=8008,
                vault=SimpleNamespace(root=Path(tmp)),
            )
            with (
                patch("obsidian_mcp.app.daemon.daemon_paths", return_value=paths),
                patch("obsidian_mcp.app.daemon.load_settings", return_value=settings),
                patch("obsidian_mcp.app.daemon.HealthClient.probe", return_value=True) as probe,
            ):
                output = daemon_status(None, 9000)

            self.assertIn("port 9000", output)
            probe.assert_called_once_with("127.0.0.1", 9000, timeout=1.0)


if __name__ == "__main__":
    unittest.main()
