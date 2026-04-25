import os
import tempfile
import unittest
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

    async def test_compare_uses_constant_time_compare(self) -> None:
        import hmac
        with patch("obsidian_mcp.server.hmac.compare_digest", wraps=hmac.compare_digest) as spy:
            await StaticTokenVerifier("a").verify_token("b")
            spy.assert_called_once()


class AuthPostureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _settings(self, **overrides) -> ServerSettings:
        env = {"OBSIDIAN_MCP_VAULT_ROOT": self._tmp.name, **overrides}
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
