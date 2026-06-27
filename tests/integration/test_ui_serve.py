from __future__ import annotations

from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import urlopen

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = REPO_ROOT / "ui" / "dist"
INDEX_PATH = DIST_DIR / "index.html"


class _SpaHandler(SimpleHTTPRequestHandler):
    def send_head(self):  # type: ignore[override]
        request_path = urlparse(self.path).path
        candidate = Path(self.translate_path(request_path))
        if candidate.exists():
            return super().send_head()
        if Path(request_path).suffix:
            self.send_error(404, "File not found")
            return None
        original_path = self.path
        self.path = "/index.html"
        try:
            return super().send_head()
        finally:
            self.path = original_path

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return None


@contextmanager
def _serve_dist(directory: Path):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(_SpaHandler, directory=str(directory)),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _fetch(url: str) -> tuple[int, bytes, str]:
    try:
        with urlopen(url) as response:
            return response.status, response.read(), response.headers.get("Content-Type", "")
    except HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


@pytest.mark.integration
@pytest.mark.skipif(
    not INDEX_PATH.exists(),
    reason="ui/dist/index.html not found; build the UI before running this test",
)
def test_ui_serves_built_dist_on_configured_port() -> None:
    with _serve_dist(DIST_DIR) as base_url:
        root_status, root_body, root_content_type = _fetch(f"{base_url}/")
        assert root_status == 200
        assert "text/html" in root_content_type
        root_html = root_body.decode("utf-8")
        assert '<div id="root">' in root_html or '<div id="root"></div>' in root_html

        env_status, _, _ = _fetch(f"{base_url}/env-config.js")
        assert env_status in {200, 404}

        jobs_status, jobs_body, jobs_content_type = _fetch(f"{base_url}/jobs")
        assert jobs_status == 200
        assert "text/html" in jobs_content_type
        assert jobs_body == root_body
