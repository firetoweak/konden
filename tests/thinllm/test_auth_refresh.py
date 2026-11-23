from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs

import httpx
import pytest

from thinllm import (
    LLMAuthenticationError,
    LLMTransientError,
    MissingProviderSetting,
    auth,
)


def test_temporary_refresh_failure_preserves_grant(chatgpt_login, monkeypatch):
    path = chatgpt_login(expires_at=1)
    original = path.read_bytes()
    monkeypatch.setattr(httpx, "post", lambda *_, **__: httpx.Response(503))
    with pytest.raises(LLMTransientError):
        auth._access_token(path)
    assert path.read_bytes() == original


def test_rejected_refresh_clears_grant_and_keeps_account_registration(chatgpt_login, monkeypatch):
    path = chatgpt_login(expires_at=1)
    original = json.loads(path.read_bytes())
    monkeypatch.setattr(httpx, "post", lambda *_, **__: httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(LLMAuthenticationError, match="重新授权"):
        auth._access_token(path)
    with pytest.raises(MissingProviderSetting):
        auth.require_chatgpt_login(path)
    original["account"]["grant"] = None
    assert json.loads(path.read_bytes()) == original


@pytest.mark.process
def test_two_processes_share_one_rotating_grant(chatgpt_login):
    """A second exchange with the consumed refresh token can invalidate the session."""
    path = chatgpt_login(expires_at=1)
    requests = []

    class Issuer(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())))
            time.sleep(0.3)
            data = json.dumps({"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    script = """
from pathlib import Path
import sys
from thinllm import auth
auth._ISSUER = sys.argv[2]
print(auth._access_token(Path(sys.argv[1])))
"""
    with ThreadingHTTPServer(("127.0.0.1", 0), Issuer) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        children = []
        try:
            issuer = f"http://127.0.0.1:{server.server_port}"
            children.extend(subprocess.Popen(
                [sys.executable, "-c", script, str(path), issuer],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ) for _ in range(2))
            for child in children:
                stdout, stderr = child.communicate(timeout=20)
                assert child.returncode == 0, stderr
                assert stdout.strip() == "new-access"
            assert requests == [("/api/accounts/oauth/token", {
                "client_id": ["oaiapp_test"], "grant_type": ["refresh_token"],
                "refresh_token": ["private-refresh"], "resource": ["https://api.openai.com/v1"],
            })]
            grant = json.loads(path.read_bytes())["account"]["grant"]
            assert grant["refresh_token"] == "new-refresh"
            assert "chatgpt.tokens.use.direct" in grant["scope"].split()
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
            server.shutdown()
