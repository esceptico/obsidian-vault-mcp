# Critical + Important Reliability Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all 7 Critical and 12 Important findings from the initial code review of `obsidian-mcp` so the project is safe for non-trivial use.

**Architecture:** Tight TDD cycles per logical fix group. Each task adds the failing test first, then the minimal fix, then commits. Tests stay in `unittest` style (matching existing suite). Logging infrastructure is added once at the start so subsequent tasks can use it. The `Vault` keeps its public surface — internal helpers grow the safety guarantees.

**Tech Stack:** Python 3.14, `mcp` (FastMCP streamable-http), `pydantic-settings`, `ruamel.yaml` (`typ="rt"`), `openai`, `sqlite3` (FTS5), `unittest`.

**Test runner:** `uv run python -m unittest discover -s tests -v`

---

## Task 0: Baseline commit + test runner sanity

**Files:**
- N/A (commit only)

- [ ] **Step 1: Run the existing tests, confirm green**

Run: `uv run python -m unittest discover -s tests -v`
Expected: 4 test classes, all pass.

- [ ] **Step 2: Commit the current state as the baseline**

```bash
git commit -m "chore: initial obsidian-mcp baseline before reliability hardening"
```

---

## Task 1: Logging infrastructure (I7)

Add a single module-level logger pattern so subsequent tasks can record warnings (auth disabled, embedding fallbacks) and audit destructive calls.

**Files:**
- Create: `src/obsidian_mcp/logging.py`
- Modify: `src/obsidian_mcp/cli.py` (configure root logger on `serve`)
- Test: `tests/test_logging.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_logging.py
import logging
import unittest

from obsidian_mcp.logging import get_logger


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
```

- [ ] **Step 2: Run, verify it fails**

Run: `uv run python -m unittest tests.test_logging -v`
Expected: ImportError on `obsidian_mcp.logging`.

- [ ] **Step 3: Implement the helper**

```python
# src/obsidian_mcp/logging.py
import logging

ROOT = "obsidian_mcp"


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT}.{name}")


def configure_default_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger(ROOT)
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
```

- [ ] **Step 4: Wire it into the serve command**

In `src/obsidian_mcp/cli.py`, before `serve_main()` is invoked:

```python
from obsidian_mcp.logging import configure_default_logging
configure_default_logging()
```

- [ ] **Step 5: Run, verify it passes**

Run: `uv run python -m unittest tests.test_logging -v`
Expected: 2 PASSED.

- [ ] **Step 6: Commit**

```bash
git add src/obsidian_mcp/logging.py src/obsidian_mcp/cli.py tests/test_logging.py
git commit -m "feat: add namespaced logger helper and wire serve command"
```

---

## Task 2: Constant-time token compare + auth posture (C1, I1, I8)

