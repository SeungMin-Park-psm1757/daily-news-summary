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
import aiohttp

# 환경 변수 확인
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

if not GEMINI_API_KEY:
    print("❌ GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.")
    sys.exit(1)

if not TELEGRAM_BOT_TOKEN:
    print("❌ TELEGRAM_BOT_TOKEN 환경 변수가 설정되지 않았습니다.")
    print("💡 텔레그램 봇 토큰을 발급받아 GitHub Secrets에 등록하세요.")
    sys.exit(1)

if not TELEGRAM_CHAT_ID:
    print("❌ TELEGRAM_CHAT_ID 환경 변수가 설정되지 않았습니다.")
    print("💡 텔레그램 채팅 ID를 확인하여 GitHub Secrets에 등록하세요.")
    sys.exit(1)

# Gemini API 설정
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# 키워드별 RSS 피드 설정 및 이모티콘 매핑
KEYWORD_FEEDS = {
    '군대': [
        'https://www.yna.co.kr/rss/northkorea.xml',
        'http://newssearch.naver.com/search.naver?where=rss&query=군대&sort=date',
        'http://newssearch.naver.com/search.naver?where=rss&query=육군&sort=date',
        'https://rss.nytimes.com/services/xml/rss/nyt/World.xml',
        'http://feeds.bbci.co.uk/news/world/rss.xml'
    ],
    '정치': [
        'https://www.yna.co.kr/rss/politics.xml',
        'http://newssearch.naver.com/search.naver?where=rss&query=정치&sort=date',
        'http://newssearch.naver.com/search.naver?where=rss&query=국정감사&sort=date',
        'https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml',
        'http://feeds.bbci.co.uk/news/politics/rss.xml'
    ],
    '주식': [
        'https://www.yna.co.kr/rss/economy.xml',
        'http://newssearch.naver.com/search.naver?where=rss&query=코스피&sort=date',
        'http://newssearch.naver.com/search.naver?where=rss&query=삼성전자&sort=date',
        'https://rss.nytimes.com/services/xml/rss/nyt/Business.xml',
        'http://feeds.bbci.co.uk/news/business/rss.xml'
    ],
    'AI': [
        'http://newssearch.naver.com/search.naver?where=rss&query=인공지능&sort=date',
        'http://newssearch.naver.com/search.naver?where=rss&query=AI&sort=date',
        'http://newssearch.naver.com/search.naver?where=rss&query=ChatGPT&sort=date',
        'https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml',
        'http://feeds.bbci.co.uk/news/technology/rss.xml'
    ]
}

# 키워드별 이모티콘 매핑
KEYWORD_EMOJIS = {
    '군대': '🪖',
    '정치': '🏛️', 
    '주식': '📈',
    'AI': '🤖'
}

def clean_text(text):
    """HTML 태그 제거 및 텍스트 정리"""
    if not text:
        return ""
    
    soup = BeautifulSoup(text, 'html.parser')
    clean_text = soup.get_text()
    clean_text = re.sub(r'\s+', ' ', clean_text)
    clean_text = re.sub(r'[\r\n\t]', ' ', clean_text)
    clean_text = clean_text.strip()
    
    return clean_text[:500]

def is_recent_article(published_date, hours=24):
    """24시간 이내 기사인지 확인"""
    try:
        if not published_date:
            return True
        
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
            
            is_international = any(domain in feed_url for domain in ['nytimes.com', 'bbci.co.uk'])
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            feed = feedparser.parse(feed_url, request_headers=headers)
            
            if feed.bozo and feed.bozo_exception:
                print(f"    ⚠️ RSS 파싱 경고: {feed_url}")
                continue
            
            for entry in feed.entries[:10]:
                try:
                    if not is_recent_article(entry.get('published')):
                        continue
                    
                    title = clean_text(entry.get('title', ''))
                    summary = clean_text(entry.get('summary', entry.get('description', '')))
                    
                    if not title or len(title) < 10:
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
                    
                    if len(domestic_articles) >= max_domestic and len(international_articles) >= max_international:
                        break
                        
                except Exception as e:
                    print(f"    ⚠️ 기사 처리 오류: {e}")
                    continue
            
            print(f"    ✅ 수집됨 - 국내: {len(domestic_articles)}, 해외: {len(international_articles)}")
            
        except Exception as e:
            print(f"    ❌ 피드 처리 실패: {e}")
            continue
            
        if len(domestic_articles) >= max_domestic and len(international_articles) >= max_international:
            break
    
    all_articles = domestic_articles + international_articles
    print(f"  ✅ [{keyword}] 총 {len(all_articles)}개 기사 수집 완료")
    
    return all_articles

