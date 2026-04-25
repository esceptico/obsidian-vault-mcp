# --- Filesystem & writes -----------------------------------------------------
MAX_NOTE_BYTES = 5 * 1024 * 1024
"""Largest note body accepted by create_note / update_note (5 MiB)."""

MAX_FRONTMATTER_DEPTH = 16
"""Refuse YAML frontmatter nested deeper than this; protects ruamel.yaml.dump
from pathological inputs."""

TRASH_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
"""Strftime format for the timestamp prefix of files moved to .trash/."""


# --- Search ------------------------------------------------------------------
DEFAULT_SEARCH_LIMIT = 10
"""Number of hits returned by vault_search when the client doesn't specify."""

SCORE_DECIMALS = 6
"""Decimal places retained when rounding search scores (FTS, vector, hybrid)."""

RRF_K = 60
"""Reciprocal-rank-fusion constant; standard literature value used to combine
FTS and vector ranks in hybrid search."""

FTS_SNIPPET_LENGTH = 32
"""Token count for the snippet column returned by FTS5 snippet()."""


# --- OpenAI client -----------------------------------------------------------
OPENAI_MAX_RETRIES = 3
"""Retry count for transient OpenAI API failures (429 / 5xx)."""

EMBEDDING_TIMEOUT_SECONDS = 30.0
"""Per-request timeout for embedding calls."""


# --- Server ------------------------------------------------------------------
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
"""Hosts considered safe to bind without an auth token."""

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
