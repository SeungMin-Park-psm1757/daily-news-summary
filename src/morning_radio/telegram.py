from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from morning_radio.config import AppConfig

TELEGRAM_MAX_MESSAGE = 3500


def send_digest_and_audio(
    *,
    config: AppConfig,
    digest_markdown: str,
    title: str,
    audio_path: Path | None,
) -> dict[str, Any]:
    text = _markdown_to_telegram_text(digest_markdown)
    message_ids = _send_text_chunks(config, text)
    document_result = None
    if audio_path and audio_path.exists():
        document_result = _send_document(config, audio_path, caption=title)
    return {
        "sent": True,
        "message_ids": message_ids,
        "audio_sent": bool(document_result),
        "audio_result": document_result,
    }


def _send_text_chunks(config: AppConfig, text: str) -> list[int]:
    chunks = _chunk_text(text, TELEGRAM_MAX_MESSAGE)
    message_ids: list[int] = []
    for chunk in chunks:
        payload = {
            "chat_id": config.telegram_chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if config.telegram_thread_id:
            payload["message_thread_id"] = config.telegram_thread_id
        response = requests.post(
            f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
            data=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise ValueError(f"Telegram sendMessage failed: {data}")
        message_ids.append(int(data["result"]["message_id"]))
    return message_ids


def _send_document(config: AppConfig, audio_path: Path, *, caption: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": config.telegram_chat_id,
        "caption": caption,
    }
    if config.telegram_thread_id:
        payload["message_thread_id"] = config.telegram_thread_id

    with audio_path.open("rb") as handle:
        response = requests.post(
            f"https://api.telegram.org/bot{config.telegram_bot_token}/sendDocument",
            data=payload,
            files={"document": (audio_path.name, handle)},
            timeout=60,
        )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise ValueError(f"Telegram sendDocument failed: {data}")
    return data["result"]


def _markdown_to_telegram_text(markdown: str) -> str:
    lines: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if line.startswith("# "):
            lines.append(line[2:])
            continue
        if line.startswith("## "):
            lines.append("")
            lines.append(f"[{line[3:]}]")
            continue
        if line.startswith("- **") and line.endswith("**"):
            title = line[4:-2]
            lines.append(f"• {title}")
            continue
        if line.startswith("  "):
            lines.append(f"  {line.strip()}")
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _chunk_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(paragraph) <= limit:
            current = paragraph
            continue

        lines = paragraph.splitlines()
        partial = ""
        for line in lines:
            candidate_line = line if not partial else f"{partial}\n{line}"
            if len(candidate_line) <= limit:
                partial = candidate_line
                continue
            if partial:
                chunks.append(partial)
            partial = line
        current = partial

    if current:
        chunks.append(current)
    return chunks
