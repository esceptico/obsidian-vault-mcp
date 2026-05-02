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

from obsidian_vault_mcp.core.config import ServerSettings, load_settings

_START_TIMEOUT = 10.0
_STOP_TIMEOUT = 10.0
_STARTUP_KILL_TIMEOUT = 2.0
_LOG_TAIL_LINES = 200
_FOLLOW_POLL_INTERVAL = 0.2
_HEALTH_PROBE_TIMEOUT = 1.0
_HEALTH_POLL_INTERVAL = 0.2

_INDEX_FILENAME = "index.sqlite"
_INDEX_SUBDIR = ".obsidian-vault-mcp"

_STATE_DIR_ENV = "OBSIDIAN_VAULT_MCP_STATE_DIR"
_DARWIN_SUBPATH = "Library/Application Support/obsidian-vault-mcp"
_XDG_SUBPATH = "obsidian-vault-mcp"
_LINUX_FALLBACK = ".local/state/obsidian-vault-mcp"


class DaemonError(RuntimeError):
    """Raised when the daemon lifecycle cannot complete safely."""


class _StopOutcome(StrEnum):
    ABSENT = "absent"
    TERMINATED = "terminated"
    KILLED = "killed"


@dataclass(frozen=True)
class _Endpoint:
    bind_host: str
    dial_host: str
    port: int

    @classmethod
    def resolve(
        cls,
        settings: ServerSettings,
        *,
        host: str | None = None,
        port: int | None = None,
    ) -> "_Endpoint":
        bind_host = host or settings.host
        bind_port = settings.port if port is None else port
        return cls(
            bind_host=bind_host, dial_host=_dialable_host(bind_host), port=bind_port
        )


@dataclass(frozen=True)
class _DaemonPaths:
    state_dir: Path
    pid_file: Path
    log_file: Path


@dataclass(frozen=True)
class _DaemonStatus:
    settings: ServerSettings
    endpoint: _Endpoint
    running: bool
    note_count: int | None
    pid: int | None = None
    stale_pid: int | None = None
    healthy: bool | None = None

    def format(self) -> str:
        if self.running and self.healthy is False:
            head = (
                "◐ obsidian-vault-mcp process running, health check failed "
                f"(pid {self.pid}, port {self.endpoint.port})"
            )
        elif self.running:
            head = f"● obsidian-vault-mcp running (pid {self.pid}, port {self.endpoint.port})"
        elif self.stale_pid is not None:
            head = f"○ obsidian-vault-mcp not running (stale pid {self.stale_pid})"
        else:
            head = "○ obsidian-vault-mcp not running"

        lines = [
            head,
            f"  vault:  {_compress_home(self.settings.vault.root.expanduser())}",
        ]
        if self.note_count is not None:
            suffix = "" if self.running else " (last-known)"
            lines.append(f"  notes:  {self.note_count} indexed{suffix}")
        return "\n".join(lines)


class _PidFile:
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


class _ProcessTable:
    def is_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def stop(
        self, pid: int, timeout: float, *, poll_interval: float = 0.1
    ) -> _StopOutcome:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return _StopOutcome.ABSENT

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_alive(pid):
                return _StopOutcome.TERMINATED
            time.sleep(poll_interval)

        if not self.is_alive(pid):
            return _StopOutcome.TERMINATED

        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return _StopOutcome.TERMINATED

        return _StopOutcome.KILLED


class _HealthClient:
    def wait(self, endpoint: _Endpoint, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if self.probe(endpoint, timeout=min(_HEALTH_PROBE_TIMEOUT, remaining)):
                return True
            sleep_for = min(
                _HEALTH_POLL_INTERVAL, max(0.0, deadline - time.monotonic())
            )
            time.sleep(sleep_for)

    def probe(self, endpoint: _Endpoint, *, timeout: float) -> bool:
        try:
            with urllib.request.urlopen(
                f"http://{endpoint.dial_host}:{endpoint.port}/health",
                timeout=timeout,
            ) as response:
                return response.status == 200
        except OSError, urllib.error.URLError:
            return False


class _ServerLauncher:
    def spawn(self, endpoint: _Endpoint, log_file: Path) -> subprocess.Popen[bytes]:
        command = [
            sys.executable,
            "-m",
            "obsidian_vault_mcp.app.cli",
            "run",
            "--host",
            endpoint.bind_host,
            "--port",
            str(endpoint.port),
        ]

        with log_file.open("ab") as log_handle:
            return subprocess.Popen(
                command,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )


class _LogReader:
    def show(self, log_file: Path, *, follow: bool) -> None:
        if follow:
            self._follow(log_file)
            return

        if not log_file.exists():
            print(f"no log file at {log_file}")
            return

        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-_LOG_TAIL_LINES:]:
            print(line)

    def _follow(self, log_file: Path) -> None:
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
                        time.sleep(_FOLLOW_POLL_INTERVAL)
        except KeyboardInterrupt:
            return


