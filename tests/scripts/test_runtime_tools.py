import hashlib
import io
import os
import platform
from pathlib import Path
import shutil
import zipfile

import pytest

from scripts import runtime_tools as tools


def package(data, sha256=None):
    return {"version": "1", "url": "https://example.test/tool.zip", "root": "tool",
            "bin": ".", "executable": "tool.exe", "sha256": sha256 or hashlib.sha256(data).hexdigest()}


def archive():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("tool/tool.exe", b"binary")
        bundle.writestr("tool/LICENSE", b"license")
    return stream.getvalue()


@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize("binary", [False, True])
def test_download_is_verified_before_installation(tmp_path, monkeypatch, valid, binary):
    data = b"binary" if binary else archive()
    monkeypatch.setattr(tools, "urlopen", lambda *args, **kwargs: io.BytesIO(data))
    record = package(data, None if valid else "0" * 64)
    if binary:
        record.update(format="binary", url="https://example.test/tool.exe")
    destination = tools.tool_directory(tmp_path, "tool")
    if valid:
        tools.install_package(tmp_path, "tool", record)
        assert (destination / "tool.exe").read_bytes() == b"binary"
        if not binary:
            assert (destination / "LICENSE").read_bytes() == b"license"
    else:
        with pytest.raises(ValueError, match="校验失败"):
            tools.install_package(tmp_path, "tool", record)
        assert not destination.exists()


def test_binary_is_product_owned_and_replaced_only_after_verification(tmp_path, monkeypatch):
    """A system CLI or stale/modified binary must not silently replace the pinned product tool."""
    data = b"pinned binary"
    record = {**package(data), "format": "binary", "url": "https://example.test/tool.exe"}
    monkeypatch.setattr(tools, "packages", lambda root: {"tool": record})
    monkeypatch.setattr(shutil, "which", lambda name: f"system/{name}")
    monkeypatch.setattr(tools, "urlopen", lambda *args, **kwargs: io.BytesIO(data))
    tools.prepare(tmp_path)
    executable = tools.tool_directory(tmp_path, "tool") / "tool.exe"
    assert executable.read_bytes() == data
    executable.write_bytes(b"previous version")
    monkeypatch.setattr(tools, "urlopen", lambda *args, **kwargs: io.BytesIO(b"corrupt download"))
    with pytest.raises(ValueError, match="校验失败"):
        tools.prepare(tmp_path)
    assert executable.read_bytes() == b"previous version"
    monkeypatch.setattr(tools, "urlopen", lambda *args, **kwargs: io.BytesIO(data))
    tools.prepare(tmp_path)
    assert executable.read_bytes() == data
    monkeypatch.setattr(tools, "urlopen", lambda *args, **kwargs: pytest.fail("must reuse verified binary"))
    tools.prepare(tmp_path)


def test_local_tools_are_visible_only_in_the_process_environment(tmp_path, monkeypatch):
    record = package(b"")
    monkeypatch.setattr(tools, "packages", lambda root: {"tool": record})
    directory = tools.tool_directory(tmp_path, "tool")
    directory.mkdir(parents=True)
    monkeypatch.setenv("PATH", str(tmp_path / "system"))
    tools.activate(tmp_path)
    tools.activate(tmp_path)
    paths = os.environ["PATH"].split(os.pathsep)
    assert Path(paths[0]).resolve() == directory.resolve()
    assert paths[1:] == [str(tmp_path / "system")]


def test_zip_helper_programs_keep_executable_permissions(tmp_path, monkeypatch):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("tool/tool.exe", b"binary")
        helper = zipfile.ZipInfo("tool/crash-handler")
        helper.external_attr = 0o100755 << 16
        bundle.writestr(helper, b"helper")
    data = stream.getvalue()
    monkeypatch.setattr(tools, "urlopen", lambda *args, **kwargs: io.BytesIO(data))
    chmods = []
    monkeypatch.setattr(Path, "chmod", lambda path, mode: chmods.append((path.name, mode)))
    tools.install_package(tmp_path, "tool", package(data))
    if platform.system() == "Darwin":
        assert os.access(tools.tool_directory(tmp_path, "tool") / "crash-handler", os.X_OK)
    else:
        assert ("crash-handler", 0o755) in chmods


@pytest.mark.parametrize("system,machine,expected", [
    ("Darwin", "arm64", "darwin-arm64"), ("Darwin", "x86_64", "darwin-x86_64"),
    ("Windows", "AMD64", "windows-x86_64"), ("Linux", "x86_64", "linux-x86_64"),
    ("Linux", "aarch64", None),
])
def test_downloads_match_supported_host_architecture(monkeypatch, system, machine, expected):
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    if expected is None:
        with pytest.raises(ValueError):
            tools.host_platform()
    else:
        assert tools.host_platform() == expected


def test_existing_system_tools_do_not_trigger_downloads(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "packages", lambda root: {name: package(b"") for name in ("node", "fd", "rg", "git")})
    monkeypatch.setattr(tools, "supported_node", lambda: True)
    monkeypatch.setattr(shutil, "which", lambda name: f"system/{name}")
    monkeypatch.setattr(tools, "install_package", lambda *args: pytest.fail("must reuse system tools"))
    tools.prepare(tmp_path)


def test_browser_uses_application_install_even_when_system_chrome_exists(tmp_path, monkeypatch):
    record = package(b"")
    monkeypatch.setattr(tools, "packages", lambda root: {"chrome": record})
    monkeypatch.setattr(shutil, "which", lambda name: f"system/{name}")
    install = []
    browser = tools.tool_directory(tmp_path, "chrome") / record["executable"]

    def install_browser(root, name, package):
        install.append(name)
        browser.parent.mkdir(parents=True)
        browser.touch()

    monkeypatch.setattr(tools, "install_package", install_browser)
    tools.prepare(tmp_path, include_browser=False)
    assert install == []
    tools.prepare(tmp_path)
    assert install == ["chrome"]
    tools.prepare(tmp_path)
    assert install == ["chrome"]
    if platform.system() == "Darwin":
        assert (browser.parent / "chrome").samefile(browser)


def test_missing_tools_are_prepared_before_setup_preflight(monkeypatch):
    from scripts import setup
    events = []
    monkeypatch.setattr(tools, "prepare", lambda root: events.append("tools"))
    monkeypatch.setattr(setup.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(setup, "check_dependencies", lambda: events.append("check") or ["still missing"])
    with pytest.raises(SystemExit):
        setup.main([])
    assert events == ["tools", "check"]


def test_linux_system_packages_use_the_available_package_manager(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: name if name == "apt-get" else None)
    commands = []
    monkeypatch.setattr(tools.subprocess, "run", lambda command, **kwargs: commands.append(command))
    tools.install_linux_packages(["git"])
    assert commands == [["sudo", "apt-get", "update"], ["sudo", "apt-get", "install", "-y", "git"]]
