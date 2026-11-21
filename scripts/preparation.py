"""Check preparation inputs and installed entrypoints before importing the application."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import runtime_tools


STAMP = Path(".tools/prepared.sha256")


def fingerprint(root: Path) -> str:
    inputs = [root / name for name in (
        ".python-version", "requirements.lock", "scripts/setup.py", "scripts/preparation.py",
        "scripts/runtime_tools.py", "scripts/runtime_tools.json",
        "scripts/browser/package.json", "scripts/browser/package-lock.json",
        "web/package.json", "web/package-lock.json", "web/index.html", "web/vite.config.ts",
    )]
    inputs.extend((root / "web").glob("tsconfig*.json"))
    for directory in ("web/src", "web/public", "assets/brand"):
        inputs.extend(path for path in (root / directory).rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for path in sorted(inputs):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def record(root: Path, signature: str) -> None:
    stamp = root / STAMP
    stamp.parent.mkdir(parents=True, exist_ok=True)
    temporary = stamp.with_suffix(".tmp")
    temporary.write_text(signature, encoding="utf-8")
    temporary.replace(stamp)


def is_prepared(root: Path, *, development: bool = False) -> bool:
    stamp = root / STAMP
    if not stamp.is_file() or stamp.read_text(encoding="utf-8") != fingerprint(root):
        return False
    if not (root / "web/dist/index.html").is_file():
        return False
    if development and not (root / "web/node_modules/vite/bin/vite.js").is_file():
        return False
    browser = root / "scripts/browser"
    dependencies = json.loads((browser / "package.json").read_text(encoding="utf-8"))["dependencies"]
    for name in dependencies:
        package = browser / "node_modules" / name / "package.json"
        if not package.is_file():
            return False
        bins = json.loads(package.read_text(encoding="utf-8")).get("bin", {})
        entrypoints = [bins] if isinstance(bins, str) else bins.values()
        if any(not (package.parent / entrypoint).is_file() for entrypoint in entrypoints):
            return False
    path = runtime_tools.command_path(root)
    return all(runtime_tools.package_ready(root, name, package, path=path)
               for name, package in runtime_tools.packages(root).items())
