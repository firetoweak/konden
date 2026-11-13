"""The ordinary launcher serves the built UI without a Node.js runtime."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

import pytest


@pytest.mark.process
def test_start_serves_built_web_without_node_from_another_directory(tmp_path, prepared_application):
    source = Path(__file__).resolve().parents[2]
    root = prepared_application
    # Use the actual application code with a small static page; no frontend build tools.
    if os.name == "nt":
        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        command = [str(shell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "scripts/start.ps1")]
        system_path = str(Path(os.environ["SystemRoot"]) / "System32")
    else:
        command = ["/bin/sh", str(root / "scripts/start.sh")]
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        (helpers / "dirname").symlink_to(shutil.which("dirname"))
        system_path = str(helpers)
    environment = {**os.environ, "PATH": system_path, "PYTHONPATH": str(source),
                   "KONDEN_HOME": str(tmp_path / "home"), "PYTHONUTF8": "1"}
    environment.pop("KONDEN_CONFIG", None)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with (tmp_path / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command + ["--port", str(port)], cwd=tmp_path, env=environment,
                                   stdout=log, stderr=log)
        try:
            # Probe this loopback server directly, regardless of machine proxies.
            opener = build_opener(ProxyHandler({}))
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                assert process.poll() is None, (tmp_path / "server.log").read_text(encoding="utf-8")
                try:
                    with opener.open(f"http://127.0.0.1:{port}/", timeout=1) as response:
                        assert b"Prepared konden" in response.read()
                    break
                except URLError:
                    time.sleep(0.1)
            else:
                pytest.fail("Prepared Web did not become available")
            # Routine browser requests must stay quiet without hiding failed requests.
            with opener.open(f"http://127.0.0.1:{port}/api/model-settings", timeout=5) as response:
                assert response.status == 200
                response.read()
            for path, status in (("/mcp/", 401), ("/api/missing", 404)):
                with pytest.raises(HTTPError) as failure:
                    opener.open(f"http://127.0.0.1:{port}{path}", timeout=5)
                assert failure.value.code == status
                failure.value.close()
        finally:
            if os.name == "nt":
                subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"),
                                "/PID", str(process.pid), "/T", "/F"], capture_output=True)
            else:
                process.terminate()
            process.wait(timeout=10)
    output = (tmp_path / "server.log").read_text(encoding="utf-8")
    assert "Uvicorn running on" in output
    assert '"GET / HTTP/1.1" 200' not in output
    assert '"GET /api/model-settings HTTP/1.1" 200' not in output
    assert '"GET /mcp/ HTTP/1.1" 401' in output
    assert '"GET /api/missing HTTP/1.1" 404' in output


@pytest.mark.process
def test_start_activates_newly_installed_python_dependencies(tmp_path, prepared_application):
    """New plugin dependencies may need .pth initialization from a fresh interpreter."""
    root = prepared_application
    (root / "scripts/setup.py").write_text('''
from pathlib import Path
import sysconfig
root = Path(__file__).resolve().parents[1]
addon = root / "installed-addon"
addon.mkdir()
(addon / "new_plugin.py").write_text("MESSAGE = 'new-plugin-ready'", encoding="utf-8")
(Path(sysconfig.get_path("purelib")) / "new-plugin.pth").write_text(str(addon) + "\\n", encoding="utf-8")
''', encoding="utf-8")
    (root / "web_chat.py").write_text('''
import new_plugin
import sys
from pathlib import Path
import time
root = Path(__file__).parent
assert sys.argv[1:] == ["--port", "9123"]
(root / "started.txt").write_text(new_plugin.MESSAGE, encoding="utf-8")
while not (root / "stop.txt").exists():
    time.sleep(0.01)
''', encoding="utf-8")
    if os.name == "nt":
        command = [shutil.which("powershell"), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                   str(root / "scripts/start.ps1")]
    else:
        command = ["/bin/sh", str(root / "scripts/start.sh")]
    environment = {**os.environ, "KONDEN_HOME": str(tmp_path / "home"), "PYTHONUTF8": "1"}
    environment.pop("KONDEN_CONFIG", None)
    log_path = tmp_path / "launcher.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command + ["--port", "9123"], cwd=tmp_path, env=environment,
                                   stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 20
            while not (root / "started.txt").exists():
                assert process.poll() is None, log_path.read_text(encoding="utf-8")
                assert time.monotonic() < deadline, log_path.read_text(encoding="utf-8")
                time.sleep(0.05)
            assert (root / "started.txt").read_text(encoding="utf-8") == "new-plugin-ready"
            # The startup command must stay attached to a running application.
            with pytest.raises(subprocess.TimeoutExpired):
                process.wait(timeout=0.3)
        finally:
            (root / "stop.txt").touch()
            process.wait(timeout=10)
    assert process.returncode == 0, log_path.read_text(encoding="utf-8")
