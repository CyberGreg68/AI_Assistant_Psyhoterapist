from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from urllib import parse
from urllib import request


@dataclass(slots=True)
class DownloadedDocument:
    url: str
    output_path: Path
    content_type: str


def _infer_extension(url: str, content_type: str) -> str:
    parsed = parse.urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix:
        return suffix
    normalized_type = content_type.lower()
    if "html" in normalized_type:
        return ".html"
    if "json" in normalized_type:
        return ".json"
    if "csv" in normalized_type:
        return ".csv"
    if "plain" in normalized_type or "text" in normalized_type:
        return ".txt"
    return ".bin"


def download_remote_documents(
    urls: list[str],
    *,
    output_dir: Path,
    timeout_seconds: int = 30,
    bearer_token: str | None = None,
    basic_auth: str | None = None,
    cookie_header: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> list[DownloadedDocument]:
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[DownloadedDocument] = []
    for url in urls:
        headers = {"User-Agent": "AI-Assistant-Psychotherapist/0.1"}
        headers.update(extra_headers or {})
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        elif basic_auth:
            headers["Authorization"] = f"Basic {basic_auth}"
        if cookie_header:
            headers["Cookie"] = cookie_header
        http_request = request.Request(url, headers=headers, method="GET")
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            content = response.read()
            content_type = response.headers.get("Content-Type", "application/octet-stream")
        extension = _infer_extension(url, content_type)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        output_path = output_dir / f"remote_{digest}{extension}"
        output_path.write_bytes(content)
        downloaded.append(
            DownloadedDocument(url=url, output_path=output_path, content_type=content_type)
        )
    return downloaded