def summarize_news_with_gemini(keyword, articles):
    """Gemini API로 뉴스 요약 (개조식 형태)"""
    if not articles:
        emoji = KEYWORD_EMOJIS.get(keyword, '📰')
        return f"{emoji} {keyword}\n• 오늘은 관련 주요 뉴스가 없었습니다."
    
    print(f"🤖 [{keyword}] AI 요약 생성 중...")
    
    articles_text = ""
    for i, article in enumerate(articles, 1):
        source_type = "🌍해외" if article['source'] == 'international' else "🇰🇷국내"
        articles_text += f"\n[{source_type} 기사 {i}]\n제목: {article['title']}\n내용: {article['summary'][:300]}\n"
    
    prompt = f"""다음은 '{keyword}' 관련 오늘의 주요 뉴스들입니다. 읽기 쉬운 개조식으로 요약해주세요.

{articles_text}

요약 형식:
1. 각 주요 내용을 개조식(• 문장)으로 작성
2. 최대 5개 항목으로 제한  
3. 각 항목은 한 줄로 간결하게
4. 구체적 수치나 핵심 키워드 포함
5. "~했다", "~됐다" 등 과거형 사용
6. 중요도 순으로 배열

예시 형식:
• 첫 번째 핵심 내용이다
• 두 번째 중요한 소식이다
• 세 번째 주요 사건이다

요약 (개조식):"""

    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.3,
                max_output_tokens=1500,
                top_p=0.8,
                top_k=40
            )
        )
        
        summary = response.text.strip()
        
        if not summary:
            emoji = KEYWORD_EMOJIS.get(keyword, '📰')
            return f"{emoji} {keyword}\n• 요약 생성에 실패했습니다."
        
        lines = [line.strip() for line in summary.split('\n') if line.strip()]
        bullet_points = []
        
        for line in lines:
            if line.startswith('•') or line.startswith('-') or line.startswith('*'):
                bullet_points.append(line if line.startswith('•') else f"• {line[1:].strip()}")
            elif not any(line.startswith(prefix) for prefix in ['요약', '형식', '예시']):
                bullet_points.append(f"• {line}")
        
        if len(bullet_points) > 5:
            bullet_points = bullet_points[:5]
        
        if not bullet_points:
            emoji = KEYWORD_EMOJIS.get(keyword, '📰')
            return f"{emoji} {keyword}\n• 충분한 요약 내용을 생성하지 못했습니다."
            
        print(f"  ✅ [{keyword}] 요약 완료 ({len(bullet_points)}개 항목)")
        
        emoji = KEYWORD_EMOJIS.get(keyword, '📰')
        formatted_summary = f"{emoji} {keyword}\n" + "\n".join(bullet_points)
        
        return formatted_summary
        
    except Exception as e:
        print(f"  ❌ [{keyword}] 요약 실패: {str(e)}")
        emoji = KEYWORD_EMOJIS.get(keyword, '📰')
        return f"{emoji} {keyword}\n• AI 요약 생성 중 오류가 발생했습니다."

def prepare_tts_text_with_pauses(text_content):
    """TTS용 텍스트 준비 - SSML로 간격 추가"""
    
    # 1. 기본 정리 (이모티콘 제거 등)
    clean_content = text_content
    clean_content = re.sub(r'[🪖🏛️📈🤖📰🌍🇰🇷]', '', clean_content)  # 이모티콘 제거
    clean_content = re.sub(r'[─]+', '', clean_content)  # 구분선 제거
    clean_content = re.sub(r'•', '', clean_content)  # 불릿 기호 제거
    clean_content = re.sub(r'\s+', ' ', clean_content).strip()  # 공백 정리
    
    # 2. 길이 제한
    if len(clean_content) > 3000:
        clean_content = clean_content[:3000] + "이상으로 오늘의 뉴스 요약을 마치겠습니다."
    
    # 3. 텍스트를 줄 단위로 분할하여 SSML로 변환
    lines = clean_content.split('\n')
    
    # 4. SSML 형식으로 변환 - 각 줄 사이에 0.5초 간격
    ssml_content = '<speak>'
    
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:  # 빈 줄은 건너뛰기
            continue
            
        # 각 줄을 추가
        ssml_content += f'{line}'
        
        # 마지막 줄이 아니면 0.5초 간격 추가
        if i < len(lines) - 1 and line:
            ssml_content += '<break time="0.5s"/>'
    
    ssml_content += '</speak>'
    
    # 5. 특수 케이스 처리
    ssml_content = ssml_content.replace('|', '<break time="0.3s"/>')  # 구분자를 짧은 간격으로
    ssml_content = ssml_content.replace('완료', '완료<break time="0.7s"/>')  # 완료 후 긴 간격
    
    # 6. 시작 멘트 추가
    final_ssml = '<speak>오늘의 주요 뉴스를 요약해드리겠습니다.<break time="1s"/>' + ssml_content[7:]  # <speak> 중복 제거
    
    return final_ssml

