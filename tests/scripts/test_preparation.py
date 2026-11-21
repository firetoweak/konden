import json
import os
from pathlib import Path
from subprocess import CompletedProcess
import sys

import pytest

from scripts import launch, preparation


@pytest.mark.parametrize("relative", [
    "requirements.lock", "scripts/runtime_tools.json", "scripts/browser/package-lock.json",
    "web/package-lock.json", "web/src/main.tsx",
])
def test_source_updates_require_preparation(prepared_source, relative):
    """A Git update must not reuse dependencies or a UI built for different inputs."""
    assert preparation.is_prepared(prepared_source)
    path = prepared_source / relative
    path.write_bytes(path.read_bytes() + b"\n")
    assert not preparation.is_prepared(prepared_source)


@pytest.mark.parametrize("relative", [
    ".tools/prepared.sha256", ".tools/runtime/tool/tool.exe", "web/dist/index.html",
    "scripts/browser/node_modules/@playwright/mcp/package.json",
    "scripts/browser/node_modules/chrome-devtools-mcp/cli.js",
])
def test_missing_installed_entrypoints_require_preparation(prepared_source, relative):
    """An unchanged source tree does not make a partially removed installation ready."""
    (prepared_source / relative).unlink()
    assert not preparation.is_prepared(prepared_source)


def test_new_browser_dependency_and_frontend_files_invalidate_preparation(prepared_source):
    browser = prepared_source / "scripts/browser/package.json"
    content = json.loads(browser.read_text(encoding="utf-8"))
    content["dependencies"]["new-mcp"] = "1.0.0"
    browser.write_text(json.dumps(content), encoding="utf-8")
    preparation.record(prepared_source, preparation.fingerprint(prepared_source))
    assert not preparation.is_prepared(prepared_source)
    content["dependencies"].pop("new-mcp")
    browser.write_text(json.dumps(content), encoding="utf-8")
    preparation.record(prepared_source, preparation.fingerprint(prepared_source))
    page = prepared_source / "web/src/new-page.tsx"
    page.touch()
    assert not preparation.is_prepared(prepared_source)
    preparation.record(prepared_source, preparation.fingerprint(prepared_source))
    page.unlink()
    assert not preparation.is_prepared(prepared_source)


@pytest.mark.parametrize("ready,returncode", [(True, 0), (False, 0), (False, 9)])
def test_launch_prepares_before_import_and_stops_on_failure(prepared_source, monkeypatch, ready, returncode):
    """Missing dependencies must be repaired before application imports can fail."""
    monkeypatch.setattr(launch, "ROOT", prepared_source)
    monkeypatch.setattr(sys, "argv", ["launch", "--port", "9123"])
    if not ready:
        (prepared_source / preparation.STAMP).unlink()
    events = []

    def prepare(command, **options):
        assert command == [sys.executable, "-m", "scripts.setup"]
        assert options["cwd"] == prepared_source
        events.append("prepare")
        return CompletedProcess(command, returncode)

    def restart(executable, arguments):
        assert executable == sys.executable
        assert arguments == [sys.executable, str(prepared_source / "web_chat.py"), "--port", "9123"]
        events.append("restart")
        raise SystemExit(0)

    class ApplicationProcess:
        def __init__(self, command):
            self.command = command

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def wait(self):
            restart(self.command[0], self.command)

    class Web:
        @property
        def main(self):
            events.append("import")
            return lambda: events.append(tuple(sys.argv[1:]))

    monkeypatch.setattr(launch.subprocess, "run", prepare)
    monkeypatch.setattr(launch.os, "execv", restart)
    monkeypatch.setattr(launch.subprocess, "Popen", ApplicationProcess)
    monkeypatch.setitem(sys.modules, "web_chat", Web())
    if not ready:
        with pytest.raises(SystemExit) as failure:
            launch.main()
        assert failure.value.code == returncode
        assert events == ["prepare"] + ([] if returncode else ["restart"])
    else:
        launch.main()
        assert events == ["import", ("--port", "9123")]


def test_prepared_start_does_not_change_the_command_environment(prepared_source, monkeypatch):
    monkeypatch.setenv("PATH", str(Path("caller-tools")))
    assert preparation.is_prepared(prepared_source)
    assert os.environ["PATH"] == str(Path("caller-tools"))
