import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from obsidian_mcp.core.config import load_settings


STOP_TIMEOUT_SECONDS = 10.0
START_TIMEOUT_SECONDS = 10.0
LOG_TAIL_LINES = 200


@dataclass(frozen=True)
class DaemonPaths:
    state_dir: Path
    pid_file: Path
    log_file: Path


def daemon_paths() -> DaemonPaths:
    root = os.environ.get("OBSIDIAN_MCP_STATE_DIR")
    state_dir = Path(root).expanduser() if root else Path.home() / "Library" / "Application Support" / "obsidian-mcp"
    return DaemonPaths(
        state_dir=state_dir,
        pid_file=state_dir / "server.pid",
        log_file=state_dir / "server.log",
    )


def start_daemon(host: str | None, port: int | None, timeout: float = START_TIMEOUT_SECONDS) -> int:
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


def stop_daemon(timeout: float = STOP_TIMEOUT_SECONDS) -> str:
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
    paths = daemon_paths()
    pid = read_pid(paths.pid_file)
    if pid is None:
        return "stopped"
    if not is_process_alive(pid):
        remove_pid(paths.pid_file)
        return f"stopped (stale pid {pid})"
    health_host, health_port = _effective_endpoint(host, port)
    healthy = check_health(_health_host(health_host), health_port)
    return f"running, pid={pid}, {'healthy' if healthy else 'unhealthy'}"


def show_logs(follow: bool) -> None:
    paths = daemon_paths()
    if follow:
        subprocess.run(["tail", "-f", str(paths.log_file)], check=False)
        return
    if not paths.log_file.exists():
        print(f"no log file at {paths.log_file}")
        return
    lines = paths.log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-LOG_TAIL_LINES:]:
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
