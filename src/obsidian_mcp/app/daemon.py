import hashlib
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from obsidian_mcp.core.config import ServerSettings, load_settings

START_TIMEOUT = 10.0
STOP_TIMEOUT = 10.0
STARTUP_KILL_TIMEOUT = 2.0
LOG_TAIL_LINES = 200
FOLLOW_POLL_INTERVAL = 0.2
HEALTH_PROBE_TIMEOUT = 1.0
HEALTH_POLL_INTERVAL = 0.2

INDEX_FILENAME = "index.sqlite"
INDEX_SUBDIR = ".obsidian-mcp"

STATE_DIR_ENV = "OBSIDIAN_MCP_STATE_DIR"
DARWIN_SUBPATH = "Library/Application Support/obsidian-mcp"
XDG_SUBPATH = "obsidian-mcp"
LINUX_FALLBACK = ".local/state/obsidian-mcp"


class DaemonError(RuntimeError):
    """Lifecycle-level failure such as an already-running server."""


class StopOutcome(StrEnum):
    ABSENT = "absent"
    TERMINATED = "terminated"
    KILLED = "killed"


@dataclass(frozen=True)
class DaemonPaths:
    state_dir: Path
    pid_file: Path
    log_file: Path


@dataclass(frozen=True)
class DaemonStatus:
    settings: ServerSettings
    running: bool
    note_count: int | None
    pid: int | None = None
    stale_pid: int | None = None
    healthy: bool | None = None
    checked_port: int | None = None

    def format(self) -> str:
        port = self.checked_port if self.checked_port is not None else self.settings.port
        if self.running and self.healthy is False:
            head = f"◐ obsidian-mcp process running, health check failed (pid {self.pid}, port {port})"
        elif self.running:
            head = f"● obsidian-mcp running (pid {self.pid}, port {port})"
        elif self.stale_pid is not None:
            head = f"○ obsidian-mcp not running (stale pid {self.stale_pid})"
        else:
            head = "○ obsidian-mcp not running"

        lines = [head, f"  vault:  {_compress_home(self.settings.vault.root.expanduser())}"]
        if self.note_count is not None:
            suffix = "" if self.running else " (last-known)"
            lines.append(f"  notes:  {self.note_count} indexed{suffix}")
        return "\n".join(lines)


class PidFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> int | None:
        if not self.path.exists():
            return None

        raw = self.path.read_text(encoding="utf-8").strip()
        if not raw:
            return None

        try:
            return int(raw)
        except ValueError:
            return None

    def write(self, pid: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"{pid}\n", encoding="utf-8")

    def remove(self) -> None:
        self.path.unlink(missing_ok=True)


class ProcessTable:
    def is_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def stop(self, pid: int, timeout: float, *, poll_interval: float = 0.1) -> StopOutcome:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return StopOutcome.ABSENT

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_alive(pid):
                return StopOutcome.TERMINATED
            time.sleep(poll_interval)

        if not self.is_alive(pid):
            return StopOutcome.TERMINATED

        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return StopOutcome.TERMINATED

        return StopOutcome.KILLED


class HealthClient:
    def wait(self, host: str, port: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if self.probe(host, port, timeout=min(HEALTH_PROBE_TIMEOUT, remaining)):
                return True
            sleep_for = min(HEALTH_POLL_INTERVAL, max(0.0, deadline - time.monotonic()))
            time.sleep(sleep_for)

    def probe(self, host: str, port: int, *, timeout: float) -> bool:
        try:
            with urllib.request.urlopen(
                f"http://{host}:{port}/health", timeout=timeout
            ) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False


class ServerLauncher:
    def spawn(self, host: str | None, port: int | None, log_file: Path) -> subprocess.Popen[bytes]:
        command = [sys.executable, "-m", "obsidian_mcp.app.cli", "run"]
        if host is not None:
            command.extend(["--host", host])
        if port is not None:
            command.extend(["--port", str(port)])

        with log_file.open("ab") as log_handle:
            return subprocess.Popen(
                command,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )


class LogReader:
    def show(self, log_file: Path, *, follow: bool) -> None:
        if follow:
            self.follow(log_file)
            return

        if not log_file.exists():
            print(f"no log file at {log_file}")
            return

        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-LOG_TAIL_LINES:]:
            print(line)

    def follow(self, log_file: Path) -> None:
        if not log_file.exists():
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.touch()

        try:
            with log_file.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(0, os.SEEK_END)
                while True:
                    chunk = handle.read()
                    if chunk:
                        print(chunk, end="", flush=True)
                    else:
                        time.sleep(FOLLOW_POLL_INTERVAL)
        except KeyboardInterrupt:
            return


