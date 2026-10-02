"""Serve the interface from a fixed Netlify address: ``marketalyzer-netlify``.

Netlify hosts only the static interface, at an address that never changes
(``https://marketalyzer.netlify.app`` by default). The server keeps running on
this computer behind a tunnel whose address changes on every start; each start
deploys the interface again with ``backend.json`` naming the new address, and
the interface calls the server there with the access token kept in the
browser. The server accepts cross-site requests only from the Netlify site.

The Netlify token comes from ``--token``, ``NETLIFY_AUTH_TOKEN`` or the
account the Netlify CLI is signed in to (``netlify login``). It is stored with
the site id in ``<home>/netlify.json``, readable only by the owner.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from marketalyzer.paper.cli import default_home

API = "https://api.netlify.com/api/v1"
STATIC = Path(__file__).with_name("static")
TIMEOUT = 30
DEPLOY_WAIT = 90.0
SITE_NAME = "marketalyzer"
MARKER = '<meta name="marketalyzer-backend" content="/backend.json">'
# Files that live at the site root on the server too.
ROOT_FILES = ("manifest.webmanifest", "sw.js")
HEADERS = """/backend.json
  Cache-Control: no-store
/index.html
  Cache-Control: no-cache
/sw.js
  Cache-Control: no-cache
"""


class NetlifyError(Exception):
    """A Netlify request that failed, with a Turkish message."""


def config_path() -> Path:
    """Return the file holding the Netlify token and site."""
    return default_home() / "netlify.json"


def load_config() -> dict[str, Any]:
    """Return the stored settings, or an empty dict."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict[str, Any]) -> None:
    """Store the settings in a file only the owner can read."""
    from marketalyzer.ai.settings import write_json

    write_json(config_path(), data)


def site_origin() -> str | None:
    """Return the configured site's origin (``https://name.netlify.app``)."""
    url = load_config().get("url")
    return str(url).rstrip("/") if url else None


def _request(method: str, path: str, token: str, **kwargs) -> requests.Response:
    """Make one Netlify API request: the only place that touches the network."""
    headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
    try:
        return requests.request(
            method, f"{API}{path}", headers=headers, timeout=TIMEOUT, **kwargs
        )
    except requests.RequestException as error:
        raise NetlifyError(
            f"Netlify'a bağlanılamadı: {type(error).__name__}"
        ) from error


def _json(response: requests.Response, action: str) -> dict[str, Any]:
    if response.status_code == 401:
        raise NetlifyError(
            "Netlify erişim anahtarı geçersiz. app.netlify.com > User settings >"
            " Applications > Personal access tokens bölümünden yeni bir anahtar alın."
        )
    if not 200 <= response.status_code < 300:
        detail = (response.text or "").strip()[:200]
        raise NetlifyError(
            f"Netlify {action} başarısız (HTTP {response.status_code}). {detail}"
        )
    try:
        return response.json()
    except ValueError as error:
        raise NetlifyError(f"Netlify {action}: beklenmeyen yanıt.") from error


def create_site(token: str, name: str = SITE_NAME) -> dict[str, Any]:
    """Create the site ``name``.netlify.app; a taken name is an error."""
    response = _request("POST", "/sites", token, json={"name": name})
    if response.status_code == 422:
        raise NetlifyError(
            f"'{name}.netlify.app' adı başka bir hesapta kullanılıyor. Başka bir ad"
            f" deneyin: marketalyzer-netlify setup --token <anahtar> --name {name}-bist"
        )
    return _json(response, "site oluşturma")


def _cli_config_files() -> list[Path]:
    """Where the Netlify CLI keeps its login, by version and platform."""
    home = Path.home()
    files = [home / ".netlify" / "config.json"]
    if os.environ.get("APPDATA"):
        files.append(Path(os.environ["APPDATA"]) / "netlify" / "Config" / "config.json")
    files.append(home / "Library" / "Preferences" / "netlify" / "config.json")
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    files.append(config_home / "netlify" / "config.json")
    return files


def cli_token() -> str | None:
    """Return the token of the account the Netlify CLI is signed in to."""
    for path in _cli_config_files():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        users = data.get("users") if isinstance(data, dict) else None
        if not isinstance(users, dict) or not users:
            continue
        user = users.get(data.get("userId")) or next(iter(users.values()))
        token = ((user or {}).get("auth") or {}).get("token")
        if token:
            return str(token)
    return None


def find_token(given: str | None = None) -> str | None:
    """Return a Netlify token: the given one, the environment's or the CLI's."""
    return (
        (given or "").strip()
        or os.environ.get("NETLIFY_AUTH_TOKEN", "").strip()
        or cli_token()
    )


