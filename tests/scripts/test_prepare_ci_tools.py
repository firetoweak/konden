"""CI downloads keep the product's version and verification contract."""
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from scripts import prepare_ci_tools as ci
from tests.scripts.test_runtime_tools import archive, package


@pytest.mark.parametrize("valid", [True, False])
def test_ci_release_download_is_installed_only_after_pinned_hash_verification(tmp_path, monkeypatch, valid):
    """Authenticated CI transport cannot bypass checksums or change the pinned release identity."""
    data = archive()
    record = {**package(data), "url": "https://github.com/owner/repository/releases/download/v1/tool.zip"}
    monkeypatch.setattr(ci.runtime_tools, "packages", lambda _: {"tool": record})
    monkeypatch.setattr(ci.shutil, "which", lambda _: None)
    run = ci.subprocess.run
    urlopen = ci.runtime_tools.urlopen

    def download(command, **kwargs):
        if command[0] != "gh":
            return run(command, **kwargs)
        assert command[:4] == ["gh", "release", "download", "v1"]
        assert command[command.index("--repo") + 1] == "github.com/owner/repository"
        assert command[command.index("--pattern") + 1] == "tool.zip"
        destination = Path(command[command.index("--dir") + 1]) / "tool.zip"
        destination.write_bytes(data if valid else b"corrupt release")

    def open_local(url, **kwargs):
        source = urlsplit(url)
        assert source.scheme == "file"
        return urlopen(url, **kwargs)

    monkeypatch.setattr(ci.subprocess, "run", download)
    monkeypatch.setattr(ci.runtime_tools, "urlopen", open_local)
    executable = ci.runtime_tools.tool_directory(tmp_path, "tool") / record["executable"]
    if valid:
        ci.prepare(tmp_path)
        assert executable.read_bytes() == b"binary"
        monkeypatch.setattr(ci.subprocess, "run", lambda *args, **kwargs: pytest.fail("must reuse prepared tools"))
        ci.prepare(tmp_path)
    else:
        with pytest.raises(ValueError, match="校验失败"):
            ci.prepare(tmp_path)
        assert not executable.exists()