async def generate_news_audio(text_content, output_path=None):
    """뉴스 요약을 음성으로 변환 - SSML로 간격 제어"""
    try:
        print("🔊 뉴스 요약 음성 변환 중 (SSML 간격 포함)...")
        
        # SSML 형식으로 텍스트 준비
        ssml_content = prepare_tts_text_with_pauses(text_content)
        
        # 임시 파일 생성
        if not output_path:
            output_path = tempfile.mktemp(suffix='.ogg')
        
        # 한국어 TTS 설정 (SSML 지원)
        communicate = edge_tts.Communicate(
            text=ssml_content,
            voice="ko-KR-SunHiNeural",
            rate="+10%",
            volume="+0%"
        )
        
        # 음성 파일 생성
        await communicate.save(output_path)
        
        # 파일 크기 확인
        file_size = os.path.getsize(output_path)
        print(f"  ✅ 음성 파일 생성 완료: {file_size/1024:.1f}KB (SSML 간격 적용)")
        
        return output_path
        
    except Exception as e:
        print(f"  ❌ 음성 변환 실패: {str(e)}")
        # SSML 실패 시 일반 텍스트로 폴백
        try:
            print("  🔄 일반 텍스트로 재시도 중...")
            
            clean_content = text_content
            clean_content = re.sub(r'[🪖🏛️📈🤖📰🌍🇰🇷]', '', clean_content)
            clean_content = re.sub(r'[─]+', '', clean_content)
            clean_content = re.sub(r'•', '', clean_content)
            clean_content = re.sub(r'\s+', ' ', clean_content).strip()
            
            if len(clean_content) > 3000:
                clean_content = clean_content[:3000] + "이상으로 오늘의 뉴스 요약을 마치겠습니다."
            
            clean_content = "오늘의 주요 뉴스를 요약해드리겠습니다. " + clean_content
            
            communicate = edge_tts.Communicate(
                text=clean_content,
                voice="ko-KR-SunHiNeural",
                rate="+10%",
                volume="+0%"
            )
            
            await communicate.save(output_path)
            file_size = os.path.getsize(output_path)
            print(f"  ✅ 일반 음성 파일 생성 완료: {file_size/1024:.1f}KB")
            
            return output_path
            
        except Exception as fallback_error:
            print(f"  ❌ 폴백 음성 변환도 실패: {str(fallback_error)}")
            return None

async def send_telegram_message(text, parse_mode='HTML'):
    """텔레그램 텍스트 메시지 전송"""
    try:
        print("📱 텔레그램 텍스트 메시지 전송 중...")
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        
        data = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': text,
            'parse_mode': parse_mode,
            'disable_web_page_preview': True
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data) as response:
                result = await response.json()
                
                if response.status == 200 and result.get('ok'):
                    print("  ✅ 텔레그램 텍스트 메시지 전송 성공!")
                    return True
                else:
                    print(f"  ❌ 텔레그램 API 오류: {result}")
                    return False
                    
    except Exception as e:
        print(f"  ❌ 텍스트 메시지 전송 실패: {str(e)}")
        return False

