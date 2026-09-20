"""File extension for a downloaded artifact.

The extension drives how Silver parses the file, so a wrong guess ("bin" for a
CSV, "pdf?token=1" for a URL with a query string) fails the promotion later.
The system mime.types table is absent from the slim images, so the map is
explicit rather than `mimetypes.guess_extension`.
"""
from __future__ import annotations

from urllib.parse import urlsplit

_BY_CONTENT_TYPE = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/zip": "zip",
    "text/csv": "csv",
    "application/csv": "csv",
    "text/plain": "txt",
    "text/html": "html",
    "text/markdown": "md",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "video/mp4": "mp4",
}


def extension_from_content_type(content_type: str) -> str | None:
    base = (content_type or "").split(";")[0].strip().lower()
    if base in _BY_CONTENT_TYPE:
        return _BY_CONTENT_TYPE[base]
    # Structured-suffix types, e.g. application/vnd.sdmx.data+csv, application/rss+xml.
    if base.endswith("+csv"):
        return "csv"
    if base.endswith("json"):
        return "json"
    if base.endswith("xml"):
        return "xml"
    return None


def extension_from_url(url: str) -> str | None:
    """Suffix of the URL path's last segment, ignoring any query string or
    fragment; None when it does not look like a file extension."""
    last_segment = urlsplit(url).path.rsplit("/", 1)[-1]
    if "." not in last_segment:
        return None
    suffix = last_segment.rsplit(".", 1)[-1].lower()
    return suffix if suffix.isalnum() and 1 <= len(suffix) <= 5 else None


def infer_extension(content_type: str, url: str, *, prefer_url: bool) -> str:
    """`prefer_url=True` for direct file downloads, where the URL names the file.
    `prefer_url=False` for APIs, whose path often ends in a dotted key
    (".../M.VN.VND.A") that is not an extension — the response type is the
    only trustworthy signal there."""
    from_type = extension_from_content_type(content_type)
    from_url = extension_from_url(url)
    order = (from_url, from_type) if prefer_url else (from_type,)
    return next((ext for ext in order if ext), "bin")