def setup(token: str | None = None, name: str | None = None) -> dict[str, Any]:
    """Create the site (or keep the configured one) and store the settings."""
    config = load_config()
    token = find_token(token)
    if not token:
        raise NetlifyError(
            "Netlify anahtarı bulunamadı. 'netlify login' ile giriş yapın, ya da"
            " --token <anahtar> verin (app.netlify.com > User settings > Applications"
            " > Personal access tokens)."
        )
    if config.get("site_id") and name in (None, config.get("name")):
        config["token"] = token
        save_config(config)
        return config
    site = create_site(token, name or SITE_NAME)
    config = {
        "token": token,
        "site_id": site["id"],
        "name": site.get("name"),
        "url": site.get("ssl_url") or site.get("url"),
    }
    save_config(config)
    return config


def build_zip(backend: str, static: Path = STATIC) -> bytes:
    """Package the interface with ``backend.json`` pointing at ``backend``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        index = (static / "index.html").read_text(encoding="utf-8")
        if MARKER not in index:
            index = index.replace("</head>", f"{MARKER}\n</head>", 1)
        archive.writestr("index.html", index)
        for name in ROOT_FILES:
            archive.write(static / name, name)
        for path in sorted(static.rglob("*")):
            if path.is_file() and path.name not in ("index.html", "login.html"):
                archive.write(path, f"static/{path.relative_to(static).as_posix()}")
        archive.writestr(
            "backend.json",
            json.dumps(
                {
                    "api": backend.rstrip("/"),
                    "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            ),
        )
        archive.writestr("_headers", HEADERS)
    return buffer.getvalue()


def deploy(backend: str, config: dict[str, Any] | None = None) -> str:
    """Upload the interface for ``backend`` and wait until it is live.

    Returns the site's address.
    """
    config = config or load_config()
    token = os.environ.get("NETLIFY_AUTH_TOKEN") or config.get("token") or cli_token()
    if not token or not config.get("site_id"):
        raise NetlifyError(
            "Netlify ayarlı değil. Önce: marketalyzer-netlify setup --token <anahtar>"
        )
    response = _request(
        "POST",
        f"/sites/{config['site_id']}/deploys",
        token,
        data=build_zip(backend),
        headers={"Content-Type": "application/zip"},
    )
    deployment = _json(response, "yükleme")
    deadline = time.monotonic() + DEPLOY_WAIT
    while deployment.get("state") not in ("ready", "error"):
        if time.monotonic() > deadline:
            raise NetlifyError("Netlify yüklemesi zamanında hazır olmadı.")
        time.sleep(1.5)
        deployment = _json(
            _request("GET", f"/deploys/{deployment['id']}", token), "durum sorgusu"
        )
    if deployment["state"] == "error":
        raise NetlifyError(
            f"Netlify yüklemesi başarısız: {deployment.get('error_message') or 'bilinmiyor'}"
        )
    return config.get("url") or deployment.get("ssl_url")


def main(argv: list[str] | None = None) -> int:
    """Set up the Netlify site, deploy for a server address or print the site."""
    parser = argparse.ArgumentParser(
        prog="marketalyzer-netlify",
        description="Arayüzü sabit bir Netlify adresinden sunar; sunucu bu bilgisayarda"
        " tünelle çalışır.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    setup_cmd = commands.add_parser("setup", help="Siteyi oluştur ve anahtarı kaydet")
    setup_cmd.add_argument(
        "--token",
        help="Netlify personal access token (varsayılan: NETLIFY_AUTH_TOKEN ya da"
        " 'netlify login' oturumu)",
    )
    setup_cmd.add_argument(
        "--name", help=f"Site adı: <ad>.netlify.app (varsayılan: {SITE_NAME})"
    )
    deploy_cmd = commands.add_parser("deploy", help="Arayüzü sunucu adresiyle yükle")
    deploy_cmd.add_argument("--backend", required=True, help="Sunucunun tünel adresi")
    commands.add_parser("url", help="Site adresini yazdır (ayarlı değilse hata)")
    commands.add_parser("forget", help="Kayıtlı Netlify ayarlarını sil")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            config = setup(args.token, args.name)
            print(f"Netlify sitesi hazır: {config['url']}")
        elif args.command == "deploy":
            print(f"Netlify güncellendi: {deploy(args.backend)}")
        elif args.command == "url":
            origin = site_origin()
            if not origin:
                return 1
            print(origin)
        elif args.command == "forget":
            config_path().unlink(missing_ok=True)
            print("Netlify ayarları silindi.")
    except NetlifyError as error:
        print(str(error))
        return 1
    return 0
