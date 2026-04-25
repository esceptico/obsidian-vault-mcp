import logging

# Silence obsidian_mcp INFO logs in test output; tests that need to assert on
# log output use unittest's assertLogs context which still captures regardless.
logging.getLogger("obsidian_mcp").addHandler(logging.NullHandler())
logging.getLogger("obsidian_mcp").setLevel(logging.WARNING)
