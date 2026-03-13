from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from urllib import parse
from urllib import request
import re


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
    if "pdf" in normalized_type:
        return ".pdf"
    if "wordprocessingml" in normalized_type or "docx" in normalized_type:
        return ".docx"
    if "xml" in normalized_type:
        return ".xml"
    if "csv" in normalized_type:
        return ".csv"
    if "plain" in normalized_type or "text" in normalized_type:
        return ".txt"
    return ".bin"


def _infer_download_name(url: str, content_type: str, content_disposition: str | None) -> str:
    if content_disposition:
        match = re.search(r"filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)", content_disposition, flags=re.IGNORECASE)
        if match:
            raw_name = match.group(1) or match.group(2) or ""
            candidate = Path(parse.unquote(raw_name)).name
            if candidate:
                return candidate

    parsed = parse.urlparse(url)
    file_name = Path(parse.unquote(parsed.path)).name
    if file_name:
        return file_name

    return f"download{_infer_extension(url, content_type)}"


def _sanitize_stem(file_name: str) -> str:
    stem = Path(file_name).stem or "remote"
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip("-._")
    return cleaned[:48] or "remote"


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
            content_disposition = response.headers.get("Content-Disposition")
        extension = _infer_extension(url, content_type)
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        inferred_name = _infer_download_name(url, content_type, content_disposition)
        output_path = output_dir / f"remote_{_sanitize_stem(inferred_name)}_{digest}{extension}"
        output_path.write_bytes(content)
        downloaded.append(
            DownloadedDocument(url=url, output_path=output_path, content_type=content_type)
        )
    return downloaded