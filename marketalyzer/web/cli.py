"""Command line entry: ``marketalyzer-web``."""

import argparse
import os
import secrets
import tempfile


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
    args = parser.parse_args(argv)

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
    token = os.environ.get("MARKETALYZER_TOKEN") or secrets.token_urlsafe(16)
    app = create_app(token, account, demo=args.demo)
    print(f"Arayüz: http://{args.host}:{args.port}/?token={token}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0
