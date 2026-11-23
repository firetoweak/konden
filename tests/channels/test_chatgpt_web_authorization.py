from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest

from konden.channels.web import chatgpt_authorization as web_authorization
from konden.channels.web import create_web_app
from konden.channels.web.channel import WebChannel
from konden.config import INITIAL_CONFIG, write_json
from konden.llm.config import initial_connections
from konden.model_settings import ModelSettings
from konden.paths import KondenHome
from thinllm import auth

_ORIGIN = "http://127.0.0.1:8765"
_ENDPOINT = "/api/model-settings/chatgpt/authorization"


@pytest.fixture
def browser(tmp_path):
    home = KondenHome(tmp_path)
    auth_path = tmp_path / "private" / "chatgpt.json"
    connections = initial_connections()
    connections["openai-chatgpt"]["auth_file"] = str(auth_path)
    write_json(home.connections_path, connections)
    write_json(home.config_path, INITIAL_CONFIG)
    models = ModelSettings(home, home.sessions_root, path=home.config_path)
    channel = WebChannel(SimpleNamespace(note_web_connected=lambda: None), None)
    body = {"connection_id": channel.connect().connection_id}
    with TestClient(create_web_app(channel=channel, models=models), client=("127.0.0.1", 50000),
                    base_url=_ORIGIN) as client:
        yield client, body, auth_path


def _begin(client, body):
    response = client.post(_ENDPOINT, json=body, headers={"Origin": _ORIGIN})
    assert response.status_code == 201
    path = _ENDPOINT + "/" + response.json()["authorization_id"]
    opened = client.get(path + "/open", follow_redirects=False)
    assert opened.status_code == 303
    redirect = urlsplit(opened.headers["location"])
    assert (redirect.netloc, redirect.path) == ("auth.openai.com", "/api/accounts/authorize")
    query = parse_qs(redirect.query)
    assert query["redirect_uri"] == [_ORIGIN + "/auth/callback"]
    return path, query


def test_local_authorization_commits_before_reporting_connected(browser, monkeypatch):
    client, body, auth_path = browser
    path, query = _begin(client, body)
    assert client.get(path).json() == {"state": "waiting"}
    assert client.post(_ENDPOINT, json=body, headers={"Origin": _ORIGIN}).status_code == 409
    callback = {"state": query["state"][0], "client_id": "oaiapp_test", "code": "test-authorization-code"}
    assert client.get("/auth/callback", params={**callback, "state": "wrong"}).status_code == 400
    assert client.get(path).json() == {"state": "waiting"}

    def exchange(form):
        assert form["redirect_uri"] == query["redirect_uri"][0]
        assert form["code"] == callback["code"]
        return SimpleNamespace(id_token="test-identity", access_token="test-access", refresh_token="test-refresh",
                               expires_in=3600, scope="offline_access resource.invoke chatgpt.tokens.use.direct")

    monkeypatch.setattr(auth, "_token_request", exchange)
    monkeypatch.setattr(auth, "_verified_subject", lambda token, client_id, nonce: "user")
    finished = client.get("/auth/callback", params=callback)
    assert "授权完成" in finished.text and callback["code"] not in finished.text
    assert finished.headers["cache-control"] == "no-store"
    assert client.get(path).json() == {"state": "connected"}
    auth.require_chatgpt_login(auth_path)
    settings = client.get("/api/model-settings").json()
    assert next(row for row in settings["providers"] if row["provider"] == "openai-chatgpt")["configured"]
    assert "test-access" not in str(settings)
    assert settings["config"] == INITIAL_CONFIG
    assert client.get("/auth/callback", params=callback).status_code == 400


def test_foreign_origin_cannot_start_and_declined_or_expired_attempt_can_retry(browser, monkeypatch):
    client, body, auth_path = browser
    assert client.post(_ENDPOINT, json=body, headers={"Origin": "https://other.invalid"}).status_code == 403
    assert not auth_path.exists()

    path, query = _begin(client, body)
    client.get("/auth/callback", params={"state": query["state"][0], "error": "access_denied"})
    assert client.get(path).json()["state"] == "failed"
    with pytest.raises(auth.MissingProviderSetting):
        auth.require_chatgpt_login(auth_path)

    monkeypatch.setattr(web_authorization, "CHATGPT_SIGN_IN_TIMEOUT", 0)
    expired = client.post(_ENDPOINT, json=body, headers={"Origin": _ORIGIN})
    assert expired.status_code == 201
    result = client.get(_ENDPOINT + "/" + expired.json()["authorization_id"]).json()
    assert result["state"] == "failed" and "超时" in result["message"]
    assert client.post(_ENDPOINT, json=body, headers={"Origin": _ORIGIN}).status_code == 201
