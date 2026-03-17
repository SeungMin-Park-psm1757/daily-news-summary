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
    text = _prepare_single_text_message(digest_markdown, public_links)
    message_ids = [_send_text_message(config, text)]

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


def _send_text_message(config: AppConfig, text: str) -> int:
    payload: dict[str, Any] = {
        "chat_id": config.telegram_chat_id,
        "text": text,
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
    return int(data["result"]["message_id"])


def _prepare_single_text_message(markdown: str, public_links: dict[str, str] | None) -> str:
    variants = (
        {"include_why": True, "include_meta": False},
        {"include_why": False, "include_meta": False},
    )
    for variant in variants:
        text = _markdown_to_telegram_html(
            markdown,
            include_why=variant["include_why"],
            include_meta=variant["include_meta"],
        )
        text = _append_public_links(text, public_links)
        if len(text) <= TELEGRAM_MAX_MESSAGE:
            return text

    compact_text = _markdown_to_telegram_html(markdown, include_why=False, include_meta=False)
    compact_text = _append_public_links(compact_text, public_links)
    return _truncate_html_message(compact_text, TELEGRAM_MAX_MESSAGE)


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


def _markdown_to_telegram_html(
    markdown: str,
    *,
    include_why: bool = True,
    include_meta: bool = True,
) -> str:
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
        if line.startswith("  왜 중요하나:") and not include_why:
            continue
        if line.startswith("  메모:") and not include_meta:
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


def _truncate_html_message(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text

    plain = re.sub(r"<[^>]+>", "", text)
    plain = html.unescape(plain)
    trimmed = plain[: max(0, limit - 1)].rstrip()
    if trimmed.endswith("…"):
        return html.escape(trimmed)
    return html.escape(trimmed + "…")


def _telegram_url(config: AppConfig, method: str) -> str:
    return f"https://api.telegram.org/bot{config.telegram_bot_token}/{method}"
