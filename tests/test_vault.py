import tempfile
import unittest
from pathlib import Path

from obsidian_mcp.config import VaultSettings
from obsidian_mcp.frontmatter import patch_frontmatter, split_frontmatter
from obsidian_mcp.vault import Vault


class FrontmatterTests(unittest.TestCase):
    def test_frontmatter_round_trip_patch(self) -> None:
        content = "---\ntags:\n- project\nstatus: old\n---\nBody"
        updated = patch_frontmatter(content, {"status": "active", "owner": "me"})
        frontmatter, body = split_frontmatter(updated)

        self.assertEqual(frontmatter["tags"], ["project"])
        self.assertEqual(frontmatter["status"], "active")
        self.assertEqual(frontmatter["owner"], "me")
        self.assertEqual(body, "Body")


class VaultTests(unittest.TestCase):
    def make_vault(self) -> tuple[tempfile.TemporaryDirectory[str], Vault]:
        tmp = tempfile.TemporaryDirectory()
        vault = Vault(VaultSettings(root=Path(tmp.name)))
        return tmp, vault

    def test_rejects_path_traversal(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            with self.assertRaises(ValueError):
                vault.read("../outside.md")

    def test_create_read_search(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            vault.create_note("Projects/Alpha", "This note discusses semantic search.", {"tags": ["ai"]})

            note = vault.read("Projects/Alpha.md")
            self.assertEqual(note["frontmatter"]["tags"], ["ai"])
            self.assertIn("semantic", note["body"])

            results = vault.search("semantic search", mode="bm25")
            self.assertEqual(results["hits"][0]["path"], "Projects/Alpha.md")
            self.assertTrue((Path(tmp.name) / ".obsidian-mcp" / "index.sqlite").exists())
            self.assertNotIn(".obsidian-mcp", {entry["path"] for entry in vault.list()})

            with self.assertRaises(ValueError):
                vault.create_note(".obsidian-mcp/manual", "nope")

    def test_rename_rewrites_wikilinks(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            vault.create_note("Old Note", "Body")
            vault.create_note("Ref", "See [[Old Note|alias]] and [[Old Note#Heading]].")

            result = vault.move_path("Old Note.md", "New Note.md", rewrite_links=True)
            ref = vault.read("Ref.md")

            self.assertEqual(result["rewritten_files"], 1)
            self.assertIn("[[New Note|alias]]", ref["content"])
            self.assertIn("[[New Note#Heading]]", ref["content"])

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
            vault.create_note("Note", "x")
            with self.assertRaises(ValueError):
                vault.delete_path(".obsidian-mcp", recursive=True, strategy="delete")

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

            from obsidian_mcp.vault import _tmp_name_for
            a = _tmp_name_for(Path(tmp.name) / "X.md")
            b = _tmp_name_for(Path(tmp.name) / "X.md")
            self.assertNotEqual(a.name, b.name)

    def test_folder_qualified_wikilinks_are_matched(self) -> None:
        tmp, vault = self.make_vault()
        with tmp:
            vault.create_note("Projects/Old Note", "Body")
            vault.create_note("Ref", "See [[Projects/Old Note]].")

            result = vault.move_path("Projects/Old Note.md", "Projects/New Note.md", rewrite_links=True)
            ref = vault.read("Ref.md")

            self.assertEqual(result["rewritten_files"], 1)
            self.assertIn("[[New Note]]", ref["content"])


if __name__ == "__main__":
    unittest.main()
