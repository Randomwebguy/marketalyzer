"""The Netlify front door: site setup, packaging, deploys and CORS."""

import io
import json
import stat
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from marketalyzer.web import netlify
from marketalyzer.web.app import create_app

TOKEN = "test-token"


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


@pytest.fixture
def api(monkeypatch):
    """Replace Netlify's API; ``replies`` are consumed one per request."""
    fake = SimpleNamespace(replies=[], calls=[])

    def request(method, path, token, **kwargs):
        fake.calls.append(
            SimpleNamespace(method=method, path=path, token=token, **kwargs)
        )
        return fake.replies.pop(0)

    monkeypatch.setattr(netlify, "_request", request)
    monkeypatch.setattr(netlify.time, "sleep", lambda seconds: None)
    monkeypatch.delenv("NETLIFY_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(netlify, "cli_token", lambda: None)
    return fake


SITE = {
    "id": "site-1",
    "name": "marketalyzer",
    "ssl_url": "https://marketalyzer.netlify.app",
}


def test_setup_creates_the_marketalyzer_site(api):
    api.replies.append(FakeResponse(201, SITE))
    config = netlify.setup(" nfp_token ")
    assert config == {
        "token": "nfp_token",
        "site_id": "site-1",
        "name": "marketalyzer",
        "url": "https://marketalyzer.netlify.app",
    }
    assert api.calls[0].json == {"name": "marketalyzer"}
    assert netlify.load_config() == config
    assert netlify.site_origin() == "https://marketalyzer.netlify.app"
    if netlify.os.name == "posix":
        assert stat.S_IMODE(netlify.config_path().stat().st_mode) == 0o600
    # A second setup keeps the site and only updates the token.
    assert netlify.setup("nfp_new")["site_id"] == "site-1"
    assert len(api.calls) == 1


def test_taken_name_is_reported(api):
    api.replies.append(FakeResponse(422, {"errors": {"subdomain": ["must be unique"]}}))
    with pytest.raises(netlify.NetlifyError, match="marketalyzer.netlify.app' adı"):
        netlify.setup("t")


def test_bad_token(api):
    api.replies.append(FakeResponse(401, {"code": 401}))
    with pytest.raises(netlify.NetlifyError, match="anahtarı geçersiz"):
        netlify.setup("t")


def test_package_points_at_the_server(tmp_path):
    data = netlify.build_zip("https://abc.trycloudflare.com/")
    archive = zipfile.ZipFile(io.BytesIO(data))
    names = set(archive.namelist())
    assert {
        "index.html",
        "backend.json",
        "sw.js",
        "manifest.webmanifest",
        "_headers",
    } <= names
    assert "static/app.js" in names and "static/js/core.js" in names
    assert "static/index.html" not in names and "static/login.html" not in names
    assert (
        json.loads(archive.read("backend.json"))["api"]
        == "https://abc.trycloudflare.com"
    )
    assert netlify.MARKER in archive.read("index.html").decode()


def test_deploy_waits_until_ready(api):
    api.replies.append(FakeResponse(201, SITE))
    netlify.setup("tok")
    api.calls.clear()
    api.replies += [
        FakeResponse(200, {"id": "d1", "state": "uploaded"}),
        FakeResponse(200, {"id": "d1", "state": "processing"}),
        FakeResponse(200, {"id": "d1", "state": "ready"}),
    ]
    assert (
        netlify.deploy("https://abc.trycloudflare.com")
        == "https://marketalyzer.netlify.app"
    )
    post = api.calls[0]
    assert (post.method, post.path, post.token) == (
        "POST",
        "/sites/site-1/deploys",
        "tok",
    )
    assert post.headers == {"Content-Type": "application/zip"}
    assert zipfile.ZipFile(io.BytesIO(post.data)).read("backend.json")
    assert [c.path for c in api.calls[1:]] == ["/deploys/d1", "/deploys/d1"]


def test_deploy_errors(api):
    with pytest.raises(netlify.NetlifyError, match="ayarlı değil"):
        netlify.deploy("https://x")
    api.replies.append(FakeResponse(201, SITE))
    netlify.setup("tok")
    api.replies += [
        FakeResponse(200, {"id": "d1", "state": "processing"}),
        FakeResponse(200, {"id": "d1", "state": "error", "error_message": "bozuk"}),
    ]
    with pytest.raises(netlify.NetlifyError, match="bozuk"):
        netlify.deploy("https://x")


def test_cli(api, capsys):
    assert netlify.main(["url"]) == 1
    api.replies.append(FakeResponse(201, SITE))
    assert netlify.main(["setup", "--token", "tok"]) == 0
    assert "https://marketalyzer.netlify.app" in capsys.readouterr().out
    assert netlify.main(["url"]) == 0
    assert capsys.readouterr().out.strip() == "https://marketalyzer.netlify.app"
    assert netlify.main(["forget"]) == 0
    assert netlify.site_origin() is None


def test_cors_allows_only_the_site():
    origin = "https://marketalyzer.netlify.app"
    app = create_app(TOKEN, cors_origins=[origin + "/"])
    with TestClient(app) as client:
        preflight = client.options(
            "/api/meta",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == origin
        response = client.get(
            "/api/meta", headers={"Origin": origin, "Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        # A refused request still carries the header, so the page can read the 401.
        refused = client.get("/api/meta", headers={"Origin": origin})
        assert refused.status_code == 401
        assert refused.headers["access-control-allow-origin"] == origin
        other = client.get(
            "/api/meta",
            headers={
                "Origin": "https://evil.example",
                "Authorization": f"Bearer {TOKEN}",
            },
        )
        assert "access-control-allow-origin" not in other.headers


def test_no_cors_by_default(monkeypatch):
    monkeypatch.delenv("MARKETALYZER_CORS_ORIGINS", raising=False)
    with TestClient(create_app(TOKEN)) as client:
        response = client.get(
            "/api/meta",
            headers={
                "Origin": "https://marketalyzer.netlify.app",
                "Authorization": f"Bearer {TOKEN}",
            },
        )
        assert "access-control-allow-origin" not in response.headers


def test_token_from_the_netlify_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() reads USERPROFILE, not HOME, on Windows.
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("NETLIFY_AUTH_TOKEN", raising=False)
    assert netlify.cli_token() is None
    path = tmp_path / ".config" / "netlify" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "userId": "u2",
                "users": {
                    "u1": {"auth": {"token": "old"}},
                    "u2": {"auth": {"token": "cli-token"}},
                },
            }
        )
    )
    assert netlify.cli_token() == "cli-token"
    assert netlify.find_token() == "cli-token"
    monkeypatch.setenv("NETLIFY_AUTH_TOKEN", "env-token")
    assert netlify.find_token() == "env-token"
    assert netlify.find_token(" given ") == "given"


def test_setup_without_any_token(api):
    with pytest.raises(netlify.NetlifyError, match="netlify login"):
        netlify.setup()


def test_setup_uses_the_cli_login(api, monkeypatch):
    monkeypatch.setattr(netlify, "cli_token", lambda: "cli-token")
    api.replies.append(FakeResponse(201, SITE))
    assert netlify.main(["setup"]) == 0
    assert api.calls[0].token == "cli-token"
    assert netlify.load_config()["token"] == "cli-token"
