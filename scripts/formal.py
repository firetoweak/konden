"""运行 formal/ 中的有界 TLC 模型，只依赖 Python 标准库和 Java 21。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "formal"
WORK = ROOT / ".tools" / "formal"


def _verify(path: Path, expected: str) -> None:
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ValueError(f"TLC SHA-256 不匹配：{path}，实际 {actual}，预期 {expected}")


def _jar() -> Path:
    pin = json.loads((MODELS / "toolchain.json").read_text(encoding="utf-8"))
    version = pin["version"]
    directory = WORK / "deps"
    directory.mkdir(parents=True, exist_ok=True)
    jar = directory / f"tla2tools-{version}.jar"
    if jar.exists():
        _verify(jar, pin["sha256"])
        return jar
    url = f"https://github.com/tlaplus/tlaplus/releases/download/v{version}/tla2tools.jar"
    with tempfile.TemporaryDirectory(dir=directory) as stage:
        download = Path(stage) / jar.name
        with urlopen(url, timeout=60) as response, download.open("wb") as output:
            shutil.copyfileobj(response, output)
        _verify(download, pin["sha256"])
        download.replace(jar)
    return jar


def check(model: str | None) -> None:
    directories = sorted(path for path in MODELS.iterdir() if path.is_dir())
    selected = []
    for directory in directories:
        number, name = directory.name.split("-", 1)
        if model is None or model in {directory.name, name, number, str(int(number))}:
            selected.append(directory / name)
    if not selected:
        choices = ", ".join(path.name for path in directories)
        raise SystemExit(f"找不到形式化模型 {model!r}。可选：{choices}")

    java = shutil.which("java")
    install = "请安装 Eclipse Temurin JDK 21，并将其 bin 目录加入 PATH 后重试。"
    if java is None:
        raise SystemExit(install)
    version = subprocess.check_output([java, "-version"], stderr=subprocess.STDOUT, text=True)
    if not re.search(r'^(?:openjdk|java) version "21(?:[.+-]|")', version, re.MULTILINE):
        raise SystemExit(f"{version.strip()}\n{install}")

    jar = _jar()
    for module in selected:
        print(f"=== TLC {module.parent.name} ===", flush=True)
        with tempfile.TemporaryDirectory(dir=WORK) as directory:
            subprocess.run(
                [java, "-XX:+UseParallelGC", "-Xmx512m", f"-Djava.io.tmpdir={directory}",
                 "-cp", str(jar), "tlc2.TLC", "-metadir", directory, "-workers", "1",
                 "-config", str(module.with_suffix(".cfg")),
                 str(module.with_suffix(".tla"))],
                check=True, cwd=directory, timeout=180,
            )
