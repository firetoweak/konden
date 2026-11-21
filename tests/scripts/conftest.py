import hashlib
import json
from pathlib import Path
import shutil
import site
import sys
import venv

import pytest

from scripts import preparation, runtime_tools


@pytest.fixture
def prepared_source(tmp_path):
    source = Path(__file__).resolve().parents[2]
    root = tmp_path / "application"
    shutil.copytree(source / "scripts", root / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
    shutil.copyfile(source / "web_chat.py", root / "web_chat.py")
    for name in (".python-version", "requirements.lock"):
        shutil.copyfile(source / name, root / name)
    (root / "web").mkdir()
    for path in (source / "web").iterdir():
        if path.is_file():
            shutil.copyfile(path, root / "web" / path.name)
    (root / "web/src").mkdir()
    (root / "web/src/main.tsx").write_text("// fixture UI", encoding="utf-8")
    (root / "web/dist/assets").mkdir(parents=True)
    (root / "web/dist/index.html").write_text("<!doctype html><title>Prepared konden</title>", encoding="utf-8")
    dependencies = json.loads((root / "scripts/browser/package.json").read_text(encoding="utf-8"))["dependencies"]
    for name in dependencies:
        package = root / "scripts/browser/node_modules" / name
        package.mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({"bin": "cli.js"}), encoding="utf-8")
        (package / "cli.js").touch()
    # A tiny installed product tool keeps preparation checks real without downloads.
    tool = root / ".tools/runtime/tool/tool.exe"
    tool.parent.mkdir(parents=True)
    tool.write_bytes(b"tool")
    (root / "scripts/runtime_tools.json").write_text(json.dumps({runtime_tools.host_platform(): {
        "tool": {"bin": ".", "executable": "tool.exe", "format": "binary",
                 "sha256": hashlib.sha256(b"tool").hexdigest()},
    }}), encoding="utf-8")
    preparation.record(root, preparation.fingerprint(root))
    return root


@pytest.fixture
def prepared_application(prepared_source):
    root = prepared_source
    environment = root / "konden-env"
    venv.create(environment, with_pip=False)
    # Reuse the locked dependencies, including their normal .pth initialization.
    packages = environment / ("Lib/site-packages" if sys.platform == "win32"
                              else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages")
    (packages / "test-dependencies.pth").write_text(
        f"import site; [site.addsitedir(path) for path in {site.getsitepackages()!r}]\n", encoding="utf-8",
    )
    return root
