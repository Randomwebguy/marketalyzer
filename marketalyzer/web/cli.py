"""Command line entry: ``marketalyzer-web``."""

import argparse
import os
import secrets
import tempfile
from pathlib import Path

from marketalyzer.paper.cli import default_home

MIN_TOKEN = 16


def token_path() -> Path:
    """Return the file that keeps the access token between runs."""
    return default_home() / "web" / "token"


def _write_private(path: Path, text: str) -> None:
    """Write a file only the owner can read (on POSIX), replacing it atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def access_token(rotate: bool = False) -> str:
    """Return the access token: the environment's, else the stored one.

    The first run stores a new random token, so the address with ``?token=``,
    the browser's login cookie and an installed app keep working across
    restarts. ``rotate`` replaces the stored token, which signs everyone out.
    """
    configured = os.environ.get("MARKETALYZER_TOKEN", "").strip()
    if configured:
        return configured
    path = token_path()
    if not rotate:
        try:
            stored = path.read_text(encoding="utf-8").strip()
        except OSError:
            stored = ""
        if len(stored) >= MIN_TOKEN:
            return stored
    token = secrets.token_urlsafe(24)
    _write_private(path, token)
    return token


def main(argv: list[str] | None = None) -> int:
    """Start the web interface."""
    parser = argparse.ArgumentParser(
        prog="marketalyzer-web",
        description="marketalyzer web arayüzünü başlatır (gerçek para kullanılmaz).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--account", help="Arayüzün yönettiği paper hesabın adı (varsayılan: web)"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="İnternet olmadan denemek için sentetik veriyle çalış",
    )
    parser.add_argument(
        "--new-token",
        action="store_true",
        help="Yeni bir erişim anahtarı üret (eski adresler ve oturumlar geçersiz olur)",
    )
    parser.add_argument(
        "--print-token",
        action="store_true",
        help="Erişim anahtarını yazdırıp çık (yoksa üretir)",
    )
    args = parser.parse_args(argv)
    token = access_token(rotate=args.new_token)
    if args.print_token:
        print(token)
        return 0

    import uvicorn

    from marketalyzer.web.app import create_app

    if args.demo:
        from marketalyzer.web import demo

        demo.enable()
        # Keep synthetic prices out of the real price cache.
        os.environ["MARKETALYZER_CACHE_DIR"] = tempfile.mkdtemp(
            prefix="marketalyzer-demo-"
        )
    account = args.account or ("demo" if args.demo else "web")
    app = create_app(token, account, demo=args.demo)
    print(f"Arayüz: http://{args.host}:{args.port}/?token={token}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0
