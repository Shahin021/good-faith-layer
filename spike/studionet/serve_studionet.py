"""
Loopback-only static server for the Studionet wallet page.

Serves a fixed allowlist of seven files and nothing else: no directory
listings, no path resolution against the repository, no .env, no keys.
Binds to 127.0.0.1 only and rejects requests whose Host header is not a
loopback name (blocks DNS-rebinding pages from reading the case file).
Its case file is spike/studionet/run/state.json, never the 61997 case.

    python spike/studionet/serve_studionet.py            # http://127.0.0.1:8766/wallet.html
    python spike/studionet/serve_studionet.py --port 9001
"""

import argparse
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BIND = "127.0.0.1"

# URL path -> (file, content type). Exact matches only.
ROUTES = {
    "/wallet.html": (HERE / "public" / "wallet.html", "text/html; charset=utf-8"),
    "/sn-core.js": (HERE / "public" / "sn-core.js", "text/javascript; charset=utf-8"),
    "/sn-flow.js": (HERE / "public" / "sn-flow.js", "text/javascript; charset=utf-8"),
    "/sn-page.js": (HERE / "public" / "sn-page.js", "text/javascript; charset=utf-8"),
    "/genlayer-js-1.1.8.js": (HERE / "public" / "vendor" / "genlayer-js-1.1.8.js", "text/javascript; charset=utf-8"),
    "/assertions.json": (ROOT / "scenarios" / "01_protected" / "assertions.json", "application/json"),
    "/state.json": (HERE / "run" / "state.json", "application/json"),
}
ALIASES = {"/": "/wallet.html"}


def allowed_hosts(port):
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


class Handler(BaseHTTPRequestHandler):
    server_version = "gfl-studionet"
    sys_version = ""

    def _deny(self, code, msg):
        body = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve(self):
        if self.headers.get("Host", "") not in allowed_hosts(self.server.server_address[1]):
            return self._deny(421, "host not allowed")
        raw_path = self.path
        path = urlsplit(raw_path).path
        path = ALIASES.get(path, path)
        route = ROUTES.get(path)
        if route is None:
            return self._deny(404, "not found")
        file, ctype = route
        if not file.is_file():
            return self._deny(404, "not available yet")
        body = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = _serve
    do_HEAD = _serve

    def _method_not_allowed(self):
        self._deny(405, "method not allowed")

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.command if hasattr(self, "command") else "-", fmt % args))


def make_server(port=8766):
    return ThreadingHTTPServer((BIND, port), Handler)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8766)
    a = p.parse_args(argv)
    srv = make_server(a.port)
    print(f"Serving {len(ROUTES)} allowlisted files on http://{BIND}:{srv.server_address[1]}/wallet.html "
          f"(loopback only). Ctrl+C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
