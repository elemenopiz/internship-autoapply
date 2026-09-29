"""Path-traversal-safe serving of generated documents and artifacts (``GET /files/{kind}/{path}``).

A request is served only when every rule holds: the kind is ``documents`` or ``artifacts``; the relative path
has no empty, ``.``/``..``, absolute, drive-letter, backslash or reserved-device segments; no component below
the root is a symlink; the fully resolved file lies inside the resolved root and is a regular file; and the
extension is on the allow-list. Everything else is a plain 404 (no oracle for what exists).
"""

from __future__ import annotations

import re
from pathlib import Path, PurePath

from starlette.responses import FileResponse, Response

from autoapply.config import AppPaths

__all__ = ["FILE_KINDS", "resolve_served_file", "serve_file", "served_file_ref"]

FILE_KINDS = ("documents", "artifacts")
_INLINE_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json",
}
_ATTACHMENT_SUFFIXES = frozenset({".html", ".htm", ".zip"})  # never rendered by the browser
_BAD_SEGMENT = re.compile(r'[\x00-\x1f<>:"|?*\\]')
_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)


def _root_for(paths: AppPaths, kind: str) -> Path | None:
    if kind == "documents":
        return paths.documents_dir
    if kind == "artifacts":
        return paths.artifacts_dir
    return None


def _clean_segments(raw: str) -> list[str] | None:
    if not raw or raw.startswith("/"):
        return None
    segments = raw.split("/")
    for segment in segments:
        if segment in {"", ".", ".."} or _BAD_SEGMENT.search(segment):
            return None
        if segment.rstrip(" .") != segment:  # Windows silently drops trailing dots/spaces
            return None
        if segment.split(".")[0].lower() in _RESERVED:
            return None
    return segments


def resolve_served_file(paths: AppPaths, kind: str, raw: str) -> Path | None:
    """The file to serve for ``kind``/``raw``, or ``None`` when any safety rule fails."""
    root = _root_for(paths, kind)
    segments = _clean_segments(raw)
    if root is None or segments is None:
        return None
    suffix = PurePath(segments[-1]).suffix.lower()
    if suffix not in _INLINE_TYPES and suffix not in _ATTACHMENT_SUFFIXES:
        return None
    try:
        if root.is_symlink() and not root.resolve().is_dir():
            return None
        current = root
        for segment in segments:
            current = current / segment
            if current.is_symlink():
                return None
        resolved = current.resolve(strict=True)
        if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    return resolved


def serve_file(path: Path) -> Response:
    """A ``FileResponse`` that can never be rendered as active content (always Content-Disposition)."""
    suffix = path.suffix.lower()
    if suffix in _INLINE_TYPES:
        return FileResponse(
            path,
            media_type=_INLINE_TYPES[suffix],
            filename=path.name,
            content_disposition_type="inline",
        )
    return FileResponse(
        path,
        media_type="text/plain; charset=utf-8",
        filename=path.name,
        content_disposition_type="attachment",
    )


def served_file_ref(paths: AppPaths, raw: str) -> tuple[str, str] | None:
    """``(kind, relative posix path)`` for a stored path string inside documents/ or artifacts/, else ``None``.

    Stored paths are absolute, relative to the data directory, or relative to the working directory.
    """
    if not raw or "\x00" in raw:
        return None
    candidate = Path(raw)
    attempts = (
        [candidate] if candidate.is_absolute() else [paths.root / candidate, Path.cwd() / candidate]
    )
    for attempt in attempts:
        for kind in FILE_KINDS:
            root = _root_for(paths, kind)
            if root is None:
                continue
            try:
                parts = attempt.relative_to(root).parts
            except ValueError:
                continue
            if parts and ".." not in parts:
                return kind, "/".join(parts)
    return None
