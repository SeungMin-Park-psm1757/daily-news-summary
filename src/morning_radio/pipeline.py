from __future__ import annotations

import json
import re
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from morning_radio.config import AppConfig
from morning_radio.gemini import GeminiEditor
from morning_radio.models import CategoryBrief, NewsItem, RadioShow
from morning_radio.news_sources import CATEGORIES, collect_news, enrich_articles, flatten_news
from morning_radio.telegram import send_digest_and_audio

OPENING_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "좋은 아침입니다. 잠 깨는 데 필요한 뉴스만 가볍게 챙겨볼게요.",
        "밤사이 흐름만 짧고 선명하게 정리해드리겠습니다.",
    ),
    (
        "아침 공기처럼 가볍게, 오늘 꼭 알아둘 소식만 먼저 짚어보겠습니다.",
        "복잡한 설명은 덜고 중요한 변화만 바로 들어가보죠.",
    ),
    (
        "하루 시작 전에 머릿속 정리부터 해보죠.",
        "밤새 쌓인 뉴스 가운데 핵심만 골라 전해드리겠습니다.",
    ),
    (
        "출근길이나 커피 한 잔 앞에서 듣기 좋게 준비했습니다.",
        "오늘도 필요한 뉴스만 짧게 묶어보겠습니다.",
    ),
    (
        "아침엔 길게 말할 필요 없죠.",
        "지금 알아야 할 흐름만 빠르게 훑어보겠습니다.",
    ),
    (
        "오늘 하루의 온도를 정할 뉴스부터 먼저 보겠습니다.",
        "무거운 내용도 최대한 간결하게 풀어드릴게요.",
    ),
    (
        "바쁜 아침이니 바로 핵심으로 들어가겠습니다.",
        "지난밤 가장 눈에 띈 변화만 추려왔습니다.",
    ),
    (
        "아침 루틴에 뉴스 한 스푼만 얹어보죠.",
        "한눈에 흐름이 잡히게 정리해드리겠습니다.",
    ),
    (
        "오늘도 정신없이 시작되기 전에 큰 그림부터 같이 보겠습니다.",
        "세부보다 방향이 보이도록 짧게 묶어드릴게요.",
    ),
    (
        "잠깐만 들어도 오늘 뉴스 감이 오도록 준비했습니다.",
        "핵심 이슈부터 순서대로 바로 들어가보죠.",
    ),
)

TITLE_STOPWORDS = {
    "속보",
    "단독",
    "상보",
    "뉴스특보",
    "종합",
    "오늘",
    "관련",
    "브리핑",
    "이란",
    "대한",
    "위한",
    "통해",
}

DEDUP_STEMS = (
    "우원식",
    "개헌",
    "지방선거",
    "국민투표",
    "한동훈",
    "중동",
    "유가",
    "전쟁",
    "휴전",
    "트럼프",
    "김여정",
    "미사일",
    "금리",
    "환율",
    "ai",
    "양자",
)


