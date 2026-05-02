import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from headless_obsidian_mcp.app.daemon import (
    DaemonService,
    _DaemonPaths,
    _Endpoint,
    _PidFile,
    _ProcessTable,
)


class DaemonTests(unittest.TestCase):
    def test_pid_file_handles_missing_and_invalid_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = _PidFile(Path(tmp) / "server.pid")
            self.assertIsNone(pid_file.read())
            pid_file.path.write_text("not-a-pid", encoding="utf-8")
            self.assertIsNone(pid_file.read())

    def test_pid_file_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = _PidFile(Path(tmp) / "server.pid")
            pid_file.write(123)
            self.assertEqual(pid_file.read(), 123)

    def test_process_alive_false_for_unlikely_pid(self) -> None:
        self.assertFalse(_ProcessTable().is_alive(99999999))

    def test_start_refuses_live_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = self._service(tmp)
            service.pid_file.write(os.getpid())

            with self.assertRaises(RuntimeError):
                service.start(timeout=0.01)

    def test_start_removes_stale_pid_and_writes_child_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = self._service(tmp, host="127.0.0.1", port=8000)
            service.pid_file.write(99999999)
            process = Mock(pid=456)

            with (
                patch(
                    "headless_obsidian_mcp.app.daemon.subprocess.Popen",
                    return_value=process,
                ),
                patch(
                    "headless_obsidian_mcp.app.daemon._HealthClient.wait",
                    return_value=True,
                ),
            ):
                pid = service.start()

            self.assertEqual(pid, 456)
            self.assertEqual(service.pid_file.read(), 456)

    def test_start_health_uses_resolved_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = self._service(tmp, host="127.0.0.1", port=8008)
            process = Mock(pid=456)

            with (
                patch(
                    "headless_obsidian_mcp.app.daemon.subprocess.Popen",
                    return_value=process,
                ),
                patch(
                    "headless_obsidian_mcp.app.daemon._HealthClient.wait",
                    return_value=True,
                ) as wait,
            ):
                service.start()

            endpoint = wait.call_args.args[0]
            self.assertEqual(endpoint.dial_host, "127.0.0.1")
            self.assertEqual(endpoint.port, 8008)
            self.assertEqual(wait.call_args.args[1], 10.0)

    def test_start_rejects_ephemeral_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self._service(tmp, port=0).start()

    def test_stop_removes_stale_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = self._service(tmp)
            service.pid_file.write(99999999)

            self.assertIn("stale", service.stop())
            self.assertFalse(service.pid_file.path.exists())

    def test_status_reports_stale_pid_without_mutating_pidfile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = self._service(tmp)
            service.pid_file.write(99999999)

            self.assertIn("stale", service.status())
            self.assertTrue(service.pid_file.path.exists())

    def test_status_probes_resolved_health_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = self._service(tmp, host="0.0.0.0", port=9000)
            service.pid_file.write(os.getpid())

            with patch(
                "headless_obsidian_mcp.app.daemon._HealthClient.probe",
                return_value=True,
            ) as probe:
                output = service.status()

            endpoint = probe.call_args.args[0]
            self.assertIn("port 9000", output)
            self.assertEqual(endpoint.bind_host, "0.0.0.0")
            self.assertEqual(endpoint.dial_host, "127.0.0.1")
            self.assertEqual(endpoint.port, 9000)
            self.assertEqual(probe.call_args.kwargs["timeout"], 1.0)

    def _service(
        self,
        tmp: str,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> DaemonService:
        root = Path(tmp)
        settings = SimpleNamespace(
            host=host,
            port=port,
            vault=SimpleNamespace(root=root),
        )
        endpoint = _Endpoint.resolve(settings)
        paths = _DaemonPaths(root, root / "server.pid", root / "server.log")
        return DaemonService(settings, endpoint, paths)


if __name__ == "__main__":
    unittest.main()
