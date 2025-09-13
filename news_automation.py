#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import asyncio
import json
import requests
import feedparser
import google.generativeai as genai
import edge_tts
from datetime import datetime, timedelta
from urllib.parse import quote
import tempfile
import base64

# 환경 변수 설정
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
KAKAO_ACCESS_TOKEN = os.getenv('KAKAO_ACCESS_TOKEN')

# API 키 확인
if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.")
    sys.exit(1)

if not KAKAO_ACCESS_TOKEN:
    print("❌ KAKAO_ACCESS_TOKEN 환경 변수가 설정되지 않았습니다.")
    sys.exit(1)

# Gemini API 설정
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# 키워드별 RSS 피드 설정
KEYWORD_FEEDS = {
    '군대': [
        'http://newssearch.naver.com/search.naver?where=rss&sort_type=1&query=군대',
        'http://newssearch.naver.com/search.naver?where=rss&sort_type=1&query=육군',
        'https://www.yna.co.kr/rss/northkorea.xml',
        'https://rss.nytimes.com/services/xml/rss/nyt/World.xml'  # 해외
    ],
    '정치': [
        'https://www.yna.co.kr/rss/politics.xml',
        'http://newssearch.naver.com/search.naver?where=rss&sort_type=1&query=정치',
        'https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml',  # 해외
        'http://feeds.bbci.co.uk/news/politics/rss.xml'  # 해외
    ],
    '주식': [
        'https://www.yna.co.kr/rss/economy.xml',
        'http://newssearch.naver.com/search.naver?where=rss&sort_type=1&query=주식',
        'http://newssearch.naver.com/search.naver?where=rss&sort_type=1&query=삼성전자',
        'https://rss.nytimes.com/services/xml/rss/nyt/Business.xml'  # 해외
    ],
    'AI': [
        'http://newssearch.naver.com/search.naver?where=rss&sort_type=1&query=인공지능',
        'http://newssearch.naver.com/search.naver?where=rss&sort_type=1&query=AI',
        'https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml',  # 해외
        'http://feeds.bbci.co.uk/news/technology/rss.xml'  # 해외
    ]
}

def is_recent_article(published_date, hours=24):
    """24시간 이내 기사인지 확인"""
    try:
        if not published_date:
            return True  # 날짜 정보 없으면 포함
        
        import time
        from email.utils import parsedate
        
        # RSS 날짜 파싱
        parsed = parsedate(published_date)
        if parsed:
            article_time = datetime(*parsed[:6])
            cutoff_time = datetime.now() - timedelta(hours=hours)
            return article_time > cutoff_time
        return True
    except:
        return True  # 파싱 실패 시 포함

def collect_news_by_keyword(keyword, max_articles_per_feed=5):
    """키워드별 뉴스 수집"""
    print(f"📰 [{keyword}] 뉴스 수집 중...")
    
    all_articles = []
    feeds = KEYWORD_FEEDS.get(keyword, [])
    
    for i, feed_url in enumerate(feeds):
        try:
            print(f"  피드 {i+1}/{len(feeds)} 수집 중: {feed_url}")
            
            # RSS 피드 파싱
            feed = feedparser.parse(feed_url)
            if feed.bozo:
                print(f"    ⚠️ RSS 파싱 오류: {feed_url}")
                continue
                
            # 최근 기사만 필터링
            recent_articles = []
            for entry in feed.entries[:max_articles_per_feed * 2]:  # 여유분 확보
                if is_recent_article(entry.get('published')):
                    # 해외 소스 판별
                    is_international = any(domain in feed_url for domain in ['nytimes.com', 'bbci.co.uk'])
                    
                    article = {
                        'title': entry.get('title', '제목 없음')[:100],
                        'link': entry.get('link', ''),
                        'summary': entry.get('summary', entry.get('description', ''))[:300],
                        'published': entry.get('published', ''),
                        'source': 'international' if is_international else 'domestic'
                    }
                    recent_articles.append(article)
                    
            all_articles.extend(recent_articles[:max_articles_per_feed])
            print(f"    ✅ {len(recent_articles[:max_articles_per_feed])}개 기사 수집")
            
        except Exception as e:
            print(f"    ❌ 피드 수집 실패 ({feed_url}): {str(e)}")
            continue
    
    # 국내/해외 기사 분리
    domestic_articles = [a for a in all_articles if a['source'] == 'domestic']
    international_articles = [a for a in all_articles if a['source'] == 'international']
    
    # 요청된 개수만큼 선택 (국내 3-5개, 해외 2개)
    selected_domestic = domestic_articles[:5]  # 최대 5개
    selected_international = international_articles[:2]  # 최대 2개
    
    print(f"  ✅ [{keyword}] 수집 완료: 국내 {len(selected_domestic)}개, 해외 {len(selected_international)}개")
    
    return selected_domestic + selected_international