def run_pipeline(config: AppConfig) -> Path:
    now_utc = datetime.now(tz=UTC)
    start_utc = now_utc - timedelta(hours=config.hours_back)
    run_dir = config.output_dir / now_utc.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    news_by_category = collect_news(
        hours_back=config.hours_back,
        per_query_limit=config.per_query_limit,
        now=now_utc,
    )
    selected_by_category = {
        category.key: _select_top_articles(news_by_category.get(category.key, []), config)
        for category in CATEGORIES
    }
    enrich_articles(
        [
            article
            for category in CATEGORIES
            for article in selected_by_category.get(category.key, [])
        ]
    )

    quiet_categories = [
        category.label
        for category in CATEGORIES
        if not selected_by_category.get(category.key)
    ]
    opening_pair = _opening_pair(now_utc.astimezone(config.timezone))

    briefs = _build_briefs(config, selected_by_category)
    show = _build_show(config, briefs, quiet_categories, opening_pair, start_utc, now_utc)
    message_digest = _render_message_digest(show.show_title, briefs, quiet_categories)

    _write_json(
        run_dir / "news_items.json",
        {
            "generated_at": now_utc.isoformat(),
            "articles": [item.to_dict() for item in flatten_news(news_by_category)],
        },
    )
    _write_json(
        run_dir / "selected_items.json",
        {
            category.label: [item.to_dict() for item in selected_by_category.get(category.key, [])]
            for category in CATEGORIES
        },
    )
    _write_json(run_dir / "category_briefs.json", [brief.to_dict() for brief in briefs])
    _write_json(run_dir / "radio_show.json", show.to_dict())
    (run_dir / "radio_script.md").write_text(show.script_markdown + "\n", encoding="utf-8")
    (run_dir / "radio_script.txt").write_text(show.script_plaintext + "\n", encoding="utf-8")
    (run_dir / "message_digest.md").write_text(message_digest, encoding="utf-8")

    audio_metadata: dict[str, Any] = {"generated": False}
    audio_path: Path | None = None
    if config.tts_enabled:
        try:
            editor = GeminiEditor(config)
            audio_bytes, mime_type = editor.generate_audio(show.script_plaintext)
            audio_path = _write_audio_output(run_dir, audio_bytes, mime_type)
            audio_metadata = {"generated": True, "mime_type": mime_type, "path": audio_path.name}
        except Exception as exc:  # pragma: no cover - resilience path
            audio_metadata = {"generated": False, "error": str(exc)}

    telegram_metadata: dict[str, Any] = {"sent": False}
    if config.telegram_enabled:
        try:
            telegram_metadata = send_digest_and_audio(
                config=config,
                digest_markdown=message_digest,
                title=show.show_title,
                audio_path=audio_path,
            )
        except Exception as exc:  # pragma: no cover - resilience path
            telegram_metadata = {"sent": False, "error": str(exc)}

    summary = _render_summary(
        config=config,
        run_dir=run_dir,
        start_utc=start_utc,
        end_utc=now_utc,
        news_by_category=news_by_category,
        selected_by_category=selected_by_category,
        briefs=briefs,
        show=show,
        audio_metadata=audio_metadata,
        telegram_metadata=telegram_metadata,
    )
    (run_dir / "summary.md").write_text(summary, encoding="utf-8")
    _write_json(
        run_dir / "run_metadata.json",
        {
            "generated_at": now_utc.isoformat(),
            "start_utc": start_utc.isoformat(),
            "end_utc": now_utc.isoformat(),
            "llm_enabled": config.llm_enabled,
            "tts_enabled": config.tts_enabled,
            "audio": audio_metadata,
            "telegram": telegram_metadata,
            "quiet_categories": quiet_categories,
            "run_dir": str(run_dir),
        },
    )
    return run_dir


def _opening_pair(local_dt: datetime) -> tuple[str, str]:
    index = local_dt.toordinal() % len(OPENING_PATTERNS)
    return OPENING_PATTERNS[index]


def _select_top_articles(items: list[NewsItem], config: AppConfig) -> list[NewsItem]:
    ranked = sorted(items, key=lambda article: (article.score, article.published_at), reverse=True)
    selected: list[NewsItem] = []
    for article in ranked:
        if article.score < config.score_threshold:
            continue
        if any(_is_duplicate_story(article, existing) for existing in selected):
            continue
        selected.append(article)
        if len(selected) >= config.max_story_count:
            break
    return selected


def _is_duplicate_story(left: NewsItem, right: NewsItem) -> bool:
    left_tokens = _title_tokens(left.title)
    right_tokens = _title_tokens(right.title)
    overlap = left_tokens & right_tokens
    if len(overlap) >= 3:
        return True

    if left_tokens and right_tokens:
        jaccard = len(overlap) / len(left_tokens | right_tokens)
        if jaccard >= 0.45:
            return True

    left_subject = _headline_subject(left.title)
    right_subject = _headline_subject(right.title)
    stem_overlap = {
        stem for stem in DEDUP_STEMS if stem.lower() in left.title.lower() and stem.lower() in right.title.lower()
    }
    if left_subject and left_subject == right_subject and len(stem_overlap) >= 2:
        return True
    return False


