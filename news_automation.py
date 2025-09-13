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
import time
from email.utils import parsedate_tz, mktime_tz
import re
from bs4 import BeautifulSoup

# 환경 변수 확인
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
KAKAO_ACCESS_TOKEN = os.getenv('KAKAO_ACCESS_TOKEN')

if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.")
    sys.exit(1)

if not KAKAO_ACCESS_TOKEN:
    print("❌ KAKAO_ACCESS_TOKEN 환경 변수가 설정되지 않았습니다.")
    sys.exit(1)

# Gemini API 설정
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# 키워드별 RSS 피드 설정 (안정적인 URL들로 업데이트)
KEYWORD_FEEDS = {
    '군대': [
        'https://www.yna.co.kr/rss/northkorea.xml',  # 연합뉴스 북한
        'http://newssearch.naver.com/search.naver?where=rss&query=군대&sort=date',
        'http://newssearch.naver.com/search.naver?where=rss&query=육군&sort=date',
        'https://rss.nytimes.com/services/xml/rss/nyt/World.xml',  # 해외
        'http://feeds.bbci.co.uk/news/world/rss.xml'  # 해외
    ],
    '정치': [
        'https://www.yna.co.kr/rss/politics.xml',
        'http://newssearch.naver.com/search.naver?where=rss&query=정치&sort=date',
        'http://newssearch.naver.com/search.naver?where=rss&query=국정감사&sort=date',
        'https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml',  # 해외
        'http://feeds.bbci.co.uk/news/politics/rss.xml'  # 해외
    ],
    '주식': [
        'https://www.yna.co.kr/rss/economy.xml',
        'http://newssearch.naver.com/search.naver?where=rss&query=코스피&sort=date',
        'http://newssearch.naver.com/search.naver?where=rss&query=삼성전자&sort=date',
        'https://rss.nytimes.com/services/xml/rss/nyt/Business.xml',  # 해외
        'http://feeds.bbci.co.uk/news/business/rss.xml'  # 해외
    ],
    'AI': [
        'http://newssearch.naver.com/search.naver?where=rss&query=인공지능&sort=date',
        'http://newssearch.naver.com/search.naver?where=rss&query=AI&sort=date',
        'http://newssearch.naver.com/search.naver?where=rss&query=ChatGPT&sort=date',
        'https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml',  # 해외
        'http://feeds.bbci.co.uk/news/technology/rss.xml'  # 해외
    ]
}

def clean_text(text):
    """HTML 태그 제거 및 텍스트 정리"""
    if not text:
        return ""
    
    # HTML 태그 제거
    soup = BeautifulSoup(text, 'html.parser')
    clean_text = soup.get_text()
    
    # 특수 문자 및 공백 정리
    clean_text = re.sub(r'\s+', ' ', clean_text)
    clean_text = re.sub(r'[\r\n\t]', ' ', clean_text)
    clean_text = clean_text.strip()
    
    return clean_text[:500]  # 최대 500자로 제한

def is_recent_article(published_date, hours=24):
    """24시간 이내 기사인지 확인"""
    try:
        if not published_date:
            return True
        
        # RSS 날짜 파싱
        parsed = parsedate_tz(published_date)
        if parsed:
            article_time = datetime.fromtimestamp(mktime_tz(parsed))
            cutoff_time = datetime.now() - timedelta(hours=hours)
            return article_time > cutoff_time
        return True
    except Exception as e:
        print(f"    ⚠️ 날짜 파싱 오류: {e}")
        return True