class DaemonService:
    def __init__(
        self,
        settings: ServerSettings,
        paths: DaemonPaths,
        *,
        pid_file: PidFile | None = None,
        process_table: ProcessTable | None = None,
        health_client: HealthClient | None = None,
        launcher: ServerLauncher | None = None,
        log_reader: LogReader | None = None,
    ) -> None:
        self.settings = settings
        self.paths = paths
        self.pid_file = pid_file or PidFile(paths.pid_file)
        self.process_table = process_table or ProcessTable()
        self.health_client = health_client or HealthClient()
        self.launcher = launcher or ServerLauncher()
        self.log_reader = log_reader or LogReader()

    @classmethod
    def from_settings(cls) -> "DaemonService":
        settings = load_settings()
        return cls(settings, daemon_paths(settings.vault.root))

    def start(
        self,
        host: str | None,
        port: int | None,
        timeout: float = START_TIMEOUT,
    ) -> int:
        bind_host = host or self.settings.host
        bind_port = port if port is not None else self.settings.port
        if bind_port == 0:
            raise ValueError("start does not support --port 0 (health port cannot be discovered)")

        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        existing = self.pid_file.read()
        if existing is not None and self.process_table.is_alive(existing):
            raise DaemonError(f"obsidian-mcp is already running with pid {existing}")
        self.pid_file.remove()

        proc = self.launcher.spawn(host, port, self.paths.log_file)
        self.pid_file.write(proc.pid)

        try:
            healthy = self.health_client.wait(_health_host(bind_host), bind_port, timeout)
        except BaseException:
            self._abort_child(proc.pid)
            raise

        if not healthy:
            self._abort_child(proc.pid)
            raise DaemonError(
                f"server did not become healthy within {timeout:g}s; see {self.paths.log_file}"
            )

        return proc.pid

    def stop(self, timeout: float = STOP_TIMEOUT) -> str:
        pid = self.pid_file.read()
        if pid is None:
            return "stopped"

        if not self.process_table.is_alive(pid):
            self.pid_file.remove()
            return "stopped (stale pid removed)"

        outcome = self.process_table.stop(pid, timeout)
        self.pid_file.remove()
        return "stopped (killed)" if outcome is StopOutcome.KILLED else "stopped"

    def status(self, host: str | None = None, port: int | None = None) -> DaemonStatus:
        pid = self.pid_file.read()
        note_count = _count_notes(_index_path(self.settings.vault.root))

        if pid is None:
            return DaemonStatus(self.settings, running=False, note_count=note_count)
        if not self.process_table.is_alive(pid):
            return DaemonStatus(
                self.settings,
                running=False,
                note_count=note_count,
                stale_pid=pid,
            )

        bind_host = host or self.settings.host
        bind_port = port if port is not None else self.settings.port
        healthy = self.health_client.probe(
            _health_host(bind_host),
            bind_port,
            timeout=HEALTH_PROBE_TIMEOUT,
        )
        return DaemonStatus(
            self.settings,
            running=True,
            pid=pid,
            note_count=note_count,
            healthy=healthy,
            checked_port=bind_port,
        )

    def logs(self, *, follow: bool) -> None:
        self.log_reader.show(self.paths.log_file, follow=follow)

    def _abort_child(self, pid: int) -> None:
        try:
            self.process_table.stop(pid, STARTUP_KILL_TIMEOUT)
        finally:
            self.pid_file.remove()


def start_daemon(
    host: str | None,
    port: int | None,
    timeout: float = START_TIMEOUT,
) -> int:
    return DaemonService.from_settings().start(host, port, timeout)


def stop_daemon(timeout: float = STOP_TIMEOUT) -> str:
    return DaemonService.from_settings().stop(timeout)


def daemon_status(host: str | None = None, port: int | None = None) -> str:
    return DaemonService.from_settings().status(host, port).format()


def show_logs(follow: bool) -> None:
    DaemonService.from_settings().logs(follow=follow)


def read_pid(path: Path) -> int | None:
    return PidFile(path).read()


def write_pid(path: Path, pid: int) -> None:
    PidFile(path).write(pid)


def is_process_alive(pid: int) -> bool:
    return ProcessTable().is_alive(pid)


def wait_for_health(host: str, port: int, timeout: float) -> bool:
    return HealthClient().wait(host, port, timeout)


def start(host: str | None, port: int | None, timeout: float = START_TIMEOUT) -> int:
    return start_daemon(host, port, timeout)


def stop(timeout: float = STOP_TIMEOUT) -> str:
    return stop_daemon(timeout)


def status(host: str | None = None, port: int | None = None) -> str:
    return daemon_status(host, port)


def daemon_paths(vault_root: Path | None = None) -> DaemonPaths:
    if vault_root is None:
        vault_root = load_settings().vault.root
    state_dir = _state_dir()
    vault_id = _vault_id(vault_root)
    return DaemonPaths(
        state_dir=state_dir,
        pid_file=state_dir / f"{vault_id}.pid",
        log_file=state_dir / f"{vault_id}.log",
    )


def _state_dir() -> Path:
    if custom := os.environ.get(STATE_DIR_ENV):
        return Path(custom).expanduser()
    if sys.platform == "darwin":
        return Path.home() / DARWIN_SUBPATH
    if xdg := os.environ.get("XDG_STATE_HOME"):
        return Path(xdg).expanduser() / XDG_SUBPATH
    return Path.home() / LINUX_FALLBACK


def _vault_id(vault_root: Path) -> str:
    path = vault_root.expanduser().resolve()
    name = re.sub(r"[^A-Za-z0-9_-]", "_", path.name) or "vault"
    path_hash = hashlib.blake2b(str(path).encode(), digest_size=8).hexdigest()
    return f"{name}-{path_hash}"


def _index_path(vault_root: Path) -> Path:
    return vault_root.expanduser() / INDEX_SUBDIR / INDEX_FILENAME


def _count_notes(db_path: Path) -> int | None:
    if not db_path.exists():
        return None
    try:
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM note_meta").fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error:
        return None


def _health_host(bind_host: str) -> str:
    if bind_host in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return bind_host


def _compress_home(path: Path) -> str:
    home = str(Path.home())
    value = str(path)
    if value == home:
        return "~"
    if value.startswith(home + os.sep):
        return "~" + value[len(home) :]
    return value