def _title_tokens(title: str) -> set[str]:
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", title)
    cleaned = cleaned.replace("“", " ").replace("”", " ").replace('"', " ")
    tokens = set()
    for token in re.findall(r"[0-9A-Za-z가-힣]+", cleaned):
        lowered = token.lower()
        if len(lowered) <= 1 or lowered in TITLE_STOPWORDS:
            continue
        tokens.add(lowered)
    return tokens


def _headline_subject(title: str) -> str:
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", title).strip()
    cleaned = cleaned.replace("“", '"').replace("”", '"')
    match = re.match(r'(.+?)\s*"(.+?)"', cleaned)
    if match:
        return match.group(1).strip()
    return cleaned.split(" ", 1)[0].strip()


def _build_briefs(
    config: AppConfig,
    selected_by_category: dict[str, list[NewsItem]],
) -> list[CategoryBrief]:
    if config.llm_enabled:
        editor = GeminiEditor(config)
        briefs: list[CategoryBrief] = []
        for category in CATEGORIES:
            selected = selected_by_category.get(category.key, [])
            if not selected:
                briefs.append(_fallback_brief(category.key, category.label, []))
                continue
            try:
                briefs.append(
                    editor.create_category_brief(
                        category=category.key,
                        label=category.label,
                        articles=selected,
                        max_story_count=config.max_story_count,
                    ),
                )
            except Exception:
                briefs.append(_fallback_brief(category.key, category.label, selected))
        return briefs

    return [
        _fallback_brief(category.key, category.label, selected_by_category.get(category.key, []))
        for category in CATEGORIES
    ]


def _build_show(
    config: AppConfig,
    briefs: list[CategoryBrief],
    quiet_categories: list[str],
    opening_pair: tuple[str, str],
    start_utc: datetime,
    end_utc: datetime,
) -> RadioShow:
    if config.llm_enabled:
        try:
            editor = GeminiEditor(config)
            return editor.create_radio_show(
                briefs=briefs,
                quiet_categories=quiet_categories,
                opening_pair=opening_pair,
                start_iso=start_utc.isoformat(),
                end_iso=end_utc.isoformat(),
            )
        except Exception:
            return _fallback_show(config, briefs, quiet_categories, opening_pair, start_utc, end_utc)
    return _fallback_show(config, briefs, quiet_categories, opening_pair, start_utc, end_utc)


def _fallback_brief(category: str, label: str, items: list[NewsItem]) -> CategoryBrief:
    if not items:
        return CategoryBrief(
            category=category,
            label=label,
            lead=f"오늘은 {label} 분야에서 기준 점수를 넘는 뚜렷한 기사가 많지 않았습니다.",
            stories=[],
            watch="후속 보도가 더 쌓이면 다음 실행에서 다시 포착하겠습니다.",
        )

    stories = [
        {
            "headline": article.title,
            "angle": _condense_article(article),
            "why_it_matters": _why_it_matters(label),
            "source_urls": [article.resolved_url or article.url],
            "score": article.score,
            "source": article.source,
        }
        for article in items
    ]

    return CategoryBrief(
        category=category,
        label=label,
        lead=_compose_lead(label, stories),
        stories=stories,
        watch=_compose_follow_up_answer(label, stories),
    )


def _fallback_show(
    config: AppConfig,
    briefs: list[CategoryBrief],
    quiet_categories: list[str],
    opening_pair: tuple[str, str],
    start_utc: datetime,
    end_utc: datetime,
) -> RadioShow:
    local_date = end_utc.astimezone(config.timezone).strftime("%m월 %d일")
    lines = [
        f"# {local_date} 아침 뉴스 라디오",
        "",
        f"{config.host_name}: {opening_pair[0]}",
        f"{config.analyst_name}: {opening_pair[1]}",
    ]

    for brief in briefs:
        if not brief.stories:
            continue
        lines.append("")
        lines.append(f"{config.host_name}: 먼저 {brief.label}부터 짚어보죠. 오늘 가장 중요한 흐름은 뭔가요?")
        lines.append(f"{config.analyst_name}: {brief.lead}")
        lines.append(f"{config.host_name}: {_follow_up_question(brief.label, brief.stories)}")
        lines.append(f"{config.analyst_name}: {brief.watch}")

    if quiet_categories:
        lines.append("")
        lines.append(f"{config.host_name}: 오늘 상대적으로 조용했던 분야도 있었나요?")
        lines.append(
            f"{config.analyst_name}: 오늘은 {', '.join(quiet_categories)} 분야에서 기준 점수를 넘는 특정 기사가 많지 않았습니다."
        )

    lines.append("")
    lines.append(f"{config.host_name}: 여기까지 {local_date} 아침 브리핑이었습니다.")
    lines.append(
        f"{config.analyst_name}: 숫자나 발언처럼 민감한 정보는 원문 기사로 다시 확인하면서 다음 업데이트에서 이어가겠습니다."
    )
    script_markdown = "\n".join(lines).strip()

    return RadioShow(
        show_title=f"{local_date} 아침 뉴스 라디오",
        show_summary=f"{start_utc.isoformat()}부터 {end_utc.isoformat()}까지의 뉴스 가운데 상위 기사만 추린 브리핑입니다.",
        estimated_minutes=7,
        script_markdown=script_markdown,
        script_plaintext=script_markdown.replace("# ", ""),
        quiet_categories=quiet_categories,
    )


