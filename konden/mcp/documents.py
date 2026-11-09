"""Run the pinned upstream document MCP with product-local optional data."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def main() -> None:
    executable = Path(__file__).resolve().parents[2] / ".tools/runtime/anymd" / (
        "anymd.exe" if os.name == "nt" else "anymd"
    )
    environment = {
        **os.environ,
        "ANYMD_CACHE_DIR": sys.argv[1],
        "MCP_TRANSPORT": "stdio",
    }
    raise SystemExit(subprocess.call([str(executable), "mcp"], env=environment))


if __name__ == "__main__":
    main()
