"""Command line entry: ``marketalyzer-web``."""

import argparse
import os
import secrets


def main(argv: list[str] | None = None) -> int:
    """Start the web interface."""
    parser = argparse.ArgumentParser(
        prog="marketalyzer-web",
        description="marketalyzer web arayüzünü başlatır (gerçek para kullanılmaz).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--account", default="web", help="Arayüzün yönettiği paper hesabın adı"
    )
    args = parser.parse_args(argv)

    import uvicorn

    from marketalyzer.web.app import create_app

    token = os.environ.get("MARKETALYZER_TOKEN") or secrets.token_urlsafe(16)
    app = create_app(token, args.account)
    print(f"Arayüz: http://{args.host}:{args.port}/?token={token}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0