def _compose_lead(label: str, stories: list[dict[str, Any]]) -> str:
    summaries = [_ensure_sentence(story["angle"]) for story in stories[:3]]
    if len(summaries) == 1:
        return summaries[0]
    if len(summaries) == 2:
        return f"{summaries[0]} {summaries[1]}"
    return f"{summaries[0]} {summaries[1]} {summaries[2]}"


def _compose_follow_up_answer(label: str, stories: list[dict[str, Any]]) -> str:
    primary = stories[0]
    reason = _ensure_sentence(primary["why_it_matters"])
    follow_up = _ensure_sentence(_watch_message(label))
    return f"특히 첫 번째 이슈가 중요합니다. {reason} {follow_up}"


def _follow_up_question(label: str, stories: list[dict[str, Any]]) -> str:
    headline = stories[0]["headline"]
    subject = _headline_subject(headline)

    topic_hints = {
        "한국정치": f"{subject} 관련 주장 가운데 핵심 쟁점은 뭔가요?",
        "세계정세": "이 흐름이 국제 정세와 시장에 어떤 의미를 주나요?",
        "군사학": "군사적으로 보면 가장 먼저 읽어야 할 포인트는 뭔가요?",
        "무기체계": "실제 전력 변화로 이어질 가능성은 어떻게 보세요?",
        "AI": "이 변화가 산업 현장에 주는 신호는 뭔가요?",
        "양자": "이게 기술 전환 관점에서 왜 눈에 띄는 건가요?",
        "경제": "시장과 정책 측면에서 어디를 가장 먼저 봐야 할까요?",
    }
    return topic_hints.get(label, "이 가운데 먼저 짚어볼 지점은 뭔가요?")


def _condense_article(article: NewsItem) -> str:
    text = article.summary or ""
    text = _strip_repetition(text, article.title)
    text = _strip_repetition(text, article.source)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\b[\w.-]+\.[a-z]{2,}\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" -:;,.")
    if len(text) < 18:
        return _headline_fallback(article.title)

    sentences = re.split(r"(?<=[.!?])\s+|(?<=다\.)\s+", text)
    summary = " ".join(sentence.strip() for sentence in sentences[:2] if sentence.strip())
    summary = re.sub(r"\s+", " ", summary).strip(" -:;,.")
    if len(summary) < 18:
        return _headline_fallback(article.title)
    return _ensure_sentence(summary)


def _strip_repetition(text: str, fragment: str) -> str:
    if not text or not fragment:
        return text
    pattern = re.escape(fragment)
    stripped = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return stripped.strip()


