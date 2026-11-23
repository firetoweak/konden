from base64 import urlsafe_b64encode
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import time
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest

from thinllm import MissingProviderSetting, auth
from thinllm.__main__ import main


@pytest.fixture
def oauth_server(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = {**RSAAlgorithm.to_jwk(key.public_key(), as_dict=True), "kid": "test"}
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda _: {"keys": [public_key]})
    login = {"scope": "offline_access resource.invoke chatgpt.tokens.use.direct"}
    attempts = []

    def authorize(path):
        sign_in = auth.ChatGPTSignIn(path, "http://127.0.0.1:12345/auth/callback")
        params = {key: value[0] for key, value in parse_qs(urlsplit(sign_in.authorization_url).query).items()}
        attempts.append(params)
        callback = {"code": ["authorization"], "state": [params["state"]]}
        if params["client_id"] == "dynamic_agent_client":
            callback["client_id"] = ["oaiapp_test"]
        return sign_in, callback

    def exchange(url, *, data, timeout):
        params = attempts[-1]
        assert url == "https://auth.openai.com/api/accounts/oauth/token"
        assert data["client_id"] == "oaiapp_test"
        assert data["grant_type"] == "authorization_code"
        assert data["redirect_uri"] == "http://127.0.0.1:12345/auth/callback"
        assert data["code"] == "authorization"
        assert data["resource"] == "https://api.openai.com/v1"
        challenge = urlsafe_b64encode(sha256(data["code_verifier"].encode()).digest()).decode().rstrip("=")
        assert challenge == params["code_challenge"] and params["code_challenge_method"] == "S256"
        id_token = jwt.encode({
            "iss": "https://auth.openai.com", "aud": "oaiapp_test", "sub": "user",
            "nonce": login.get("nonce", params["nonce"]), "exp": int(time.time()) + 300,
        }, key, algorithm="RS256", headers={"kid": "test"})
        return httpx.Response(200, json={"id_token": id_token, "access_token": "test-access",
                                         "refresh_token": "test-refresh", "expires_in": 3600,
                                         "scope": login["scope"]})

    monkeypatch.setattr(auth, "_browser_callback", authorize)
    monkeypatch.setattr(httpx, "post", exchange)
    return login, attempts


def test_login_reuses_the_issued_client_and_exports_only_the_file_reference(tmp_path, capsys, oauth_server):
    _, attempts = oauth_server
    path = tmp_path / "订阅 登录" / "chatgpt.json"
    args = ["auth", "chatgpt", "--auth-file", str(path)]
    assert main(args) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"openai-chatgpt": {"auth_file": path.as_posix()}}
    assert "test-access" not in (output.out + output.err)
    assert "test-refresh" not in (output.out + output.err)
    assert attempts[0]["client_id"] == "dynamic_agent_client"
    assert attempts[0]["agent_name_hint"] == "konden"
    assert set(attempts[0]["scope"].split()) >= {"openid", "offline_access", "chatgpt.tokens.use.direct"}
    assert attempts[0]["ext_agent_host_id"].startswith("urn:uuid:")
    auth.require_chatgpt_login(path)

    assert main(args) == 0
    assert attempts[1]["client_id"] == "oaiapp_test"
    assert attempts[1]["ext_agent_host_id"] == attempts[0]["ext_agent_host_id"]
    assert attempts[1]["state"] != attempts[0]["state"]
    assert "agent_name_hint" not in attempts[1]


def test_valid_identity_without_plan_permission_cannot_be_used_for_inference(tmp_path, capsys, oauth_server):
    login, attempts = oauth_server
    login["scope"] = "openid profile email"
    path = tmp_path / "chatgpt.json"
    args = ["auth", "chatgpt", "--auth-file", str(path)]
    assert main(args) == 1
    output = capsys.readouterr()
    assert output.out == "" and "尚未授权" in output.err
    with pytest.raises(MissingProviderSetting):
        auth.require_chatgpt_login(path)

    login["scope"] += " offline_access resource.invoke chatgpt.tokens.use.direct"
    assert main(args) == 0
    assert attempts[1]["client_id"] == "oaiapp_test"
    assert attempts[1]["prompt"] == "consent"
    auth.require_chatgpt_login(path)

    authorized = path.read_bytes()
    login["scope"] = "openid profile email"
    assert main(args) == 1
    assert path.read_bytes() == authorized


def test_signed_identity_with_wrong_nonce_cannot_replace_active_credentials(tmp_path, capsys, oauth_server):
    login, _ = oauth_server
    path = tmp_path / "chatgpt.json"
    args = ["auth", "chatgpt", "--auth-file", str(path)]
    assert main(args) == 0
    original = path.read_bytes()
    capsys.readouterr()
    login["nonce"] = "different-attempt"
    assert main(args) == 1
    assert capsys.readouterr().out == ""
    assert path.read_bytes() == original


def test_loopback_ignores_mismatched_state_and_never_logs_callback_codes(monkeypatch, capsys, tmp_path):
    with ThreadPoolExecutor(max_workers=1) as browser:
        def visit(authorization_url):
            params = parse_qs(urlsplit(authorization_url).query)
            redirect = params["redirect_uri"][0]
            assert redirect.startswith("http://127.0.0.1:")
            with httpx.Client(trust_env=False) as client:
                bad = client.get(redirect, params={"state": "different", "code": "bad-code"})
                good = client.get(redirect, params={"state": params["state"][0], "code": "good-code"})
            assert bad.status_code == 400 and good.status_code == 200
            assert "good-code" not in good.text

        callbacks = []

        def open_browser(url):
            callbacks.append(browser.submit(visit, url))
            return True

        monkeypatch.setattr(auth.webbrowser, "open", open_browser)
        _, result = auth._browser_callback(tmp_path / "chatgpt.json")
        callbacks[0].result(timeout=10)
    assert result["code"] == ["good-code"]
    assert "code" not in capsys.readouterr().err