def collect_news_by_keyword(keyword, max_domestic=5, max_international=2):
    """키워드별 뉴스 수집"""
    print(f"\n📰 [{keyword}] 뉴스 수집 중...")
    
    domestic_articles = []
    international_articles = []
    feeds = KEYWORD_FEEDS.get(keyword, [])
    
    for i, feed_url in enumerate(feeds):
        try:
            print(f"  피드 {i+1}/{len(feeds)} 처리 중...")
            
            # 해외 소스 판별
            is_international = any(domain in feed_url for domain in ['nytimes.com', 'bbci.co.uk'])
            
            # RSS 피드 파싱
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            feed = feedparser.parse(feed_url, request_headers=headers)
            
            if feed.bozo and feed.bozo_exception:
                print(f"    ⚠️ RSS 파싱 경고: {feed_url}")
                continue
            
            # 기사 수집
            for entry in feed.entries[:10]:  # 최대 10개씩 확인
                try:
                    if not is_recent_article(entry.get('published')):
                        continue
                    
                    title = clean_text(entry.get('title', ''))
                    summary = clean_text(entry.get('summary', entry.get('description', '')))
                    
                    if not title or len(title) < 10:  # 너무 짧은 제목 제외
                        continue
                    
                    article = {
                        'title': title[:100],
                        'link': entry.get('link', ''),
                        'summary': summary,
                        'published': entry.get('published', ''),
                        'source': 'international' if is_international else 'domestic'
                    }
                    
                    if is_international:
                        if len(international_articles) < max_international:
                            international_articles.append(article)
                    else:
                        if len(domestic_articles) < max_domestic:
                            domestic_articles.append(article)
                    
                    # 충분히 수집했으면 다음 피드로
                    if len(domestic_articles) >= max_domestic and len(international_articles) >= max_international:
                        break
                        
                except Exception as e:
                    print(f"    ⚠️ 기사 처리 오류: {e}")
                    continue
            
            print(f"    ✅ 수집됨 - 국내: {len(domestic_articles)}, 해외: {len(international_articles)}")
            
        except Exception as e:
            print(f"    ❌ 피드 처리 실패: {e}")
            continue
            
        # 충분히 수집했으면 종료
        if len(domestic_articles) >= max_domestic and len(international_articles) >= max_international:
            break
    
    all_articles = domestic_articles + international_articles
    print(f"  ✅ [{keyword}] 총 {len(all_articles)}개 기사 수집 완료")
    
    return all_articles

def summarize_news_with_gemini(keyword, articles):
    """Gemini API로 뉴스 요약"""
    if not articles:
        return f"[{keyword}] 오늘은 관련된 주요 뉴스가 확인되지 않았다."
    
    print(f"🤖 [{keyword}] AI 요약 생성 중...")
    
    # 기사 내용 정리
    articles_text = ""
    for i, article in enumerate(articles, 1):
        source_type = "해외" if article['source'] == 'international' else "국내"
        articles_text += f"\n[{source_type} 기사 {i}]\n제목: {article['title']}\n내용: {article['summary'][:300]}\n"
    
    # Gemini 프롬프트
    prompt = f"""다음은 '{keyword}' 관련 오늘의 주요 뉴스들입니다. 이를 바탕으로 핵심 내용을 한국어로 요약해주세요.

{articles_text}

요약 규칙:
1. 5-10개 문장으로 요약
2. 모든 문장은 "~했다", "~됐다", "~나타났다", "~밝혔다" 등 과거형으로 종료
3. 핵심 사실과 구체적 수치 포함
4. 국내외 소식을 균형있게 반영
5. 객관적이고 간결한 어조 유지
6. 중요도 순으로 배열

요약:"""

    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.3,
                max_output_tokens=2000,
                top_p=0.8,
                top_k=40
            )
        )
        
        summary = response.text.strip()
        
        if not summary:
            return f"[{keyword}] 요약 생성에 실패했다."
        
        # 문장 단위로 분리하여 검증
        sentences = [s.strip() for s in summary.split('.') if s.strip()]
        if len(sentences) < 3:
            return f"[{keyword}] 충분한 요약 내용을 생성하지 못했다."
            
        print(f"  ✅ [{keyword}] 요약 완료 ({len(summary)}자, {len(sentences)}문장)")
        
        return f"[{keyword}]\n{summary}"
        
    except Exception as e:
        print(f"  ❌ [{keyword}] 요약 실패: {str(e)}")
        return f"[{keyword}] AI 요약 생성 중 오류가 발생했다."

async def text_to_speech(text, output_file):
    """Edge TTS로 텍스트를 음성으로 변환 (선택적)"""
    try:
        print("🔊 음성 변환 시도 중...")
        
        # 텍스트 길이 제한 (TTS는 너무 긴 텍스트에 부담)
        if len(text) > 3000:
            text = text[:3000] + "..."
        
        # 한국어 음성 설정
        communicate = edge_tts.Communicate(
            text=text,
            voice="ko-KR-SunHiNeural",
            rate="+10%",
            volume="+0%"
        )
        
        await communicate.save(output_file)
        print(f"  ✅ 음성 파일 생성 성공")
        return True
        
    except Exception as e:
        print(f"  ⚠️ 음성 변환 실패 (계속 진행): {str(e)}")
        return False