def _headline_fallback(title: str) -> str:
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", title).strip()
    cleaned = cleaned.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")

    quote_match = re.match(r'(.+?)\s*"(.+?)"', cleaned)
    if quote_match:
        speaker = _normalize_speaker(quote_match.group(1))
        claim = quote_match.group(2).strip(" ,.-")
        return _ensure_sentence(f"{speaker} 측이 {claim}라는 입장을 내놨습니다")

    if "…" in cleaned:
        left, right = cleaned.split("…", 1)
        left = left.strip(" ,.-")
        right = right.strip(" ,.-")
        if left and right:
            return _ensure_sentence(f"{left}와 관련해 {right} 흐름이 부각됐습니다")

    if " - " in cleaned:
        left, right = cleaned.rsplit(" - ", 1)
        left = left.strip(" ,.-")
        right = right.strip(" ,.-")
        if left and right:
            return _ensure_sentence(f"{left}와 관련해 {right} 내용이 전해졌습니다")

    if ":" in cleaned:
        left, right = cleaned.split(":", 1)
        left = left.strip(" ,.-")
        right = right.strip(" ,.-")
        if left and right:
            return _ensure_sentence(f"{left}를 두고 {right} 내용이 나왔습니다")

    return _ensure_sentence(f"{cleaned}와 관련한 움직임이 보도됐습니다")


def _normalize_speaker(raw: str) -> str:
    speaker = raw.strip(" ,.-")
    speaker = re.sub(r",.*$", "", speaker).strip()
    if "…" in speaker:
        speaker = speaker.split("…")[-1].strip() or speaker
    if "·" in speaker and len(speaker) > 12:
        speaker = speaker.split("·")[0].strip()
    speaker = speaker.rstrip("에은는이가를을")
    return speaker or "관계자"


def _ensure_sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""
    if cleaned[-1] not in {".", "!", "?"}:
        cleaned += "."
    return cleaned


def _why_it_matters(label: str) -> str:
    reasons = {
        "한국정치": "정책 일정과 정치권의 힘의 균형에 직접 영향을 줄 수 있습니다.",
        "세계정세": "외교, 에너지, 공급망 흐름에 연쇄적으로 연결될 수 있습니다.",
        "군사학": "안보 환경과 군사적 긴장 수준을 판단하는 데 중요합니다.",
        "무기체계": "전력 변화와 방산 수요 방향을 가늠하는 신호가 됩니다.",
        "AI": "모델 경쟁과 산업 적용 속도를 판단하는 데 중요합니다.",
        "양자": "상용화 속도와 보안 기술 전환 시점을 가늠하는 재료가 됩니다.",
        "경제": "시장 심리와 정책 대응 전망에 바로 영향을 줄 수 있습니다.",
    }
    return reasons.get(label, "분야 흐름을 읽는 데 의미가 있습니다.")


def _watch_message(label: str) -> str:
    follow_ups = {
        "한국정치": "추가 공식 발표나 일정 확정 여부를 확인해보는 것이 좋겠습니다.",
        "세계정세": "후속 외교 일정과 국제 시장 반응을 함께 볼 필요가 있습니다.",
        "군사학": "현장 동향과 각국 공식 발표의 간극을 계속 확인해야 합니다.",
        "무기체계": "실전 배치 여부와 실제 계약 규모가 뒤따르는지 보겠습니다.",
        "AI": "실제 제품화와 기업 간 제휴가 이어지는지 지켜보겠습니다.",
        "양자": "기술 발표가 실사용 단계로 이어지는지 확인이 필요합니다.",
        "경제": "정책 대응과 시장 가격 변화를 함께 봐야 합니다.",
    }
    return follow_ups.get(label, "후속 기사에서 구체성이 더 붙는지 보겠습니다.")


def _render_message_digest(
    show_title: str,
    briefs: list[CategoryBrief],
    quiet_categories: list[str],
) -> str:
    lines = [
        f"# {show_title} 요약",
        "",
        "필요한 기사만 빠르게 찾아볼 수 있도록 핵심 제목과 한 줄 요약만 정리했습니다.",
    ]

    for brief in briefs:
        if not brief.stories:
            continue
        lines.append("")
        lines.append(f"## {brief.label}")
        for story in brief.stories:
            summary = _message_summary(story)
            lines.append(f"- **{story['headline']}**")
            lines.append(f"  {summary}")

    if quiet_categories:
        lines.append("")
        lines.append("## 저신호 분야")
        lines.append(f"- 오늘은 {', '.join(quiet_categories)} 분야에서 기준 점수를 넘는 특정 기사가 많지 않았습니다.")

    return "\n".join(lines).strip() + "\n"


