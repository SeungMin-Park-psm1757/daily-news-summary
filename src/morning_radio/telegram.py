from __future__ import annotations

import html
import re
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
    public_links: dict[str, str] | None = None,
) -> dict[str, Any]:
    chat_info = _get_chat_info(config)
    text = _markdown_to_telegram_html(digest_markdown)
    text = _append_public_links(text, public_links)
    message_ids = _send_text_chunks(config, text)

    audio_result = None
    audio_mode = None
    if audio_path and audio_path.exists():
        try:
            audio_result = _send_audio(config, audio_path, caption=title)
            audio_mode = "audio"
        except requests.HTTPError:
            audio_result = _send_document(config, audio_path, caption=title)
            audio_mode = "document"

    return {
        "sent": True,
        "message_ids": message_ids,
        "audio_sent": bool(audio_result),
        "audio_mode": audio_mode,
        "audio_result": audio_result,
        "target_type": chat_info.get("type"),
        "target_title": chat_info.get("title"),
        "target_username": chat_info.get("username"),
        "thread_id": config.telegram_thread_id,
        "public_links": public_links or {},
    }


def _get_chat_info(config: AppConfig) -> dict[str, Any]:
    response = requests.post(
        _telegram_url(config, "getChat"),
        data={"chat_id": config.telegram_chat_id},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise ValueError(f"Telegram getChat failed: {data}")

    result = data["result"]
    title = (
        result.get("title")
        or " ".join(part for part in [result.get("first_name"), result.get("last_name")] if part).strip()
        or None
    )
    return {
        "id": result.get("id"),
        "type": result.get("type"),
        "title": title,
        "username": result.get("username"),
    }


def _send_text_chunks(config: AppConfig, text: str) -> list[int]:
    chunks = _chunk_text(text, TELEGRAM_MAX_MESSAGE)
    message_ids: list[int] = []
    for chunk in chunks:
        payload: dict[str, Any] = {
            "chat_id": config.telegram_chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
            "disable_notification": config.telegram_silent,
            "parse_mode": "HTML",
        }
        if config.telegram_thread_id:
            payload["message_thread_id"] = config.telegram_thread_id
        response = requests.post(
            _telegram_url(config, "sendMessage"),
            data=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise ValueError(f"Telegram sendMessage failed: {data}")
        message_ids.append(int(data["result"]["message_id"]))
    return message_ids


def _send_audio(config: AppConfig, audio_path: Path, *, caption: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": config.telegram_chat_id,
        "caption": caption,
        "title": audio_path.stem,
        "disable_notification": config.telegram_silent,
    }
    if config.telegram_thread_id:
        payload["message_thread_id"] = config.telegram_thread_id

    with audio_path.open("rb") as handle:
        response = requests.post(
            _telegram_url(config, "sendAudio"),
            data=payload,
            files={"audio": (audio_path.name, handle, "audio/mpeg")},
            timeout=60,
        )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise ValueError(f"Telegram sendAudio failed: {data}")
    return data["result"]


def _send_document(config: AppConfig, audio_path: Path, *, caption: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": config.telegram_chat_id,
        "caption": caption,
        "disable_notification": config.telegram_silent,
    }
    if config.telegram_thread_id:
        payload["message_thread_id"] = config.telegram_thread_id

    with audio_path.open("rb") as handle:
        response = requests.post(
            _telegram_url(config, "sendDocument"),
            data=payload,
            files={"document": (audio_path.name, handle)},
            timeout=60,
        )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise ValueError(f"Telegram sendDocument failed: {data}")
    return data["result"]


def _append_public_links(text: str, public_links: dict[str, str] | None) -> str:
    if not public_links:
        return text

    labels = (
        ("archive", "아카이브 보기"),
        ("summary", "실행 요약"),
        ("digest", "메신저 요약"),
        ("audio", "오디오 파일"),
    )
    link_lines = ["<b>바로가기</b>"]
    for key, label in labels:
        url = (public_links.get(key) or "").strip()
        if not url:
            continue
        link_lines.append(
            f"- <a href=\"{html.escape(url, quote=True)}\">{html.escape(label)}</a>"
        )

    if len(link_lines) == 1:
        return text
    return f"{text}\n\n" + "\n".join(link_lines)


def _markdown_to_telegram_html(markdown: str) -> str:
    lines: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if line.startswith("# "):
            lines.append(f"<b>{html.escape(line[2:])}</b>")
            continue
        if line.startswith("## "):
            lines.append("")
            lines.append(f"<b>{html.escape(line[3:])}</b>")
            continue
        if line.startswith("- **") and line.endswith("**"):
            title = line[4:-2]
            lines.append(f"- <b>{html.escape(title)}</b>")
            continue
        if line.startswith("  "):
            lines.append(_inline_markdown_to_html(line.strip()))
            continue
        lines.append(html.escape(line))
    return "\n".join(lines).strip()


def _inline_markdown_to_html(text: str) -> str:
    escaped = html.escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)


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


def _telegram_url(config: AppConfig, method: str) -> str:
    return f"https://api.telegram.org/bot{config.telegram_bot_token}/{method}"
