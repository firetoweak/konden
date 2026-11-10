"""Prepare pinned GitHub release tools using the runner's authenticated gh CLI."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit

from scripts import runtime_tools


def prepare(root: Path) -> None:
    runtime_tools.activate(root)
    for name, package in runtime_tools.packages(root).items():
        source = urlsplit(package["url"])
        if source.hostname != "github.com":
            continue
        executable = runtime_tools.tool_directory(root, name) / package["bin"] / package["executable"]
        if executable.is_file() or package.get("format") not in {"binary", "binary-archive"} and shutil.which(name):
            continue
        owner, repository, _, _, tag, filename = source.path.strip("/").split("/")
        with tempfile.TemporaryDirectory(prefix=f"konden-ci-{name}-") as temporary:
            subprocess.run([
                "gh", "release", "download", tag, "--repo", f"github.com/{owner}/{repository}",
                "--pattern", filename, "--dir", temporary,
            ], check=True)
            archive = Path(temporary) / filename
            runtime_tools.install_package(root, name, {**package, "url": archive.as_uri()})
        runtime_tools.activate(root)


if __name__ == "__main__":
    prepare(Path(__file__).resolve().parents[1])
