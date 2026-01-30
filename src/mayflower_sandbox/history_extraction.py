"""
Utilities for extracting fenced code blocks from message history.
"""

from __future__ import annotations

import re
from typing import Any

_CODE_FENCE_RE = re.compile(r"```(?P<info>[^\n`]*)\n(?P<code>.*?)(?:\n```|```)", re.S)


def _normalize_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _parse_fence_info(info: str) -> tuple[str | None, str | None]:
    if not info:
        return None, None
    parts = info.split()
    language = parts[0].strip() if parts else None
    file_path = None
    for part in parts[1:]:
        if part.startswith("file=") or part.startswith("path="):
            file_path = part.split("=", 1)[1].strip("\"'")
            break
    return language, file_path


def extract_fenced_blocks(text: str) -> list[dict[str, str | None]]:
    blocks: list[dict[str, str | None]] = []
    if not text:
        return blocks
    for match in _CODE_FENCE_RE.finditer(text):
        info = (match.group("info") or "").strip()
        code = match.group("code") or ""
        language, file_path = _parse_fence_info(info)
        blocks.append(
            {
                "language": language,
                "file_path": file_path,
                "code": code,
            }
        )
    return blocks


def _select_block(
    blocks: list[dict[str, str | None]],
    *,
    file_path: str | None = None,
    language: str | None = None,
) -> str:
    if not blocks:
        return ""

    candidates = blocks
    if file_path:
        by_path = [b for b in candidates if b.get("file_path") == file_path]
        if not by_path and "/" in file_path:
            basename = file_path.rsplit("/", 1)[-1]
            by_path = [b for b in blocks if b.get("file_path") in {basename, f"./{basename}"}]
        if by_path:
            candidates = by_path

    if language:
        lang = language.lower()
        lang_matches = [b for b in candidates if (b.get("language") or "").lower() == lang]
        if lang_matches:
            candidates = lang_matches

    return candidates[-1].get("code") or ""


def _iter_message_texts(messages: Any) -> list[str]:
    texts: list[str] = []
    if not messages:
        return texts
    for message in reversed(messages):
        content = None
        if hasattr(message, "content"):
            content = message.content
        elif isinstance(message, dict):
            content = message.get("content")
        elif isinstance(message, (tuple, list)) and len(message) >= 2:
            content = message[1]
        text = _normalize_message_content(content)
        if text:
            texts.append(text)
    return texts


def extract_fenced_code_from_messages(
    messages: Any,
    *,
    file_path: str | None = None,
    language: str | None = None,
) -> str:
    for text in _iter_message_texts(messages):
        blocks = extract_fenced_blocks(text)
        selected = _select_block(blocks, file_path=file_path, language=language)
        if selected:
            return selected
    return ""