def send_kakao_message(text_message):
    """카카오톡 나에게 메시지 전송"""
    print("📱 카카오톡 메시지 전송 중...")
    
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {
        "Authorization": f"Bearer {KAKAO_ACCESS_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"
    }
    
    # 메시지 길이 제한 (카카오톡 제한 고려)
    if len(text_message) > 1000:
        text_message = text_message[:1000] + "\n\n(전체 내용이 길어 일부만 표시됩니다)"
    
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
        response = requests.post(url, headers=headers, data=data, timeout=10)
        
        if response.status_code == 200:
            result = response.json()
            if result.get('result_code') == 0:
                print("  ✅ 카카오톡 메시지 전송 성공!")
                return True
            else:
                print(f"  ❌ 카카오 API 오류: {result.get('msg', 'Unknown error')}")
                return False
        else:
            print(f"  ❌ HTTP 오류: {response.status_code}")
            return False
            
    except requests.exceptions.Timeout:
        print("  ❌ 요청 시간 초과")
        return False
    except Exception as e:
        print(f"  ❌ 메시지 전송 실패: {str(e)}")
        return False

async def main():
    """메인 실행 함수"""
    start_time = datetime.now()
    print("🚀 뉴스 요약 봇 시작")
    print(f"⏰ 실행 시간: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🔍 대상 키워드: {', '.join(KEYWORD_FEEDS.keys())}")
    
    # 전체 요약 저장
    all_summaries = []
    success_count = 0
    
    # 키워드별 뉴스 수집 및 요약
    for keyword in KEYWORD_FEEDS.keys():
        try:
            print(f"\n{'='*60}")
            print(f"🎯 [{keyword}] 처리 시작")
            
            # 뉴스 수집
            articles = collect_news_by_keyword(keyword, max_domestic=5, max_international=2)
            
            if not articles:
                summary = f"[{keyword}] 오늘은 관련된 최신 뉴스를 찾을 수 없었다."
            else:
                # AI 요약
                summary = summarize_news_with_gemini(keyword, articles)
            
            all_summaries.append(summary)
            success_count += 1
            
            print(f"✅ [{keyword}] 처리 완료")
            
            # API 호출 간격 조정
            time.sleep(2)
            
        except Exception as e:
            error_summary = f"[{keyword}] 처리 중 오류가 발생했다: {str(e)}"
            all_summaries.append(error_summary)
            print(f"❌ [{keyword}] 처리 실패: {str(e)}")
    
    # 전체 메시지 구성
    today = datetime.now().strftime('%Y년 %m월 %d일 %A')
    header = f"📰 {today} 주요 뉴스 요약"
    
    full_message = header + "\n" + "="*50 + "\n\n"
    full_message += "\n\n".join(all_summaries)
    
    # 실행 정보 추가
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    footer = f"\n\n📊 처리 결과: {success_count}/{len(KEYWORD_FEEDS)}개 키워드 완료"
    footer += f"\n⏱️ 처리 시간: {duration:.1f}초"
    footer += f"\n🕐 생성 시간: {end_time.strftime('%H:%M')}"
    
    full_message += footer
    
    print(f"\n{'='*60}")
    print("📝 최종 요약 생성 완료")
    print(f"📊 총 길이: {len(full_message)}자")
    print(f"⏱️ 총 처리 시간: {duration:.1f}초")
    
    # 음성 변환 시도 (선택적)
    audio_file = None
    try:
        audio_file = tempfile.mktemp(suffix='.wav')
        await text_to_speech(full_message[:1000], audio_file)  # 길이 제한
    except Exception as e:
        print(f"⚠️ 음성 변환 건너뛰기: {str(e)}")
    
    # 카카오톡 전송
    success = send_kakao_message(full_message)
    
    # 임시 파일 정리
    if audio_file and os.path.exists(audio_file):
        try:
            os.unlink(audio_file)
        except:
            pass
    
    # 최종 결과
    if success:
        print("\n🎉 뉴스 요약 봇 실행 완료!")
        print("📱 카카오톡으로 요약이 전송되었습니다.")
    else:
        print("\n⚠️ 메시지 전송은 실패했지만, 요약 생성은 완료되었습니다.")
    
    print(f"\n📋 요약 미리보기:")
    print("-" * 50)
    print(full_message[:500] + "..." if len(full_message) > 500 else full_message)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️ 사용자에 의해 중단되었습니다.")
    except Exception as e:
        print(f"\n💥 예상치 못한 오류 발생: {str(e)}")
        sys.exit(1)