Make the bearer-token check timing-safe, refuse to start unauthenticated when binding off-loopback, and require a public URL when auth is on (so OAuth metadata isn't pointing at the bind address).

**Files:**
- Modify: `src/obsidian_mcp/server.py` (`StaticTokenVerifier`, `create_mcp`)
- Modify: `src/obsidian_mcp/config.py` (validation hooks on `ServerSettings`)
- Test: `tests/test_server.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_server.py
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from obsidian_mcp.config import ServerSettings
from obsidian_mcp.server import StaticTokenVerifier, create_mcp


class StaticTokenVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_correct_token_returns_access_token(self) -> None:
        verifier = StaticTokenVerifier("s3cret")
        token = await verifier.verify_token("s3cret")
        assert token is not None
        self.assertEqual(token.scopes, ["vault"])

    async def test_wrong_token_returns_none(self) -> None:
        verifier = StaticTokenVerifier("s3cret")
        self.assertIsNone(await verifier.verify_token("nope"))

    async def test_compare_is_constant_time(self) -> None:
        # Direct check that compare_digest is in use; not a timing measurement.
        import hmac
        with patch("obsidian_mcp.server.hmac.compare_digest", wraps=hmac.compare_digest) as spy:
            await StaticTokenVerifier("a").verify_token("b")
            spy.assert_called_once()


class AuthPostureTests(unittest.TestCase):
    def _settings(self, **overrides) -> ServerSettings:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"OBSIDIAN_MCP_VAULT_ROOT": tmp, **overrides}
            with patch.dict(os.environ, env, clear=True):
                return ServerSettings()

    def test_non_loopback_without_token_refuses(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            create_mcp(self._settings(OBSIDIAN_MCP_HOST="0.0.0.0"))
        self.assertIn("AUTH", str(ctx.exception).upper())

    def test_loopback_without_token_starts(self) -> None:
        # Should not raise.
        create_mcp(self._settings(OBSIDIAN_MCP_HOST="127.0.0.1"))

    def test_auth_token_without_public_url_warns(self) -> None:
        with self.assertLogs("obsidian_mcp.server", level="WARNING") as captured:
            create_mcp(
                self._settings(
                    OBSIDIAN_MCP_HOST="0.0.0.0",
                    OBSIDIAN_MCP_AUTH_TOKEN="t",
                )
            )
        self.assertTrue(any("OBSIDIAN_MCP_PUBLIC_URL" in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run python -m unittest tests.test_server -v`
Expected: failures on each new test (compare_digest not used; create_mcp doesn't enforce or warn).

- [ ] **Step 3: Implement**

In `src/obsidian_mcp/server.py`:

```python
import hmac
import logging

from obsidian_mcp.logging import get_logger

log = get_logger("server")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class StaticTokenVerifier(TokenVerifier):
    def __init__(self, token: str):
        self.token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self.token):
            return None
        return AccessToken(token=token, client_id="obsidian-mcp-client", scopes=["vault"])


def create_mcp(settings: ServerSettings | None = None) -> FastMCP:
    settings = settings or load_settings()

    if not settings.auth_token and settings.host not in LOOPBACK_HOSTS:
        raise RuntimeError(
            "AUTH DISABLED: refusing to bind a non-loopback host without OBSIDIAN_MCP_AUTH_TOKEN. "
            "Set the token, or bind to 127.0.0.1."
        )

    if settings.auth_token and settings.public_url is None and settings.host not in LOOPBACK_HOSTS:
        log.warning(
            "auth_token is set but OBSIDIAN_MCP_PUBLIC_URL is not; OAuth metadata will advertise %s. "
            "Reverse-proxied deployments must set OBSIDIAN_MCP_PUBLIC_URL.",
            settings.resolved_public_url,
        )

    if not settings.auth_token:
        log.warning("auth_token not set; tools are exposed without authentication on %s", settings.host)

    auth = None
    verifier = None
    if settings.auth_token:
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(settings.resolved_public_url),
            resource_server_url=AnyHttpUrl(settings.resolved_public_url),
            required_scopes=["vault"],
        )
        verifier = StaticTokenVerifier(settings.auth_token)

    # ... (rest of body unchanged)
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run python -m unittest tests.test_server -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/obsidian_mcp/server.py tests/test_server.py
git commit -m "fix(security): constant-time token compare + require auth on non-loopback (C1,I1,I8)"
```

---

## Task 3: Durable atomic write (I9)

`_atomic_write` must fsync the tmp file before rename and use a unique tmp name to avoid concurrent collisions.

**Files:**
- Modify: `src/obsidian_mcp/vault.py` (`_atomic_write`)
- Test: `tests/test_vault.py` (extend)

- [ ] **Step 1: Add the failing test**

```python
# In tests/test_vault.py, inside VaultTests
def test_atomic_write_unique_tmp_and_fsync(self) -> None:
    import os
    from unittest.mock import patch

    tmp, vault = self.make_vault()
    with tmp:
        called: dict[str, int] = {}
        real_fsync = os.fsync

        def spy_fsync(fd: int) -> None:
            called["count"] = called.get("count", 0) + 1
            return real_fsync(fd)

        with patch("obsidian_mcp.vault.os.fsync", side_effect=spy_fsync):
            vault.create_note("Note", "hello")

        self.assertGreaterEqual(called.get("count", 0), 1)

        # Two writers should not stomp the same fixed-name tmp file.
        from obsidian_mcp.vault import _tmp_name_for
        a = _tmp_name_for(Path(tmp.name) / "X.md")
        b = _tmp_name_for(Path(tmp.name) / "X.md")
        self.assertNotEqual(a.name, b.name)
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run python -m unittest tests.test_vault.VaultTests.test_atomic_write_unique_tmp_and_fsync -v`
Expected: ImportError or fsync count == 0.

- [ ] **Step 3: Implement**

Replace `_atomic_write` in `src/obsidian_mcp/vault.py` and add a tmp-name helper:

```python
import os
import secrets

def _tmp_name_for(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")


# inside Vault:
def _atomic_write(self, path: Path, content: str) -> None:
    tmp = _tmp_name_for(path)
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(path)
    except BaseException:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run python -m unittest tests.test_vault -v`
Expected: all PASS, including the new test.

- [ ] **Step 5: Commit**

```bash
git add src/obsidian_mcp/vault.py tests/test_vault.py
git commit -m "fix(durability): fsync tmp + unique tmp name in atomic_write (I9)"
```

---

## Task 4: Frontmatter — CRLF support + preserve ruamel formatting (C6, C7)

Stop discarding ruamel's `CommentedMap` on parse, and accept CRLF frontmatter fences.

**Files:**
- Modify: `src/obsidian_mcp/frontmatter.py`
- Modify: `src/obsidian_mcp/vault.py` (`read` should still emit a plain dict for the wire)
- Modify: `src/obsidian_mcp/search.py` (`_stored_note` already json-dumps; ensure compat)
- Test: `tests/test_frontmatter.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_frontmatter.py
import unittest

from obsidian_mcp.frontmatter import patch_frontmatter, split_frontmatter


class FrontmatterTests(unittest.TestCase):
    def test_crlf_fences_are_recognized(self) -> None:
        content = "---\r\ntitle: Hello\r\n---\r\nbody\r\n"
        fm, body = split_frontmatter(content)
        self.assertEqual(fm["title"], "Hello")
        self.assertEqual(body, "body\r\n")

    def test_patch_preserves_comments_and_order(self) -> None:
        content = "---\n# leading comment\nb: 2\na: 1\n---\nbody"
        patched = patch_frontmatter(content, {"c": 3})
        # Order preserved (b before a, then c appended), comment retained.
        self.assertIn("# leading comment", patched)
        b_idx = patched.index("b: 2")
        a_idx = patched.index("a: 1")
        c_idx = patched.index("c: 3")
        self.assertLess(b_idx, a_idx)
        self.assertLess(a_idx, c_idx)

    def test_patch_with_none_deletes_key(self) -> None:
        content = "---\na: 1\nb: 2\n---\nbody"
        patched = patch_frontmatter(content, {"a": None})
        self.assertNotIn("a: 1", patched)
        self.assertIn("b: 2", patched)

    def test_malformed_yaml_raises(self) -> None:
        content = "---\n: bad : yaml :\n---\nbody"
        with self.assertRaises(ValueError):
            split_frontmatter(content)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run python -m unittest tests.test_frontmatter -v`
Expected: failures on CRLF and order/comment preservation.

- [ ] **Step 3: Implement**

```python
# src/obsidian_mcp/frontmatter.py
from io import StringIO
from typing import Any, MutableMapping

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError


_yaml = YAML(typ="rt")
_yaml.default_flow_style = False
_yaml.preserve_quotes = True


_OPEN_FENCES = ("---\n", "---\r\n")


def _find_close(markdown: str, start: int) -> int:
    for needle in ("\n---", "\r\n---"):
        idx = markdown.find(needle, start)
        if idx != -1:
            return idx + len(needle)
    return -1


def _parse_yaml(raw: str) -> MutableMapping[str, Any]:
    try:
        parsed = _yaml.load(raw)
    except YAMLError as exc:
        raise ValueError(f"Invalid YAML frontmatter: {exc}") from exc
    if parsed is None:
        return CommentedMap()
    if not isinstance(parsed, MutableMapping):
        raise ValueError("YAML frontmatter must be a mapping")
    return parsed


def split_frontmatter(markdown: str) -> tuple[dict[str, Any], str]:
    fm, body = split_frontmatter_raw(markdown)
    return dict(fm), body


def split_frontmatter_raw(markdown: str) -> tuple[MutableMapping[str, Any], str]:
    opener = next((f for f in _OPEN_FENCES if markdown.startswith(f)), None)
    if opener is None:
        return CommentedMap(), markdown

    content_start = len(opener)
    close_end = _find_close(markdown, content_start)
    if close_end == -1:
        return CommentedMap(), markdown
    if close_end < len(markdown) and markdown[close_end] not in "\r\n":
        return CommentedMap(), markdown

    raw_yaml = markdown[content_start : close_end - len("---")].rstrip("\r\n")
    body = markdown[close_end:].lstrip("\n")
    if body.startswith("\r"):
        body = body[1:]
    if not raw_yaml.strip():
        return CommentedMap(), body
    return _parse_yaml(raw_yaml), body


def render_frontmatter(frontmatter: MutableMapping[str, Any] | dict[str, Any], body: str) -> str:
    normalized_body = body.lstrip("\r\n")
    if not frontmatter:
        return normalized_body
    stream = StringIO()
    _yaml.dump(frontmatter, stream)
    return f"---\n{stream.getvalue()}---\n{normalized_body}"


def patch_frontmatter(markdown: str, patch: dict[str, Any]) -> str:
    current, body = split_frontmatter_raw(markdown)
    for key, value in patch.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    return render_frontmatter(current, body)
```

Update `src/obsidian_mcp/vault.py` `update_note` to call the raw splitter when re-serializing so CommentedMap is preserved through the round-trip:

```python
from obsidian_mcp.frontmatter import (
    patch_frontmatter, render_frontmatter, split_frontmatter, split_frontmatter_raw,
)

def update_note(self, path, content=None, frontmatter_patch=None):
    note_path = self.resolve(path)
    if not note_path.is_file():
        raise ValueError(f"Not a file: {path}")
    existing = note_path.read_text(encoding="utf-8")
    _, body = split_frontmatter(existing)
    next_content = existing
    if content is not None:
        current_fm, _ = split_frontmatter_raw(existing)
        next_content = render_frontmatter(current_fm, content)
    if frontmatter_patch:
        next_content = patch_frontmatter(next_content, frontmatter_patch)
    if next_content != existing:
        self._atomic_write(note_path, next_content)
        self.invalidate_index()
    return {"ok": True, "path": self.relative(note_path), "changed": next_content != existing, "previous_body": body}
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run python -m unittest tests.test_frontmatter tests.test_vault -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/obsidian_mcp/frontmatter.py src/obsidian_mcp/vault.py tests/test_frontmatter.py
git commit -m "fix: preserve ruamel formatting and accept CRLF frontmatter (C6,C7)"
```

---

## Task 5: Ignored-path semantics + delete safety (C3, I12)

Anchor ignored-root checks to `parts[0]`, ignore-aware empty-dir check, refuse to delete `.obsidian-mcp`.

**Files:**
- Modify: `src/obsidian_mcp/vault.py` (`_is_ignored_path`, `delete_path`)
- Test: `tests/test_vault.py` (extend)

- [ ] **Step 1: Add failing tests**

```python
# In tests/test_vault.py, inside VaultTests
def test_nested_dot_trash_is_visible(self) -> None:
    tmp, vault = self.make_vault()
    with tmp:
        nested = Path(tmp.name) / "Projects" / ".trash" / "note.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("hello", encoding="utf-8")
        listed = vault.list("Projects/.trash")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["path"], "Projects/.trash/note.md")

def test_top_level_trash_is_hidden(self) -> None:
    tmp, vault = self.make_vault()
    with tmp:
        (Path(tmp.name) / ".trash").mkdir()
        (Path(tmp.name) / ".trash" / "x.md").write_text("x", encoding="utf-8")
        self.assertNotIn(".trash", {entry["path"] for entry in vault.list()})

def test_delete_refuses_obsidian_mcp(self) -> None:
    tmp, vault = self.make_vault()
    with tmp:
        # Create a note so the index dir exists.
        vault.create_note("Note", "x")
        with self.assertRaises(ValueError):
            vault.delete_path(".obsidian-mcp", recursive=True, strategy="delete")

def test_delete_treats_dir_with_only_ignored_children_as_empty(self) -> None:
    tmp, vault = self.make_vault()
    with tmp:
        target = Path(tmp.name) / "EmptyButHasIndex"
        target.mkdir()
        (target / ".obsidian-mcp").mkdir()
        # Should NOT require recursive=True since user-visible content is empty.
        vault.delete_path("EmptyButHasIndex", recursive=False, strategy="delete")
        self.assertFalse(target.exists())
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run python -m unittest tests.test_vault -v`
Expected: 4 new failures.

- [ ] **Step 3: Implement**

```python
# src/obsidian_mcp/vault.py
def _is_ignored_path(self, path: Path) -> bool:
    parts = path.relative_to(self.root).parts
    return bool(parts) and parts[0] in {self.settings.trash_path, ".obsidian-mcp"}

def delete_path(self, path: str, recursive: bool = False, strategy: str = "trash") -> dict[str, Any]:
    target = self.resolve(path)
    if target == self.root:
        raise ValueError("Refusing to delete the vault root")
    if self._is_ignored_path(target):
        raise ValueError(f"Refusing to delete reserved path: {path}")
    if not target.exists():
        raise FileNotFoundError(path)

    if target.is_dir():
        visible = [c for c in target.iterdir() if not self._is_ignored_path(c)]
        if visible and not recursive:
            raise ValueError("Directory is not empty; pass recursive=True")

    # ... (rest of body unchanged)
```

Also add a logger usage:

```python
log = get_logger("vault")  # near the top of vault.py
# In delete_path, before each branch:
log.info("delete_path path=%s strategy=%s recursive=%s", path, strategy, recursive)
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run python -m unittest tests.test_vault -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/obsidian_mcp/vault.py tests/test_vault.py
git commit -m "fix: anchor ignored-root checks to parts[0]; refuse to delete .obsidian-mcp (C3,I12)"
```

---

## Task 6: Trash collision-safe destination (C2)

Suffix-on-collision when moving to `.trash`.

**Files:**
- Modify: `src/obsidian_mcp/vault.py` (`delete_path` trash branch + helper)
- Test: `tests/test_vault.py` (extend)

- [ ] **Step 1: Add failing test**

```python
# In tests/test_vault.py, inside VaultTests
def test_trash_does_not_overwrite_same_second(self) -> None:
    from unittest.mock import patch
    from datetime import datetime, timezone

    tmp, vault = self.make_vault()
    with tmp:
        vault.create_note("A", "first")
        # Re-create then delete twice with a frozen clock.
        fixed = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
        with patch("obsidian_mcp.vault.datetime") as dt:
            dt.now.return_value = fixed
            dt.fromtimestamp = datetime.fromtimestamp
            r1 = vault.delete_path("A.md", strategy="trash")
            vault.create_note("A", "second")
            r2 = vault.delete_path("A.md", strategy="trash")

        self.assertNotEqual(r1["trashed_to"], r2["trashed_to"])
        trash_dir = Path(tmp.name) / ".trash"
        self.assertEqual(len(list(trash_dir.iterdir())), 2)
```

- [ ] **Step 2: Run, verify failure**

Expected: AssertionError — second move overwrote the first.

- [ ] **Step 3: Implement**

```python
# src/obsidian_mcp/vault.py
def _unique_trash_destination(self, trash_dir: Path, target_name: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = trash_dir / f"{timestamp}-{target_name}"
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = base.with_name(f"{base.stem}-{suffix}{base.suffix}")
        suffix += 1
    return candidate
```

In the trash branch of `delete_path`:

```python
destination = self._unique_trash_destination(trash, target.name)
shutil.move(str(target), str(destination))
```

- [ ] **Step 4: Run, verify pass**

Run: `uv run python -m unittest tests.test_vault -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/obsidian_mcp/vault.py tests/test_vault.py
git commit -m "fix: trash destination is unique within the same second (C2)"
```

---

## Task 7: Move atomicity + folder-qualified rewrite (C4, C5)

Pre-compute every link rewrite *before* the move; emit folder-qualified replacement when the source link was qualified, when stems collide, or when a same-stem note exists elsewhere.

**Files:**
- Modify: `src/obsidian_mcp/obsidian.py` (`rewrite_wikilink_targets` accepts a per-match replacement)
- Modify: `src/obsidian_mcp/vault.py` (`move_path`, helpers)
- Test: `tests/test_vault.py` (extend)

- [ ] **Step 1: Add failing tests**

```python
# In tests/test_vault.py, inside VaultTests
def test_rename_preserves_folder_qualifier_on_collision(self) -> None:
    tmp, vault = self.make_vault()
    with tmp:
        vault.create_note("Projects/Old", "Body")
        vault.create_note("Archive/New", "Other body")
        vault.create_note("Ref", "See [[Projects/Old]] and [[Archive/New]].")
        result = vault.move_path("Projects/Old.md", "Projects/New.md")
        ref = vault.read("Ref.md")

        self.assertEqual(result["rewritten_files"], 1)
        # Replacement must remain unambiguous.
        self.assertIn("[[Projects/New]]", ref["content"])
        self.assertIn("[[Archive/New]]", ref["content"])

def test_rename_preserves_folder_qualifier_when_source_was_qualified(self) -> None:
    tmp, vault = self.make_vault()
    with tmp:
        vault.create_note("Projects/Old", "Body")
        vault.create_note("Ref", "See [[Projects/Old]].")
        vault.move_path("Projects/Old.md", "Projects/Renamed.md")
        ref = vault.read("Ref.md")
        self.assertIn("[[Projects/Renamed]]", ref["content"])

def test_move_rolls_back_on_rewrite_failure(self) -> None:
    from unittest.mock import patch

    tmp, vault = self.make_vault()
    with tmp:
        vault.create_note("Old", "body")
        vault.create_note("Ref", "[[Old]]")
        with patch.object(vault, "_atomic_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                vault.move_path("Old.md", "New.md")
        # Original file still in place; nothing renamed.
        self.assertTrue((Path(tmp.name) / "Old.md").exists())
        self.assertFalse((Path(tmp.name) / "New.md").exists())
        ref = (Path(tmp.name) / "Ref.md").read_text(encoding="utf-8")
        self.assertIn("[[Old]]", ref)
```

- [ ] **Step 2: Run, verify failure**

Expected: 3 failing tests (collision, qualifier preservation, rollback).

- [ ] **Step 3: Refactor `rewrite_wikilink_targets` to accept a replacement function**

```python
# src/obsidian_mcp/obsidian.py
from typing import Callable

def rewrite_wikilink_targets(
    markdown: str,
    old_names: set[str],
    replacement_for: str | Callable[[str], str],
) -> str:
    if isinstance(replacement_for, str):
        plain_new = replacement_for
        replacement_for = lambda _: plain_new

    def replace(match: re.Match[str]) -> str:
        target, alias, heading, block_id = parse_wikilink_inner(match.group("inner"))
        if target not in old_names:
            return match.group(0)
        suffix = ""
        if block_id:
            suffix = f"#^{block_id}"
        elif heading:
            suffix = f"#{heading}"
        alias_part = f"|{alias}" if alias else ""
        embed = "!" if match.group("embed") else ""
        return f"{embed}[[{replacement_for(target)}{suffix}{alias_part}]]"

    return WIKILINK_RE.sub(replace, markdown)
```

- [ ] **Step 4: Implement collision-aware, transactional move**

```python
# src/obsidian_mcp/vault.py
def move_path(self, source: str, destination: str, rewrite_links: bool = True, overwrite: bool = False) -> dict[str, Any]:
    src = self.resolve(source)
    dst = self.resolve_for_write(destination)
    if not src.exists():
        raise FileNotFoundError(source)
    if dst.exists() and not overwrite:
        raise FileExistsError(f"Destination already exists: {destination}")

    old_names = self._link_names_for(src)
    is_note_rename = (
        rewrite_links and src.suffix == ".md" and dst.suffix == ".md" and bool(old_names)
    )

    pending_rewrites: list[tuple[Path, str]] = []
    if is_note_rename:
        new_bare = dst.stem
        new_qualified = Path(self._relative_str(dst)).with_suffix("").as_posix()
        same_stem_exists = any(
            other.is_file()
            and other.suffix == ".md"
            and other.stem == new_bare
            and other.resolve() != dst.resolve()
            and not self._is_ignored_path(other)
            for other in self.root.rglob("*.md")
        )

        def replacement_for(matched_old: str) -> str:
            # Preserve the folder qualifier when the source link was qualified
            # or when a bare-stem replacement would be ambiguous.
            if "/" in matched_old or same_stem_exists:
                return new_qualified
            return new_bare

        for path in self.root.rglob("*.md"):
            if not path.is_file() or self._is_ignored_path(path):
                continue
            original = path.read_text(encoding="utf-8")
            updated = rewrite_wikilink_targets(original, old_names, replacement_for)
            if updated != original:
                pending_rewrites.append((path, updated))

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    try:
        for path, new_content in pending_rewrites:
            self._atomic_write(path, new_content)
    except BaseException:
        # Best-effort rollback of the rename; pending rewrites were never applied yet
        # because we fail on the first one.
        try:
            shutil.move(str(dst), str(src))
        except Exception:
            log.exception("rollback of move %s -> %s failed", source, destination)
        raise

    self.invalidate_index()
    log.info("move_path source=%s destination=%s rewritten=%d", source, self.relative(dst), len(pending_rewrites))
    return {
        "ok": True,
        "source": source,
        "destination": self.relative(dst),
        "rewritten_files": len(pending_rewrites),
    }


def _relative_str(self, path: Path) -> str:
    """Like relative() but does not require the path to exist."""
    try:
        return path.resolve().relative_to(self.root).as_posix()
    except FileNotFoundError:
        return path.relative_to(self.root).as_posix()
```

- [ ] **Step 5: Run, verify pass**

Run: `uv run python -m unittest tests.test_vault -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/obsidian_mcp/obsidian.py src/obsidian_mcp/vault.py tests/test_vault.py
git commit -m "fix: collision-aware folder-qualified link rewrite + rollback move on failure (C4,C5)"
```

---

## Task 8: Concurrency lock around vault writes and index rebuild (I2)

Single `RLock` on the `Vault` guarding all mutating operations and the index rebuild.

**Files:**
- Modify: `src/obsidian_mcp/vault.py`
- Test: `tests/test_vault.py` (extend)

- [ ] **Step 1: Add failing test**

```python
# In tests/test_vault.py, inside VaultTests
def test_concurrent_searches_only_rebuild_once(self) -> None:
    import threading
    tmp, vault = self.make_vault()
    with tmp:
        for i in range(5):
            vault.create_note(f"N{i}", f"hello {i}")
        rebuild_calls = {"n": 0}
        original = vault._index.rebuild

        def counting_rebuild(notes):
            rebuild_calls["n"] += 1
            return original(notes)

        vault._index.rebuild = counting_rebuild  # type: ignore[assignment]
        vault.invalidate_index()

        threads = [threading.Thread(target=lambda: vault.search("hello", mode="bm25")) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()

        self.assertEqual(rebuild_calls["n"], 1)
```

- [ ] **Step 2: Run, verify failure**

Expected: `rebuild_calls["n"] > 1`.

- [ ] **Step 3: Implement**

In `src/obsidian_mcp/vault.py`:

```python
import threading

class Vault:
    def __init__(self, settings, embeddings=None):
        # ... existing init ...
        self._lock = threading.RLock()

    def search(self, query, limit=10, mode="hybrid"):
        with self._lock:
            if self._index_dirty:
                self._index.rebuild(
                    [IndexedNote(path=p, content=c) for p, c in self._markdown_files().items()]
                )
                self._index_dirty = False
        return self._index.search(query=query, limit=limit, mode=mode)
```

Wrap `create_note`, `update_note`, `move_path`, `delete_path`, `_rewrite_links` (now folded into `move_path`) in `with self._lock:` blocks. Also `invalidate_index`.

- [ ] **Step 4: Run, verify pass**

Run: `uv run python -m unittest tests.test_vault -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/obsidian_mcp/vault.py tests/test_vault.py
git commit -m "fix(concurrency): RLock around vault writes and index rebuild (I2)"
```

---

## Task 9: Incremental indexing + stale-embedding cleanup (I3, I4)

Replace the full-vault rebuild on every write with per-note upserts. Update `delete_stale_embeddings` to also drop rows whose `content_hash` differs.

**Files:**
- Modify: `src/obsidian_mcp/store.py` (add `upsert_note`, `delete_note`, hash-aware stale cleanup)
- Modify: `src/obsidian_mcp/search.py` (`SearchIndex.upsert_note`, `delete_note`)
- Modify: `src/obsidian_mcp/vault.py` (call `upsert_note` / `delete_note` on writes; reserve `rebuild` for `vault_reindex`)
- Test: `tests/test_store.py` and `tests/test_search.py` (extend)

- [ ] **Step 1: Add failing tests**

```python
# In tests/test_store.py
def test_upsert_then_delete_round_trip(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = SearchStore(Path(tmp) / "i.sqlite")
        store.upsert_note(StoredNote(
            path="A.md", title="A", frontmatter_json="{}", body="alpha",
            tags_text="", search_text="A alpha", content_hash="h1",
        ))
        hits = store.search_fts('"alpha"', 10)
        self.assertEqual(hits[0].path, "A.md")
        store.delete_note("A.md")
        self.assertEqual(store.search_fts('"alpha"', 10), [])

def test_replace_notes_evicts_changed_embeddings(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = SearchStore(Path(tmp) / "i.sqlite")
        note = StoredNote(
            path="A.md", title="A", frontmatter_json="{}", body="x", tags_text="",
            search_text="A x", content_hash="h1",
        )
        store.replace_notes([note])
        store.upsert_embeddings([note], [[0.1, 0.2]], "m", 2)
        # New body, new hash.
        new = StoredNote(**{**note.__dict__, "body": "y", "search_text": "A y", "content_hash": "h2"})
        store.replace_notes([new])
        # Embedding for A.md must be gone since the hash no longer matches.
        self.assertEqual(store.embedding_metadata("m", 2), {})
```

```python
# In tests/test_search.py, add a test that incremental upsert is what runs after a single-file change.
def test_upsert_does_not_full_rebuild(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        from obsidian_mcp.search import SearchIndex
        from obsidian_mcp.config import EmbeddingSettings
        index = SearchIndex(Path(tmp) / "i.sqlite", EmbeddingSettings())
        index.rebuild([IndexedNote(path="A.md", content="alpha")])
        index.upsert_note(IndexedNote(path="B.md", content="beta"))
        hits = index.search("beta", mode="bm25")
        paths = [hit["path"] for hit in hits["hits"]]
        self.assertIn("B.md", paths)
        self.assertIn("A.md", [hit["path"] for hit in index.search("alpha", mode="bm25")["hits"]])
```

- [ ] **Step 2: Run, verify failures**

Expected: `upsert_note`/`delete_note` missing; embedding cleanup retains stale row.

- [ ] **Step 3: Implement store changes**

```python
# src/obsidian_mcp/store.py
DELETE_NOTE = "DELETE FROM notes WHERE path = ?"
DELETE_EMBEDDING = "DELETE FROM note_embeddings WHERE path = ?"
DELETE_EMBEDDING_BY_HASH = """
DELETE FROM note_embeddings
WHERE path = ? AND content_hash != ?
"""

class SearchStore:
    def upsert_note(self, note: StoredNote) -> None:
        with self.connect() as connection:
            connection.execute(DELETE_NOTE, (note.path,))
            connection.execute(
                INSERT_NOTE,
                (note.path, note.title, note.frontmatter_json, note.body, note.tags_text),
            )
            connection.execute(DELETE_EMBEDDING_BY_HASH, (note.path, note.content_hash))

    def delete_note(self, path: str) -> None:
        with self.connect() as connection:
            connection.execute(DELETE_NOTE, (path,))
            connection.execute(DELETE_EMBEDDING, (path,))

    def replace_notes(self, notes: list[StoredNote]) -> None:
        paths_by_hash = {note.path: note.content_hash for note in notes}
        with self.connect() as connection:
            connection.execute(DELETE_NOTES)
            for note in notes:
                connection.execute(
                    INSERT_NOTE,
                    (note.path, note.title, note.frontmatter_json, note.body, note.tags_text),
                )
            self._evict_stale_embeddings(connection, paths_by_hash)

    def _evict_stale_embeddings(self, connection, paths_by_hash: dict[str, str]) -> None:
        if not paths_by_hash:
            connection.execute(DELETE_ALL_EMBEDDINGS)
            return
        placeholders = ",".join("?" for _ in paths_by_hash)
        # Drop rows whose path is not in the new set.
        connection.execute(
            f"DELETE FROM note_embeddings WHERE path NOT IN ({placeholders})",
            tuple(paths_by_hash),
        )
        # Drop rows whose content_hash no longer matches.
        for path, h in paths_by_hash.items():
            connection.execute(DELETE_EMBEDDING_BY_HASH, (path, h))
```

(Drop the old `delete_stale_embeddings`; callers updated below.)

- [ ] **Step 4: Implement search index changes**

```python
# src/obsidian_mcp/search.py
class SearchIndex:
    def upsert_note(self, note: IndexedNote) -> None:
        record = _stored_note(note)
        self.store.upsert_note(record)
        if self.embeddings.enabled:
            self._embed_missing([record])

    def delete_note(self, path: str) -> None:
        self.store.delete_note(path)
```

- [ ] **Step 5: Implement vault changes**

In `src/obsidian_mcp/vault.py`:

```python
def create_note(self, path, content="", frontmatter=None, overwrite=False):
    with self._lock:
        note_path = self.resolve_for_write(_ensure_md(path))
        if note_path.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing note: {path}")
        note_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = render_frontmatter(frontmatter or {}, content)
        self._atomic_write(note_path, rendered)
        rel = self.relative(note_path)
        self._index.upsert_note(IndexedNote(path=rel, content=rendered))
        log.info("create_note path=%s", rel)
        return {"ok": True, "path": rel}

def update_note(self, path, content=None, frontmatter_patch=None):
    with self._lock:
        # ... existing computation of next_content ...
        if next_content != existing:
            self._atomic_write(note_path, next_content)
            rel = self.relative(note_path)
            self._index.upsert_note(IndexedNote(path=rel, content=next_content))
        return {...}

def delete_path(self, path, recursive=False, strategy="trash"):
    with self._lock:
        # ... existing safety checks ...
        affected: list[str] = []
        if target.is_file() and target.suffix == ".md":
            affected = [self.relative(target)]
        elif target.is_dir():
            affected = [
                self.relative(p)
                for p in target.rglob("*.md")
                if p.is_file() and not self._is_ignored_path(p)
            ]
        # ... move/delete ...
        for p in affected:
            self._index.delete_note(p)
        return result

def move_path(self, ...):
    with self._lock:
        # ... existing logic ...
        # After successful move + rewrites:
        old_rel = source.lstrip("/")
        if src.suffix == ".md":
            self._index.delete_note(old_rel)
        if dst.is_file() and dst.suffix == ".md":
            self._index.upsert_note(IndexedNote(path=self.relative(dst), content=dst.read_text(encoding="utf-8")))
        for p, content in pending_rewrites:
            self._index.upsert_note(IndexedNote(path=self.relative(p), content=content))
        # No more invalidate_index() except on vault_reindex.
        return result
```

`vault_reindex` keeps the full rebuild path:

```python
def reindex(self) -> None:
    with self._lock:
        self._index_dirty = True
```

`search()` only rebuilds when `_index_dirty` is set, which now only happens on explicit `vault_reindex` (or on first run when the index file is empty — see Step 6).

- [ ] **Step 6: First-run initialization**

In `Vault.__init__`, set `_index_dirty = True` only when the SQLite file is empty (no notes). Otherwise leave it `False`. Use a `count_notes()` helper on `SearchStore`:

```python
# store.py
def count_notes(self) -> int:
    with self.connect() as connection:
        return connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0]

# vault.py
self._index_dirty = self._index.store.count_notes() == 0
```

- [ ] **Step 7: Run all tests, verify pass**

Run: `uv run python -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/obsidian_mcp/store.py src/obsidian_mcp/search.py src/obsidian_mcp/vault.py tests/test_store.py tests/test_search.py
git commit -m "perf,fix: incremental indexing + evict stale embeddings on hash change (I3,I4)"
```

---

## Task 10: Better FTS5 error & cached OpenAI client with retries (I5, I6)

Detect missing FTS5 at table creation; cache the OpenAI client and let the SDK retry.

**Files:**
- Modify: `src/obsidian_mcp/store.py` (`initialize`)
- Modify: `src/obsidian_mcp/search.py` (cached client, retries)
- Test: `tests/test_store.py` (extend), `tests/test_search.py` (extend)

- [ ] **Step 1: Add failing tests**

```python
# In tests/test_store.py
def test_search_fts_propagates_real_syntax_errors(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = SearchStore(Path(tmp) / "i.sqlite")
        store.upsert_note(StoredNote(
            path="A.md", title="A", frontmatter_json="{}", body="x", tags_text="",
            search_text="A x", content_hash="h",
        ))
        # Reserved-word-only query without our quoting helper triggers an FTS5 syntax error.
        with self.assertRaises(sqlite3.OperationalError):
            store.search_fts("AND", 1)
```

```python
# In tests/test_search.py
def test_openai_client_is_reused(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        from obsidian_mcp.search import SearchIndex
        from obsidian_mcp.config import EmbeddingSettings
        idx = SearchIndex(Path(tmp) / "i.sqlite", EmbeddingSettings(api_key="k", model="text-embedding-3-small"))
        c1 = idx._client()
        c2 = idx._client()
        self.assertIs(c1, c2)
```

- [ ] **Step 2: Run, verify failure**

Expected: search masks FTS5 syntax error as RuntimeError; `_client` not present / not memoized.

- [ ] **Step 3: Implement store change**

```python
# src/obsidian_mcp/store.py
def initialize(self) -> None:
    with self.connect() as connection:
        connection.execute(PRAGMA_JOURNAL_MODE)
        connection.execute(PRAGMA_SYNCHRONOUS)
        try:
            connection.execute(CREATE_NOTES_TABLE)
        except sqlite3.OperationalError as exc:
            raise RuntimeError("This Python SQLite build does not include FTS5 support") from exc
        connection.execute(CREATE_EMBEDDINGS_TABLE)

def search_fts(self, query: str, limit: int) -> list[FtsHit]:
    with self.connect() as connection:
        rows = connection.execute(SEARCH_FTS, (query, limit)).fetchall()
    return [...]
```

- [ ] **Step 4: Implement search change**

```python
# src/obsidian_mcp/search.py
class SearchIndex:
    def __init__(self, database_path, embeddings):
        self.embeddings = embeddings
        self.store = SearchStore(database_path)
        self._openai_client: OpenAI | None = None

    def _client(self) -> OpenAI:
        if self._openai_client is None:
            self._openai_client = OpenAI(api_key=self.embeddings.api_key, max_retries=3, timeout=30.0)
        return self._openai_client

    def _embed_texts(self, texts):
        if not texts:
            return []
        request: dict = {
            "model": self.embeddings.model,
            "input": texts,
            "encoding_format": "float",
        }
        if self.embeddings.dimensions is not None:
            request["dimensions"] = self.embeddings.dimensions
        response = self._client().embeddings.create(**request)
        return [item.embedding for item in sorted(response.data, key=lambda i: i.index)]
```

- [ ] **Step 5: Run, verify pass**

Run: `uv run python -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/obsidian_mcp/store.py src/obsidian_mcp/search.py tests/test_store.py tests/test_search.py
git commit -m "fix: detect missing FTS5 at init; reuse OpenAI client with retries (I5,I6)"
```

---

## Task 11: Input validation + missing-path errors (I10, I11)

Constrain `mode` and `strategy` to `Literal[...]` so MCP tool schemas advertise the enum, cap content/frontmatter size, and make `vault_backlinks` raise on a missing path.

**Files:**
- Modify: `src/obsidian_mcp/server.py` (tool signatures)
- Modify: `src/obsidian_mcp/vault.py` (`backlinks`, size caps)
- Test: `tests/test_vault.py`, `tests/test_server.py` (extend)

- [ ] **Step 1: Add failing tests**

```python
# In tests/test_vault.py, inside VaultTests
def test_backlinks_missing_path_raises(self) -> None:
    tmp, vault = self.make_vault()
    with tmp:
        with self.assertRaises(FileNotFoundError):
            vault.backlinks("does-not-exist.md")

def test_create_note_rejects_oversized_content(self) -> None:
    from obsidian_mcp.vault import MAX_NOTE_BYTES
    tmp, vault = self.make_vault()
    with tmp:
        with self.assertRaises(ValueError):
            vault.create_note("Big", "x" * (MAX_NOTE_BYTES + 1))
```

- [ ] **Step 2: Run, verify failure**

Expected: `backlinks` returned `{...}` instead of raising; size cap missing.

- [ ] **Step 3: Implement vault changes**

```python
# src/obsidian_mcp/vault.py
MAX_NOTE_BYTES = 5 * 1024 * 1024  # 5 MiB
MAX_FRONTMATTER_DEPTH = 16


def _check_size(content: str) -> None:
    if len(content.encode("utf-8")) > MAX_NOTE_BYTES:
        raise ValueError(f"Note content exceeds {MAX_NOTE_BYTES} bytes")


def _check_frontmatter_depth(value: Any, depth: int = 0) -> None:
    if depth > MAX_FRONTMATTER_DEPTH:
        raise ValueError("Frontmatter exceeds max depth")
    if isinstance(value, dict):
        for v in value.values():
            _check_frontmatter_depth(v, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _check_frontmatter_depth(v, depth + 1)


# In create_note / update_note: call _check_size(content) and _check_frontmatter_depth(frontmatter or {}).

def backlinks(self, path: str) -> dict[str, Any]:
    target = self.resolve(path)
    if not target.exists():
        raise FileNotFoundError(path)
    # ... existing logic ...
```

- [ ] **Step 4: Implement server tool signature changes**

```python
# src/obsidian_mcp/server.py
from typing import Literal

SearchMode = Literal["bm25", "hybrid", "vector"]
DeleteStrategy = Literal["trash", "delete"]

@mcp.tool()
def vault_search(query: str, limit: int = 10, mode: SearchMode = "hybrid") -> dict[str, Any]:
    ...

@mcp.tool()
def vault_delete_path(path: str, recursive: bool = False, strategy: DeleteStrategy = "trash") -> dict[str, Any]:
    ...
```

- [ ] **Step 5: Run all tests**

Run: `uv run python -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/obsidian_mcp/vault.py src/obsidian_mcp/server.py tests/test_vault.py
git commit -m "fix: typed tool enums, content size caps, missing-path error on backlinks (I10,I11)"
```

---

## Task 12: Final verification + smoke run

- [ ] **Step 1: Run the entire test suite**

Run: `uv run python -m unittest discover -s tests -v`
Expected: all PASS, no warnings other than expected ones (auth-disabled banner).

- [ ] **Step 2: Boot the server in a temporary vault and confirm it starts**

```bash
TMP=$(mktemp -d)
OBSIDIAN_MCP_VAULT_ROOT="$TMP" \
OBSIDIAN_MCP_AUTH_TOKEN="t" \
OBSIDIAN_MCP_HOST=127.0.0.1 \
OBSIDIAN_MCP_PORT=8765 \
uv run obsidian-mcp serve &
PID=$!
sleep 1
curl -sf http://127.0.0.1:8765/mcp >/dev/null && echo "MCP listening"
kill $PID
```
Expected: "MCP listening".

- [ ] **Step 3: Boot without a token on 0.0.0.0 and confirm it refuses**

```bash
OBSIDIAN_MCP_VAULT_ROOT="$TMP" OBSIDIAN_MCP_HOST=0.0.0.0 \
uv run obsidian-mcp serve 2>&1 | head -5
```
Expected: traceback containing `AUTH DISABLED`.

- [ ] **Step 4: Final commit if anything changed**

```bash
git status
# If any cleanup needed:
git commit -am "chore: post-fix cleanup"
```
