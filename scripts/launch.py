"""Launch the prepared application; --dev adds Vite and supported backend reload."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from scripts import preparation

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    development = "--dev" in sys.argv[1:]
    if not {"--help", "-h"}.intersection(sys.argv[1:]):
        expected = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
        if f"{sys.version_info.major}.{sys.version_info.minor}" != expected:
            raise SystemExit(f"运行环境需要 Python {expected}，请重新运行 scripts/setup.ps1 或 scripts/setup.sh。")
        if not preparation.is_prepared(ROOT, development=development):
            print("检测到运行依赖或网页需要更新，正在准备；完成后继续启动。", flush=True)
            result = subprocess.run([sys.executable, "-m", "scripts.setup"], cwd=ROOT)
            if result.returncode:
                raise SystemExit(result.returncode)
            # Newly installed dependencies can add .pth initialization at interpreter startup.
            command = [sys.executable, str(ROOT / "web_chat.py"), *sys.argv[1:]]
            if os.name == "nt":
                # Windows exec detaches the replacement; keep the launcher waiting for its child.
                with subprocess.Popen(command) as process:
                    try:
                        returncode = process.wait()
                    except KeyboardInterrupt:
                        returncode = process.wait()
                raise SystemExit(returncode)
            os.execv(sys.executable, command)
    from web_chat import main as run_web
    run_web()


if __name__ == "__main__":
    main()
