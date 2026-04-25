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

EMBEDDING_MAX_INPUT_TOKENS = 8000
"""Token cap fed to the embedding model. text-embedding-3-* hard-limits
inputs to 8192 tokens; we leave a 192-token safety margin. Notes longer
than this still get FTS-indexed in full — only the embedding input is
truncated. Chunking would preserve more signal but is out of scope."""

EMBEDDING_FALLBACK_ENCODING = "cl100k_base"
"""Tokenizer used when tiktoken doesn't recognize the configured model
(e.g. preview/experimental models). cl100k_base is the encoding shared
by gpt-3.5/4 and the text-embedding-3-* family."""


# --- Server ------------------------------------------------------------------
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
"""Hosts considered safe to bind without an auth token."""

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


# --- File watcher ------------------------------------------------------------
WATCHER_DEBOUNCE_SECONDS = 0.5
"""How long a path must be quiet before the watcher applies its index update.
Editors typically fire several FS events per save (tmp write + rename +
fsync); coalescing within this window collapses them to one update."""