def _message_summary(story: dict[str, Any]) -> str:
    candidate = story.get("message_summary") or story.get("angle") or story.get("why_it_matters") or ""
    headline_tokens = _title_tokens(story.get("headline", ""))
    summary_tokens = _title_tokens(candidate)
    if headline_tokens and summary_tokens:
        overlap_ratio = len(headline_tokens & summary_tokens) / len(headline_tokens)
        if overlap_ratio >= 0.55 and story.get("why_it_matters"):
            candidate = str(story["why_it_matters"])
    return _ensure_sentence(str(candidate))


def _render_summary(
    *,
    config: AppConfig,
    run_dir: Path,
    start_utc: datetime,
    end_utc: datetime,
    news_by_category: dict[str, list[NewsItem]],
    selected_by_category: dict[str, list[NewsItem]],
    briefs: list[CategoryBrief],
    show: RadioShow,
    audio_metadata: dict[str, Any],
    telegram_metadata: dict[str, Any],
) -> str:
    lines = [
        f"# {show.show_title}",
        "",
        f"- 실행 디렉터리: `{run_dir}`",
        f"- 시간 범위(UTC): `{start_utc.isoformat()}` ~ `{end_utc.isoformat()}`",
        f"- LLM 사용: `{config.llm_enabled}`",
        f"- TTS 사용: `{config.tts_enabled}`",
        f"- 오디오 생성: `{audio_metadata.get('generated', False)}`",
        f"- 텔레그램 전송: `{telegram_metadata.get('sent', False)}`",
        f"- 점수 임계치: `{config.score_threshold}`",
        "",
        "## 기사 수집 현황",
    ]

    for category in CATEGORIES:
        total_count = len(news_by_category.get(category.key, []))
        selected = selected_by_category.get(category.key, [])
        selected_text = ", ".join(f"{article.score:.1f}" for article in selected) or "-"
        lines.append(
            f"- {category.label}: 전체 {total_count}건, 선정 {len(selected)}건, 점수 {selected_text}"
        )

    lines.append("")
    lines.append("## 브리프 개요")
    for brief in briefs:
        lines.append(f"- {brief.label}: {brief.lead}")

    if show.quiet_categories:
        lines.append("")
        lines.append("## 저신호 분야")
        lines.append(f"- {', '.join(show.quiet_categories)}")

    lines.append("")
    lines.append("## 라디오 요약")
    lines.append(show.show_summary)
    lines.append("")
    lines.append("## 산출물")
    lines.append("- `news_items.json`")
    lines.append("- `selected_items.json`")
    lines.append("- `category_briefs.json`")
    lines.append("- `radio_show.json`")
    lines.append("- `radio_script.md`")
    lines.append("- `radio_script.txt`")
    lines.append("- `message_digest.md`")
    if audio_metadata.get("generated") and audio_metadata.get("path"):
        lines.append(f"- `{audio_metadata['path']}`")
    return "\n".join(lines).strip() + "\n"


def _audio_suffix(mime_type: str) -> str:
    lowered = mime_type.lower()
    if "wav" in lowered:
        return ".wav"
    if "mpeg" in lowered or "mp3" in lowered:
        return ".mp3"
    if "ogg" in lowered:
        return ".ogg"
    return ".bin"


def _write_audio_output(run_dir: Path, audio_bytes: bytes, mime_type: str) -> Path:
    lowered = mime_type.lower()
    if "audio/l16" in lowered or "codec=pcm" in lowered:
        sample_rate = _parse_sample_rate(lowered)
        output_path = run_dir / "audio.wav"
        pcm_little_endian = b"".join(
            audio_bytes[index + 1 : index + 2] + audio_bytes[index : index + 1]
            for index in range(0, len(audio_bytes), 2)
            if len(audio_bytes[index : index + 2]) == 2
        )
        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_little_endian)
        return output_path

    suffix = _audio_suffix(mime_type)
    output_path = run_dir / f"audio{suffix}"
    output_path.write_bytes(audio_bytes)
    return output_path


def _parse_sample_rate(mime_type: str) -> int:
    match = re.search(r"rate=(\d+)", mime_type)
    if match:
        return int(match.group(1))
    return 24000


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