def summarize_news_with_gemini(keyword, articles):
    """Gemini API로 뉴스 요약"""
    if not articles:
        return f"[{keyword}] 오늘은 주요 뉴스가 없었다."
    
    print(f"🤖 [{keyword}] AI 요약 생성 중...")
    
    # 기사 내용 정리
    articles_text = ""
    for i, article in enumerate(articles, 1):
        source_type = "해외" if article['source'] == 'international' else "국내"
        articles_text += f"\n[{source_type} 기사 {i}]\n제목: {article['title']}\n내용: {article['summary']}\n"
    
    # Gemini 프롬프트
    prompt = f"""
다음은 '{keyword}' 관련 오늘의 주요 뉴스들입니다. 이를 바탕으로 핵심 내용을 요약해주세요.

{articles_text}

요약 규칙:
1. 5-10개 문장으로 요약
2. 모든 문장은 "~했다", "~됐다", "~나타났다" 등으로 종료
3. 핵심 사실과 숫자를 포함
4. 국내외 소식을 균형있게 반영
5. 객관적이고 간결한 톤 유지

[{keyword}] 요약:
"""

    try:
        response = model.generate_content(prompt)
        summary = response.text.strip()
        
        if not summary:
            return f"[{keyword}] 요약 생성에 실패했다."
            
        print(f"  ✅ [{keyword}] 요약 완료 ({len(summary)}자)")
        return f"[{keyword}]\n{summary}"
        
    except Exception as e:
        print(f"  ❌ [{keyword}] 요약 실패: {str(e)}")
        return f"[{keyword}] 요약 생성 중 오류가 발생했다."

async def text_to_speech(text, output_file):
    """Edge TTS로 텍스트를 음성으로 변환"""
    try:
        print("🔊 음성 변환 중...")
        
        # 한국어 음성 설정
        communicate = edge_tts.Communicate(
            text=text,
            voice="ko-KR-SunHiNeural",  # 자연스러운 한국어 음성
            rate="+20%"  # 약간 빠르게
        )
        
        await communicate.save(output_file)
        print(f"  ✅ 음성 파일 생성 완료: {output_file}")
        return True
        
    except Exception as e:
        print(f"  ❌ 음성 변환 실패: {str(e)}")
        return False

def send_kakao_message(text_message, audio_file_path=None):
    """카카오톡 나에게 메시지 전송"""
    print("📱 카카오톡 메시지 전송 중...")
    
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {KAKAO_ACCESS_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    # 텍스트 메시지 구성
    template_object = {
        "object_type": "text",
        "text": text_message,
        "link": {
            "web_url": "https://news.naver.com",
            "mobile_web_url": "https://news.naver.com"
        }
    }
    
    data = {
        "template_object": json.dumps(template_object, ensure_ascii=False)
    }
    
    try:
        # 텍스트 메시지 전송
        response = requests.post(url, headers=headers, data=data)
        
        if response.status_code == 200:
            result = response.json()
            if result.get('result_code') == 0:
                print("  ✅ 텍스트 메시지 전송 성공")
                
                # 음성 파일이 있으면 추가 전송 시도
                if audio_file_path and os.path.exists(audio_file_path):
                    print("  📎 음성 파일 전송은 현재 지원되지 않아 텍스트로만 전송합니다.")
                
                return True
            else:
                print(f"  ❌ 카카오 API 오류: {result}")
                return False
        else:
            print(f"  ❌ HTTP 오류: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"  ❌ 메시지 전송 실패: {str(e)}")
        return False

async def main():
    """메인 실행 함수"""
    print("🚀 뉴스 요약 봇 시작")
    print(f"⏰ 실행 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 전체 요약 저장
    all_summaries = []
    
    # 키워드별 뉴스 수집 및 요약
    for keyword in KEYWORD_FEEDS.keys():
        print(f"\n{'='*50}")
        print(f"🔍 [{keyword}] 처리 시작")
        
        # 뉴스 수집
        articles = collect_news_by_keyword(keyword)
        
        # AI 요약
        summary = summarize_news_with_gemini(keyword, articles)
        all_summaries.append(summary)
        
        print(f"✅ [{keyword}] 처리 완료")
    
    # 전체 메시지 구성
    today = datetime.now().strftime('%Y년 %m월 %d일')
    full_message = f"📰 {today} 주요 뉴스 요약\n\n"
    full_message += "\n\n".join(all_summaries)
    full_message += f"\n\n⏰ 생성 시간: {datetime.now().strftime('%H:%M')}"
    
    print(f"\n{'='*50}")
    print("📝 최종 요약 생성 완료")
    print(f"📊 총 길이: {len(full_message)}자")
    
    # 음성 변환 시도
    audio_file = None
    try:
        audio_file = tempfile.mktemp(suffix='.wav')
        success = await text_to_speech(full_message, audio_file)
        if not success:
            audio_file = None
    except Exception as e:
        print(f"⚠️ 음성 변환 건너뛰기: {str(e)}")
        audio_file = None
    
    # 카카오톡 전송
    success = send_kakao_message(full_message, audio_file)
    
    # 임시 파일 정리
    if audio_file and os.path.exists(audio_file):
        try:
            os.unlink(audio_file)
        except:
            pass
    
    if success:
        print("🎉 뉴스 요약 봇 실행 완료!")
    else:
        print("❌ 메시지 전송 실패")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
