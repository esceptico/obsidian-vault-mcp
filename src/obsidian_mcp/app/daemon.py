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
from pathlib import Path

from obsidian_mcp.core.config import ServerSettings, load_settings

_STOP_TIMEOUT_SECONDS = 10.0
_START_TIMEOUT_SECONDS = 10.0
_LOG_TAIL_LINES = 200
_INDEX_FILENAME = "index.sqlite"


@dataclass(frozen=True)
class DaemonPaths:
    state_dir: Path
    pid_file: Path
    log_file: Path


def daemon_paths(vault_root: Path | None = None) -> DaemonPaths:
    """Per-vault state file locations. The pidfile and log live OUTSIDE the
    vault on purpose: a synced vault (iCloud, Dropbox) would otherwise share
    runtime state across machines, where it's actively wrong (a pid valid on
    laptop A is meaningless on laptop B)."""
    if vault_root is None:
        vault_root = load_settings().vault.root
    vault_id = _vault_id(vault_root)
    state_dir = _state_dir()
    return DaemonPaths(
        state_dir=state_dir,
        pid_file=state_dir / f"{vault_id}.pid",
        log_file=state_dir / f"{vault_id}.log",
    )


def _state_dir() -> Path:
    custom = os.environ.get("OBSIDIAN_MCP_STATE_DIR")
    if custom:
        return Path(custom).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "obsidian-mcp"
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "obsidian-mcp"
    return Path.home() / ".local" / "state" / "obsidian-mcp"


def _vault_id(vault_root: Path) -> str:
    """Filename-safe identifier for one vault. Includes the basename for
    human readability and an 8-char path hash to avoid collisions across
    vaults that happen to share a name."""
    resolved = vault_root.expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", resolved.name) or "vault"
    return f"{safe}-{digest}"


def start_daemon(host: str | None, port: int | None, timeout: float = _START_TIMEOUT_SECONDS) -> int:
    health_host, health_port = _effective_endpoint(host, port)
    if health_port == 0:
        raise ValueError("start does not support --port 0 because the health port cannot be discovered")
    paths = daemon_paths()
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    existing = read_pid(paths.pid_file)
    if existing is not None and is_process_alive(existing):
        raise RuntimeError(f"obsidian-mcp is already running with pid {existing}")
    remove_pid(paths.pid_file)

    command = [sys.executable, "-m", "obsidian_mcp.app.cli", "run"]
    if host is not None:
        command.extend(["--host", host])
    if port is not None:
        command.extend(["--port", str(port)])

    log_handle = paths.log_file.open("ab")
    try:
        process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_handle.close()

    write_pid(paths.pid_file, process.pid)
    try:
        if not wait_for_health(_health_host(health_host), health_port, timeout):
            stop_pid(process.pid, timeout=2.0)
            remove_pid(paths.pid_file)
            raise RuntimeError(f"server did not become healthy within {timeout:g}s; see {paths.log_file}")
    except BaseException:
        if not is_process_alive(process.pid):
            remove_pid(paths.pid_file)
        raise
    return process.pid


def stop_daemon(timeout: float = _STOP_TIMEOUT_SECONDS) -> str:
    paths = daemon_paths()
    pid = read_pid(paths.pid_file)
    if pid is None:
        remove_pid(paths.pid_file)
        return "stopped"
    if not is_process_alive(pid):
        remove_pid(paths.pid_file)
        return "stopped (stale pid removed)"

    killed = stop_pid(pid, timeout)
    remove_pid(paths.pid_file)
    return "stopped" if not killed else "stopped (killed)"


def daemon_status(host: str | None, port: int | None) -> str:
    settings = load_settings()
    paths = daemon_paths(settings.vault.root)
    pid = read_pid(paths.pid_file)
    note_count = _count_notes(_index_path(settings))

    if pid is None:
        return _format_status(settings=settings, running=False, note_count=note_count)

    if not is_process_alive(pid):
        remove_pid(paths.pid_file)
        return _format_status(
            settings=settings, running=False, note_count=note_count, stale_pid=pid
        )

    return _format_status(settings=settings, running=True, pid=pid, note_count=note_count)


def _format_status(
    *,
    settings: ServerSettings,
    running: bool,
    note_count: int | None,
    pid: int | None = None,
    stale_pid: int | None = None,
) -> str:
    if running:
        head = f"● obsidian-mcp running (pid {pid}, port {settings.port})"
    elif stale_pid is not None:
        head = f"○ obsidian-mcp not running (stale pid {stale_pid} removed)"
    else:
        head = "○ obsidian-mcp not running"

    lines = [head, f"  vault:  {_compress_home(settings.vault.root.expanduser())}"]
    if note_count is not None:
        suffix = "" if running else " (last-known)"
        lines.append(f"  notes:  {note_count} indexed{suffix}")
    return "\n".join(lines)


def _index_path(settings: ServerSettings) -> Path:
    return settings.vault.root.expanduser() / ".obsidian-mcp" / _INDEX_FILENAME


def _count_notes(db_path: Path) -> int | None:
    if not db_path.exists():
        return None
    try:
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM note_meta").fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error:
        return None


def _compress_home(path: Path) -> str:
    home = str(Path.home())
    s = str(path)
    if s == home:
        return "~"
    if s.startswith(home + os.sep):
        return "~" + s[len(home):]
    return s


def show_logs(follow: bool) -> None:
    paths = daemon_paths()
    if follow:
        subprocess.run(["tail", "-f", str(paths.log_file)], check=False)
        return
    if not paths.log_file.exists():
        print(f"no log file at {paths.log_file}")
        return
    lines = paths.log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-_LOG_TAIL_LINES:]:
        print(line)


def read_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def remove_pid(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stop_pid(pid: int, timeout: float) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_process_alive(pid):
            return False
        time.sleep(0.1)
    if is_process_alive(pid):
        os.kill(pid, signal.SIGKILL)
        return True
    return False


def wait_for_health(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check_health(host, port):
            return True
        time.sleep(0.2)
    return False


def check_health(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1.0) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _health_host(host: str) -> str:
    if host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return host


def _effective_endpoint(host: str | None, port: int | None) -> tuple[str, int]:
    settings = load_settings()
    return host or settings.host, port if port is not None else settings.port