async def send_telegram_voice(voice_file_path, caption=""):
    """텔레그램 음성 파일 전송"""
    try:
        print("🔊 텔레그램 음성 메시지 전송 중...")
        
        if not voice_file_path or not os.path.exists(voice_file_path):
            print("  ❌ 음성 파일이 없습니다.")
            return False
        
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVoice"
        
        async with aiohttp.ClientSession() as session:
            with open(voice_file_path, 'rb') as voice_file:
                form_data = aiohttp.FormData()
                form_data.add_field('chat_id', TELEGRAM_CHAT_ID)
                form_data.add_field('voice', voice_file, filename='news_summary.ogg')
                form_data.add_field('caption', caption)
                
                async with session.post(url, data=form_data) as response:
                    result = await response.json()
                    
                    if response.status == 200 and result.get('ok'):
                        print("  ✅ 텔레그램 음성 메시지 전송 성공!")
                        return True
                    else:
                        print(f"  ❌ 텔레그램 음성 전송 오류: {result}")
                        return False
                        
    except Exception as e:
        print(f"  ❌ 음성 메시지 전송 실패: {str(e)}")
        return False

async def main():
    """메인 실행 함수 - 텔레그램 단일 메시지 + 음성 전송"""
    start_time = datetime.now()
    print("🚀 텔레그램 뉴스 요약 봇 시작 (텍스트 + SSML 간격 음성)")
    print(f"⏰ 실행 시간: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🤖 텔레그램 봇: @{TELEGRAM_BOT_TOKEN.split(':')[0]}")
    print(f"📢 채팅 ID: {TELEGRAM_CHAT_ID}")
    print(f"🔍 대상 키워드: {', '.join(KEYWORD_FEEDS.keys())}")
    
    today = datetime.now()
    weekday_names = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일']
    weekday = weekday_names[today.weekday()]
    
    all_summaries = []
    success_count = 0
    
    for keyword in KEYWORD_FEEDS.keys():
        try:
            print(f"\n{'='*60}")
            print(f"🎯 [{keyword}] 처리 시작")
            
            articles = collect_news_by_keyword(keyword, max_domestic=5, max_international=2)
            summary = summarize_news_with_gemini(keyword, articles)
            all_summaries.append(summary)
            success_count += 1
            
            print(f"✅ [{keyword}] 처리 완료")
            time.sleep(2)
            
        except Exception as e:
            emoji = KEYWORD_EMOJIS.get(keyword, '📰')
            error_summary = f"{emoji} {keyword}\n• 처리 중 오류가 발생했습니다: {str(e)}"
            all_summaries.append(error_summary)
            print(f"❌ [{keyword}] 처리 실패: {str(e)}")
    
    header = f"📰 {today.strftime('%m/%d')} {weekday} 뉴스요약"
    
    full_message = f"{header}\n{'─'*30}\n\n"
    full_message += "\n\n".join(all_summaries)
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    footer = f"\n\n{'─'*30}"
    footer += f"\n📊 {success_count}/{len(KEYWORD_FEEDS)}개 완료"
    footer += f" | ⏱️ {duration:.0f}초"
    footer += f" | 🕐 {end_time.strftime('%H:%M')}"
    
    full_message += footer
    
    print(f"\n{'='*60}")
    print("📝 최종 요약 생성 완료")
    print(f"📊 총 길이: {len(full_message)}자")
    print(f"⏱️ 총 처리 시간: {duration:.1f}초")
    
    # 🎵 SSML 간격 적용된 음성 파일 생성
    audio_file = await generate_news_audio(full_message)
    
    text_success = await send_telegram_message(full_message)
    voice_success = False
    
    if audio_file and os.path.exists(audio_file):
        voice_caption = f"🔊 {today.strftime('%m/%d')} {weekday} 뉴스 요약 음성 (간격 적용)"
        voice_success = await send_telegram_voice(audio_file, voice_caption)
        
        try:
            os.unlink(audio_file)
            print(f"🗂️ 임시 음성 파일 정리 완료")
        except Exception as e:
            print(f"⚠️ 임시 파일 삭제 실패: {e}")
    
    if text_success:
        print("✅ 텍스트 메시지 전송 성공!")
    else:
        print("❌ 텍스트 메시지 전송 실패")
        
    if voice_success:
        print("🔊 음성 메시지 전송 성공! (SSML 간격 적용)")
    else:
        print("❌ 음성 메시지 전송 실패")
    
    print(f"\n🎉 텔레그램 뉴스 요약 봇 실행 완료!")
    print(f"📊 총 처리 결과: {success_count}/{len(KEYWORD_FEEDS)}개 키워드 완료")
    print(f"⏱️ 총 처리 시간: {duration:.1f}초")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️ 사용자에 의해 중단되었습니다.")
    except Exception as e:
        print(f"\n💥 예상치 못한 오류 발생: {str(e)}")
        sys.exit(1)
