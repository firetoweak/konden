"""Launch the upstream OfficeCLI MCP with product-owned tools and updates."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


def main() -> None:
    runtime = Path(__file__).resolve().parents[2] / ".tools/runtime"
    executable = runtime / "officecli" / ("officecli.exe" if os.name == "nt" else "officecli")
    chrome = runtime / "chrome"
    environment = {
        **os.environ,
        "PATH": os.pathsep.join((str(chrome), os.environ.get("PATH", ""))),
        "OFFICECLI_SKIP_UPDATE": "1",
        "OFFICECLI_NO_AUTO_INSTALL": "1",
    }
    raise SystemExit(subprocess.call([str(executable), "mcp"], env=environment))


if __name__ == "__main__":
    main()
