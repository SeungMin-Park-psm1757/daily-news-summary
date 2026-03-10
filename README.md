# Morning Radio

지난 24시간 동안의 주요 뉴스를 수집해서 한국어 아침 라디오 형식의 대화형 브리핑으로 만드는 Python MVP입니다.

## 무엇을 하나요

- 한국정치, 세계정세, 군사, 무기체계, AI, 양자, 경제 분야의 뉴스 후보를 RSS로 수집합니다.
- 지난 24시간 기준으로 기사 후보를 추리고 중복을 제거합니다.
- Gemini로 카테고리별 핵심 브리프를 만들고, `HOST`와 `ANALYST`가 대화하는 라디오 대본으로 엮습니다.
- 선택적으로 Gemini TTS를 사용해 오디오 파일까지 생성합니다.
- GitHub Actions 스케줄 실행을 바로 붙일 수 있습니다.

## 빠른 시작

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
```

`.env`에 `GEMINI_API_KEY`를 넣고 실행합니다. 실행 시 현재 디렉터리의 `.env`를 자동으로 읽습니다.

```bash
morning-radio
```

텍스트만 빠르게 검증하려면:

```bash
morning-radio --skip-llm --skip-tts
```

## 출력물

기본적으로 `output/YYYYMMDD-HHMMSS/` 아래에 생성됩니다.

- `news_items.json`: 수집된 기사 후보
- `selected_items.json`: 점수 기준을 통과한 상위 기사
- `category_briefs.json`: 카테고리별 브리프
- `radio_show.json`: 최종 라디오 메타데이터
- `radio_script.md`: 라디오 대본
- `radio_script.txt`: TTS용 정리 텍스트
- `message_digest.md`: 텔레그램/카카오톡 공유용 마크다운 요약
- `summary.md`: 실행 요약
- `audio.wav`: 생성된 음성
- `run_metadata.json`: 실행 메타데이터

## GitHub Actions

워크플로는 [`.github/workflows/daily-radio.yml`](.github/workflows/daily-radio.yml)에 들어 있습니다.

- 기본 스케줄은 `21:10 UTC`, 즉 한국 시간 기준 `06:10 KST`입니다.
- 저장소 시크릿에 `GEMINI_API_KEY`를 추가하세요.
- 텔레그램 자동 발송까지 쓰려면 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 시크릿을 추가하세요.
- 토픽형 그룹에 보낼 경우에만 `TELEGRAM_THREAD_ID`를 추가하면 됩니다.
- 현재 워크플로는 TTS를 켜 둔 상태라, 오디오가 생성되면 텍스트 요약 뒤에 함께 전송합니다.

## 설계 메모

- 토큰 폭발을 막기 위해 원문 전체가 아니라 RSS 메타데이터 위주로 먼저 줄입니다.
- 유사한 제목은 점수와 제목 토큰을 기준으로 중복 제거하고, 카테고리별 상위 기사만 남깁니다.
- 카테고리별 브리프와 최종 라디오 대본 생성을 분리해서, 최종 생성 시점의 컨텍스트를 작게 유지합니다.
- API 키가 없으면 휴리스틱 모드로 동작해서 파이프라인 자체는 검증할 수 있습니다.
