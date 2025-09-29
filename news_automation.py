# 북한 군사/정치 뉴스 필터링 + 요약 포맷 개선 버전

def is_north_korea_military_or_politics(article):
    """북한 군사/정치 뉴스 필터링 함수"""
    keywords = ['북한', '조선', '군', '군사', '정치', '김정은', '핵', '미사일']
    title = article.get('title', '')
    summary = article.get('summary', '')
    return any(k in title or k in summary for k in keywords)

async def collect_news_by_keyword(keyword, max_domestic=5, max_international=2):
    print(f"\n📰 [{keyword}] 뉴스 수집 중...")
    feeds = KEYWORD_FEEDS.get(keyword, [])
    domestic_articles = []
    international_articles = []

    async with aiohttp.ClientSession() as session:
        tasks = [fetch_feed(session, url) for url in feeds]
        feed_results = await asyncio.gather(*tasks)

    for feed_url, feed in zip(feeds, feed_results):
        if not feed or (feed.bozo and feed.bozo_exception):
            continue

        is_international = any(domain in feed_url for domain in ['nytimes.com', 'bbci.co.uk'])
        
        for entry in feed.entries[:10]:
            if not is_recent_article(entry.get('published')):
                continue

            # 북한 군사/정치 키워드 필터링 적용
            if keyword in ['군대', '정치'] and not is_north_korea_military_or_politics(entry):
                continue

            title = clean_text(entry.get('title', ''))
            if not title or len(title) < 10:
                continue

            summary = clean_text(entry.get('summary', entry.get('description', '')))
            article = {
                'title': title[:100],
                'link': entry.get('link', ''),
                'summary': summary,
                'published': entry.get('published', ''),
                'source': 'international' if is_international else 'domestic'
            }

            if is_international and len(international_articles) < max_international:
                international_articles.append(article)
            elif not is_international and len(domestic_articles) < max_domestic:
                domestic_articles.append(article)

            if len(domestic_articles) >= max_domestic and len(international_articles) >= max_international:
                break
        if len(domestic_articles) >= max_domestic and len(international_articles) >= max_international:
            break

    all_articles = sorted(domestic_articles + international_articles, 
                          key=lambda x: parsedate_tz(x['published']) if x['published'] else datetime.now(), 
                          reverse=True)
    return all_articles

def summarize_news_with_gemini(keyword, articles):
    if not articles:
        emoji = KEYWORD_EMOJIS.get(keyword, '📰')
        return f"{emoji} {keyword}\n• 오늘은 관련 주요 뉴스가 없었습니다."

    headlines = []
    articles_text_for_summary = ""
    for i, article in enumerate(articles[:5], 1):
        headlines.append(f"{i}. {article['title']}")
        source_type = "🌍해외" if article['source'] == 'international' else "🇰🇷국내"
        articles_text_for_summary += f"\n[{source_type} 기사 {i}]\n제목: {article['title']}\n내용: {article['summary'][:300]}\n"

    headlines_text = "\n".join(headlines)
    emoji = KEYWORD_EMOJIS.get(keyword, '📰')

    # Gemini 프롬프트: 군사/정치 핵심 이슈만, 불필요 정보 제외
    prompt = (
        f"'{keyword}' 관련 최신 뉴스 중 군사/정치 이슈만 골라 2~3줄로 요약해줘. "
        "날씨, 문화, 기타 비관련 내용은 모두 제외해줘. 각 뉴스 제목을 먼저 나열하고, 아래에 핵심만 요약해줘.\n"
        f"[뉴스 헤드라인 목록]\n{headlines_text}\n\n[참고용 뉴스 내용]\n{articles_text_for_summary}\n[요약]"
    )

    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.5,
                max_output_tokens=1000,
                top_p=0.8,
                top_k=40
            )
        )
        summary = response.text.strip()
        if not summary:
            summary = "AI 요약 생성에 실패했습니다."
        formatted_summary = (
            f"{emoji} {keyword}\n"
            f"[헤드라인]\n{headlines_text}\n\n"
            f"[요약]\n{summary.replace('*', '').strip()}"
        )
        return formatted_summary
    except Exception as e:
        error_summary = (
            f"{emoji} {keyword}\n"
            f"[헤드라인]\n{headlines_text}\n\n"
            f"[요약]\n* AI 요약 생성 중 오류가 발생했습니다. 헤드라인만 참고해주세요."
        )
        return error_summary