class DaemonService:
    def __init__(
        self,
        settings: ServerSettings,
        endpoint: _Endpoint,
        paths: _DaemonPaths,
        *,
        pid_file: _PidFile | None = None,
        process_table: _ProcessTable | None = None,
        health_client: _HealthClient | None = None,
        launcher: _ServerLauncher | None = None,
        log_reader: _LogReader | None = None,
    ) -> None:
        self.settings = settings
        self.endpoint = endpoint
        self.paths = paths
        self.pid_file = pid_file or _PidFile(paths.pid_file)
        self.process_table = process_table or _ProcessTable()
        self.health_client = health_client or _HealthClient()
        self.launcher = launcher or _ServerLauncher()
        self.log_reader = log_reader or _LogReader()

    @classmethod
    def from_settings(
        cls,
        *,
        host: str | None = None,
        port: int | None = None,
    ) -> "DaemonService":
        settings = load_settings()
        endpoint = _Endpoint.resolve(settings, host=host, port=port)
        return cls(settings, endpoint, _daemon_paths(settings.vault.root))

    def start(self, timeout: float = _START_TIMEOUT) -> int:
        if self.endpoint.port == 0:
            raise ValueError(
                "start does not support --port 0 (health port cannot be discovered)"
            )

        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        existing = self.pid_file.read()
        if existing is not None and self.process_table.is_alive(existing):
            raise DaemonError(
                f"obsidian-vault-mcp is already running with pid {existing}"
            )
        self.pid_file.remove()

        proc = self.launcher.spawn(self.endpoint, self.paths.log_file)
        self.pid_file.write(proc.pid)

        try:
            healthy = self.health_client.wait(self.endpoint, timeout)
        except BaseException:
            self._abort_child(proc.pid)
            raise

        if not healthy:
            self._abort_child(proc.pid)
            raise DaemonError(
                f"server did not become healthy within {timeout:g}s; see {self.paths.log_file}"
            )

        return proc.pid

    def stop(self, timeout: float = _STOP_TIMEOUT) -> str:
        pid = self.pid_file.read()
        if pid is None:
            return "stopped"

        if not self.process_table.is_alive(pid):
            self.pid_file.remove()
            return "stopped (stale pid removed)"

        outcome = self.process_table.stop(pid, timeout)
        self.pid_file.remove()
        return "stopped (killed)" if outcome is _StopOutcome.KILLED else "stopped"

    def status(self) -> str:
        return self._status().format()

    def logs(self, *, follow: bool) -> None:
        self.log_reader.show(self.paths.log_file, follow=follow)

    def _status(self) -> _DaemonStatus:
        pid = self.pid_file.read()
        note_count = _count_notes(_index_path(self.settings.vault.root))

        if pid is None:
            return _DaemonStatus(
                self.settings,
                self.endpoint,
                running=False,
                note_count=note_count,
            )
        if not self.process_table.is_alive(pid):
            return _DaemonStatus(
                self.settings,
                self.endpoint,
                running=False,
                note_count=note_count,
                stale_pid=pid,
            )

        healthy = self.health_client.probe(self.endpoint, timeout=_HEALTH_PROBE_TIMEOUT)
        return _DaemonStatus(
            self.settings,
            self.endpoint,
            running=True,
            pid=pid,
            note_count=note_count,
            healthy=healthy,
        )

    def _abort_child(self, pid: int) -> None:
        try:
            self.process_table.stop(pid, _STARTUP_KILL_TIMEOUT)
        finally:
            self.pid_file.remove()


def _daemon_paths(vault_root: Path) -> _DaemonPaths:
    state_dir = _state_dir()
    vault_id = _vault_id(vault_root)
    return _DaemonPaths(
        state_dir=state_dir,
        pid_file=state_dir / f"{vault_id}.pid",
        log_file=state_dir / f"{vault_id}.log",
    )


def _state_dir() -> Path:
    if custom := os.environ.get(_STATE_DIR_ENV):
        return Path(custom).expanduser()
    if sys.platform == "darwin":
        return Path.home() / _DARWIN_SUBPATH
    if xdg := os.environ.get("XDG_STATE_HOME"):
        return Path(xdg).expanduser() / _XDG_SUBPATH
    return Path.home() / _LINUX_FALLBACK


def _vault_id(vault_root: Path) -> str:
    path = vault_root.expanduser().resolve()
    name = re.sub(r"[^A-Za-z0-9_-]", "_", path.name) or "vault"
    path_hash = hashlib.blake2b(str(path).encode(), digest_size=8).hexdigest()
    return f"{name}-{path_hash}"


def _index_path(vault_root: Path) -> Path:
    return vault_root.expanduser() / _INDEX_SUBDIR / _INDEX_FILENAME


def _count_notes(db_path: Path) -> int | None:
    if not db_path.exists():
        return None
    try:
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM note_meta").fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error:
        return None


def _dialable_host(bind_host: str) -> str:
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
