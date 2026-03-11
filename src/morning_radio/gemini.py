from __future__ import annotations

import json
import re
import time
from typing import Any

from google import genai
from google.genai import errors
from google.genai import types

from morning_radio.config import AppConfig
from morning_radio.models import CategoryBrief, NewsItem, RadioShow


def _extract_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text:
        return text

    candidates = getattr(response, "candidates", None) or []
    parts: list[str] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            value = getattr(part, "text", None)
            if value:
                parts.append(value)
    return "\n".join(parts).strip()


def _extract_json_payload(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```json\s*", "", cleaned)
        cleaned = re.sub(r"^```\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Model did not return a JSON object.")
    return json.loads(cleaned[start : end + 1])


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _markdown_to_plaintext(markdown: str) -> str:
    text = markdown.strip()
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    return text.strip()


class GeminiEditor:
    def __init__(self, config: AppConfig) -> None:
        if not config.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required for GeminiEditor.")
        self.config = config
        self.client = genai.Client(api_key=config.gemini_api_key)

    def _generate_json(
        self,
        *,
        model: str,
        system_instruction: str,
        prompt: str,
        max_output_tokens: int,
        temperature: float,
    ) -> dict[str, Any]:
        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                systemInstruction=system_instruction,
                temperature=temperature,
                maxOutputTokens=max_output_tokens,
                responseMimeType="application/json",
            ),
        )
        return _extract_json_payload(_extract_text(response))

    def create_category_brief(
        self,
        *,
        category: str,
        label: str,
        articles: list[NewsItem],
        max_story_count: int,
    ) -> CategoryBrief:
        serializable_items = [
            {
                "title": article.title,
                "source": article.source,
                "published_at": article.published_at.isoformat(),
                "summary": article.summary,
                "url": article.url,
                "score": article.score,
            }
            for article in articles
        ]
        system_instruction = (
            "You are a careful Korean morning radio editor. "
            "Use only the supplied article metadata. "
            "Summarize substance, not headlines. "
            "Do not repeat titles or source names in angle unless absolutely necessary. "
            "If specifics are uncertain, explicitly say details are still developing. "
            "Never invent quotes, figures, motives, or classified context."
        )
        prompt = f"""
카테고리: {label} ({category})
목표: 점수가 높은 기사만 바탕으로 한국어 브리프를 만드세요.
반드시 JSON 객체 하나만 반환하세요.

요구 사항:
- lead: 오늘 이 분야의 큰 흐름을 1~3문장으로 설명
- stories: 최대 {max_story_count}개
- 각 story는 headline, angle, message_summary, why_it_matters, source_urls를 포함
- angle은 기사 제목을 다시 옮기지 말고 핵심 내용만 1~2문장으로 요약
- message_summary는 메신저 공유용 한 문장 요약이며, 제목과 같은 표현을 반복하지 말 것
- message_summary는 가장 중요한 변화나 영향 1~2개에만 `**굵은 표시**`를 넣을 것
- watch: 후속 관찰 포인트 1문장

입력 기사:
{_json_dumps(serializable_items)}
""".strip()

        payload = self._generate_json(
            model=self.config.triage_model,
            system_instruction=system_instruction,
            prompt=prompt,
            max_output_tokens=self.config.max_output_tokens,
            temperature=0.25,
        )

        return CategoryBrief(
            category=category,
            label=label,
            lead=str(payload.get("lead", "")).strip(),
            stories=list(payload.get("stories", []))[:max_story_count],
            watch=str(payload.get("watch", "")).strip(),
        )

    def create_radio_show(
        self,
        *,
        briefs: list[CategoryBrief],
        quiet_categories: list[str],
        opening_pair: tuple[str, str],
        start_iso: str,
        end_iso: str,
    ) -> RadioShow:
        system_instruction = (
            "You are writing a Korean two-person morning news radio script. "
            "The voice should feel calm, informed, concise, and conversational. "
            f"Use the exact speaker labels {self.config.host_name} and {self.config.analyst_name} "
            "on every dialogue line. "
            "Summarize meaning and implications, not headlines. "
            "Avoid hype, avoid unverified claims, and mention uncertainty when needed."
        )

        prompt = f"""
지난 24시간 기준 라디오 대본을 작성하세요.
시간 범위: {start_iso} ~ {end_iso}
반드시 JSON 객체 하나만 반환하세요.

형식:
- show_title: 1줄 제목
- show_summary: 2문장 이하 요약
- estimated_minutes: 6~10 사이 정수
- script_markdown: 마크다운 문자열

대본 규칙:
- `{self.config.host_name}:`와 `{self.config.analyst_name}:` 라벨을 정확히 사용
- 오프닝은 아래 두 문장을 그대로 사용
- 각 분야는 "HOST 질문 -> ANALYST 핵심요약(1~3문장) -> HOST 추가 질문 -> ANALYST 답변" 구조로 작성
- 기사 제목을 기계적으로 반복하지 말 것
- 대신 "무슨 변화가 있었는지", "왜 중요한지", "뭘 더 봐야 하는지"를 말할 것
- `ANALYST: 분야별로 상위 세 건만...` 같은 운영 설명은 넣지 말 것
- 마지막에는 quiet_categories가 있으면 "오늘은 ... 분야에서 기준 점수를 넘는 특정 기사가 많지 않았다"는 식으로 한 번만 언급
- 기사 URL은 대본에 직접 쓰지 말 것
- 투자 조언, 선정적 표현, 과장된 단정 금지

opening_pair:
{_json_dumps(list(opening_pair))}

quiet_categories:
{_json_dumps(quiet_categories)}

브리프:
{_json_dumps([brief.to_dict() for brief in briefs])}
""".strip()

        payload = self._generate_json(
            model=self.config.editor_model,
            system_instruction=system_instruction,
            prompt=prompt,
            max_output_tokens=self.config.max_output_tokens + 1024,
            temperature=0.45,
        )
        script_markdown = str(payload.get("script_markdown", "")).strip()
        return RadioShow(
            show_title=str(payload.get("show_title", "Morning Radio Briefing")).strip(),
            show_summary=str(payload.get("show_summary", "")).strip(),
            estimated_minutes=int(payload.get("estimated_minutes", 8)),
            script_markdown=script_markdown,
            script_plaintext=_markdown_to_plaintext(script_markdown),
            quiet_categories=quiet_categories,
        )

    def generate_audio(self, script_text: str) -> tuple[bytes, str]:
        attempts = self.config.tts_retry_count + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return self._generate_audio_once(script_text)
            except errors.ClientError as exc:
                last_error = exc
                status_code = getattr(exc, "status_code", None)
                is_retryable = status_code == 429 or "429" in str(exc)
                if attempt >= attempts - 1 or not is_retryable:
                    raise
                time.sleep(_retry_delay_seconds(str(exc), self.config.tts_retry_delay_seconds))
            except ValueError as exc:
                last_error = exc
                if attempt >= attempts - 1:
                    raise
                time.sleep(2)
        raise ValueError(f"Gemini TTS failed after retries: {last_error}")

    def _generate_audio_once(self, script_text: str) -> tuple[bytes, str]:
        response = self.client.models.generate_content(
            model=self.config.tts_model,
            contents=self._build_tts_prompt(script_text),
            config=types.GenerateContentConfig(
                responseModalities=["AUDIO"],
                speechConfig=types.SpeechConfig(
                    languageCode="ko-KR",
                    multiSpeakerVoiceConfig=types.MultiSpeakerVoiceConfig(
                        speakerVoiceConfigs=[
                            types.SpeakerVoiceConfig(
                                speaker=self.config.host_name,
                                voiceConfig=types.VoiceConfig(
                                    prebuiltVoiceConfig=types.PrebuiltVoiceConfig(
                                        voiceName=self.config.host_voice,
                                    ),
                                ),
                            ),
                            types.SpeakerVoiceConfig(
                                speaker=self.config.analyst_name,
                                voiceConfig=types.VoiceConfig(
                                    prebuiltVoiceConfig=types.PrebuiltVoiceConfig(
                                        voiceName=self.config.analyst_voice,
                                    ),
                                ),
                            ),
                        ],
                    ),
                ),
            ),
        )

        candidates = getattr(response, "candidates", None) or []
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                inline_data = getattr(part, "inline_data", None)
                if inline_data and getattr(inline_data, "data", None):
                    return inline_data.data, inline_data.mime_type or "audio/wav"

        raise ValueError("Gemini TTS response did not contain audio data.")

    def _build_tts_prompt(self, script_text: str) -> str:
        transcript = _format_tts_transcript(script_text)
        return (
            "# Korean Morning Radio TTS\n"
            "Generate audio only for the transcript below.\n"
            "Do not add explanations, labels, or extra narration.\n"
            "Blank lines indicate silent handoff beats and must not be spoken.\n\n"
            "## Director Notes\n"
            "- Deliver at approximately "
            f"{self.config.tts_speed_multiplier:.2f}x the pace of a standard Korean radio briefing.\n"
            "- Keep diction crisp and natural, never rushed or clipped.\n"
            "- After each speaker change, leave a clean pause about "
            f"{self.config.tts_turn_pause_multiplier:.2f}x longer than a normal broadcast handoff.\n"
            "- Maintain a calm, bright morning-news tone with steady loudness.\n"
            "- Read each speaker turn exactly as written in Korean.\n\n"
            "## Transcript\n"
            f"{transcript}"
        )


def _format_tts_transcript(script_text: str) -> str:
    lines = [line.strip() for line in script_text.splitlines() if line.strip()]
    return "\n\n".join(lines)


def _retry_delay_seconds(message: str, default_seconds: int) -> int:
    match = re.search(r"retry in ([0-9.]+)s", message, flags=re.IGNORECASE)
    if match:
        return max(1, int(float(match.group(1))) + 1)

    match = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+)s", message, flags=re.IGNORECASE)
    if match:
        return max(1, int(match.group(1)))

    return max(1, default_seconds)
