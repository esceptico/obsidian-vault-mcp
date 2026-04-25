import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from obsidian_mcp.config import ServerSettings


class ConfigTests(unittest.TestCase):
    def test_settings_load_from_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "OBSIDIAN_MCP_VAULT_ROOT": tmp,
                "OBSIDIAN_MCP_AUTH_TOKEN": "secret",
                "OBSIDIAN_MCP_OPENAI_API_KEY": "openai-key",
                "OBSIDIAN_MCP_EMBEDDING_MODEL": "text-embedding-3-large",
                "OBSIDIAN_MCP_EMBEDDING_DIMENSIONS": "256",
            }
            with patch.dict(os.environ, env, clear=True):
                settings = ServerSettings()

        self.assertEqual(settings.vault.root, Path(tmp))
        self.assertEqual(settings.auth_token, "secret")
        self.assertEqual(settings.embeddings.api_key, "openai-key")
        self.assertEqual(settings.embeddings.model, "text-embedding-3-large")
        self.assertEqual(settings.embeddings.dimensions, 256)


if __name__ == "__main__":
    unittest.main()
