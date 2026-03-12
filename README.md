# Morning Radio

Morning Radio builds a weekday Korean morning news briefing from the last 24 hours and ships it as:

- a ranked article digest
- a two-speaker radio script
- an optional MP3 audio file
- a Telegram summary and audio delivery
- a simple HTML archive page

## What It Does

- Collects recent news for Korea politics, global affairs, military strategy, weapon systems, AI, quantum, and the economy.
- Scores articles by recency, source reliability, signal terms, and low-signal penalties.
- Clusters near-duplicate coverage so one event is represented once.
- Produces a messenger digest with a short summary and a short "why it matters" line.
- Generates a two-speaker Korean radio script with a calm host and a brighter analyst voice.
- Uses Gemini TTS to create an MP3 when enabled.
- Writes per-run output plus an HTML archive index.

## Quick Start

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
```

Set `GEMINI_API_KEY` in `.env`, then run:

```bash
morning-radio
```

For a no-API smoke test:

```bash
morning-radio --skip-llm --skip-tts
```

## Main Outputs

Each run writes to `output/YYYYMMDD-HHMMSS/`.

- `news_items.json`: all collected items
- `selected_items.json`: clustered and selected representatives
- `category_briefs.json`: category-level brief objects
- `radio_show.json`: final radio show metadata
- `radio_script.md`: markdown radio script
- `radio_script.txt`: plain text transcript for TTS
- `message_digest.md`: Telegram-friendly digest
- `summary.md`: run summary and quota log
- `index.html`: run-level archive page
- `audio.mp3`: generated TTS audio when available
- `run_metadata.json`: machine-readable run metadata

The root `output/index.html` file lists recent runs as a lightweight archive page.

## Key Environment Variables

- `GEMINI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_THREAD_ID`
- `MORNING_RADIO_ENABLE_TTS`
- `MORNING_RADIO_TTS_MODE`
  - `daily`: lighter weekday mode
  - `manual`: higher-bitrate manual mode
- `MORNING_RADIO_HOST_VOICE`
- `MORNING_RADIO_ANALYST_VOICE`
- `MORNING_RADIO_TTS_SPEED`
- `MORNING_RADIO_TTS_TURN_PAUSE`
- `MORNING_RADIO_TTS_RETRY_COUNT`
- `MORNING_RADIO_TTS_RETRY_DELAY_SECONDS`
- `MORNING_RADIO_ARCHIVE_LIMIT`

## GitHub Actions

The workflow is defined in `.github/workflows/daily-radio.yml`.

- Schedule: weekday `06:00 KST`
- The workflow uses `daily` TTS mode by default
- Telegram delivery is enabled when the Telegram secrets are present

Required secrets:

- `GEMINI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_THREAD_ID` only for topic-based groups

## Notes

- Text generation and TTS share the same Gemini API key but use different models.
- The main free-tier bottleneck is TTS, not text generation.
- If TTS fails, the pipeline still delivers the text digest and preserves run metadata.
- If LLM generation fails, the package falls back to heuristic summaries so the pipeline still completes.
