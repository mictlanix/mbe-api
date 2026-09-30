"""The only way a render reaches outside its HTML string: bundled assets and images (#230)."""

import mimetypes
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from weasyprint.urls import URLFetcher, URLFetcherResponse

from app.core.config import settings

ASSETS_DIR = Path(__file__).parent / 'static'


class LocalOnlyFetcher(URLFetcher):
    """Serve `data:` URIs, and `file:` URLs inside the assets or images directory; refuse the rest.

    `images_dir` is read per fetch, not at import, because it is a setting (and tests move it).
    A refusal raises `ValueError`, which WeasyPrint logs as a failed load and renders around.
    """

    def __init__(self) -> None:
        super().__init__(allowed_protocols={'data', 'file'}, allow_redirects=False)

    def fetch(self, url: str, headers: dict | None = None) -> URLFetcherResponse:
        parts = urlsplit(url)
        if parts.scheme == 'data':
            return super().fetch(url, headers)
        if parts.scheme == 'file':
            path = Path(url2pathname(parts.path)).resolve()
            roots = (ASSETS_DIR.resolve(), Path(settings.images_dir).resolve())
            if path.is_file() and any(path.is_relative_to(root) for root in roots):
                content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
                return URLFetcherResponse(url, path.read_bytes(), {'Content-Type': content_type})
        raise ValueError(f'Refused to fetch {url}')
