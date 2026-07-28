"""
네이버 뉴스 제목/본문/댓글 → Word2Vec 입력 TXT 생성기 v21_comment_csv_quote_fixed

이 파일은 Word2Vec 학습을 하지 않습니다.
결과물 TXT만 생성합니다.

v16_strict_dedup 개선점:
1. 네이버 뉴스 본문 페이지뿐 아니라 언론사 원문 링크도 함께 수집할 수 있습니다.
2. 네이버 뉴스는 전용 선택자로 제목/본문을 추출합니다.
3. 언론사 원문은 범용 HTML 추출 방식으로 제목/본문을 추출합니다.
4. 긴 기간 검색 시 월/주/일 단위로 날짜를 나누어 더 많은 결과를 수집할 수 있습니다.
5. 제목 + 본문을 한 기사당 한 줄로 TXT 저장합니다.
6. 제목에 특정 키워드가 들어간 기사만 저장하는 필터를 제공합니다.
7. 네이버 서비스 링크(book/mail/comic/help 등)가 섞이는 문제를 줄였습니다.
8. 같은 검색 결과 카드의 네이버 링크/원문 링크 중복 집계를 줄이고, 제목+본문 기준 2차 중복 제거를 합니다.
9. 날짜 조각별 요청 URL, HTTP 상태 코드, 발견 링크 수, 새 링크 수, 중복 수를 진단 로그로 출력합니다.
10. 차단 의심 페이지가 연속 감지되면 즉시 중단합니다.
11. 요청 간격을 고정값이 아니라 랜덤 대기 시간으로 적용합니다.
12. Selenium DNS/네트워크 오류(net::ERR_NAME_NOT_RESOLVED 등)를 전체 오류로 띄우지 않고 해당 페이지를 건너뜁니다.

필요 패키지:
pip install requests beautifulsoup4 selenium chromedriver-autoinstaller tqdm

주의:
- 언론사 원문 사이트는 사이트마다 HTML 구조가 달라서 네이버 뉴스 페이지보다 실패율이 높을 수 있습니다.
- 일부 언론사 사이트는 봇 차단, 유료 기사, JavaScript 렌더링 때문에 본문 추출이 실패할 수 있습니다.
"""

from __future__ import annotations

import csv
import os
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

import chromedriver_autoinstaller
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, WebDriverException


DEFAULT_SEARCH_SLEEP_MIN = 4.0
DEFAULT_SEARCH_SLEEP_MAX = 8.0
DEFAULT_ARTICLE_SLEEP_MIN = 2.0
DEFAULT_ARTICLE_SLEEP_MAX = 5.0
DEFAULT_BLOCK_PAGE_LIMIT = 3
# 기존 코드 호환용: 검색 페이지 기본 최소 대기초
DEFAULT_SLEEP_SEC = DEFAULT_SEARCH_SLEEP_MIN
DEFAULT_BODY_CHARS = 200
DEFAULT_EMPTY_PAGE_LIMIT = None  # None이면 빈 페이지가 나와도 최대 페이지까지 계속 확인
MIN_BODY_LEN = 80
DEBUG_HTML_SAMPLE_CHARS = 300

# 네이버 검색 페이지 전체 링크를 보강 수집할 때, 기사와 무관한 네이버 서비스 링크를 제외합니다.
EXCLUDED_HOST_KEYWORDS = {
    "search.naver.com",
    "nid.naver.com",
    "section.blog.naver.com",
    "blog.naver.com",
    "m.blog.naver.com",
    "kin.naver.com",
    "shopping.naver.com",
    "search.shopping.naver.com",
    "book.naver.com",
    "comic.naver.com",
    "mail.naver.com",
    "help.naver.com",
    "www.naver.com",
    "map.naver.com",
    "cafe.naver.com",
    "dict.naver.com",
    "papago.naver.com",
    "adcr.naver.com",
    "ssl.pstatic.net",
}

NAVER_NEWS_HOSTS = {"n.news.naver.com", "news.naver.com", "m.news.naver.com"}


# -----------------------------------------------------------------------------
# 공통 유틸
# -----------------------------------------------------------------------------


def clean_text(text: str) -> str:
    """줄바꿈, 탭, 여러 공백을 한 칸 공백으로 정리합니다."""
    return " ".join(str(text).split()).strip()


def random_sleep(min_sec: float, max_sec: float) -> None:
    """차단 가능성을 낮추기 위해 지정 범위 안에서 랜덤 대기합니다."""
    try:
        min_sec = float(min_sec)
        max_sec = float(max_sec)
    except Exception:
        min_sec, max_sec = DEFAULT_SEARCH_SLEEP_MIN, DEFAULT_SEARCH_SLEEP_MAX

    if min_sec < 0:
        min_sec = 0
    if max_sec < min_sec:
        max_sec = min_sec

    time.sleep(random.uniform(min_sec, max_sec))


def safe_filename(text: str) -> str:
    """Windows 파일명에 사용할 수 없는 문자를 _로 바꿉니다."""
    text = clean_text(text)
    return re.sub(r'[\\/:*?"<>|]', "_", text) or "naver_news"


def normalize_keyword_text(text: str) -> str:
    """키워드 비교용으로 대소문자와 공백 차이를 제거합니다."""
    return re.sub(r"\s+", "", str(text)).upper()


def split_filter_keywords(text: str) -> list[str]:
    """쉼표/공백으로 입력한 필터 키워드를 목록으로 분리합니다."""
    parts = re.split(r"[,\s]+", str(text).strip())
    return [p for p in parts if p]


def contains_any_keyword(text: str, keywords: list[str]) -> bool:
    """text 안에 keywords 중 하나라도 들어 있으면 True를 반환합니다."""
    if not keywords:
        return True
    normalized_text = normalize_keyword_text(text)
    return any(normalize_keyword_text(keyword) in normalized_text for keyword in keywords)


def make_article_title_key(title: str) -> str:
    """
    URL이 달라도 제목이 같은 기사를 같은 기사로 판별하기 위한 제목 중복 키를 만듭니다.

    네이버 뉴스 링크와 언론사 원문 링크는 URL과 본문 형태가 달라도 제목은 같은 경우가 많습니다.
    Word2Vec 학습용 데이터에서는 같은 제목 기사가 반복되면 특정 표현이 과대표집되므로,
    제목을 공백/대소문자 차이 없이 정규화해서 먼저 중복 제거합니다.
    """
    return normalize_keyword_text(title)


def make_article_content_key(title: str, body: str) -> str:
    """
    URL이 달라도 같은 기사인지 판별하기 위한 내용 기반 중복 키를 만듭니다.

    제목이 조금 다르지만 본문이 거의 같은 기사까지 한 번 더 제거하기 위해
    제목 + 본문 앞부분을 정규화해서 내용 기반 중복 제거 키로 사용합니다.
    """
    title_key = normalize_keyword_text(title)
    body_key = normalize_keyword_text(body)[:300]
    return f"{title_key}|{body_key}"


def format_date(date_text: str) -> str:
    """
    YYYYMMDD 또는 YYYY.MM.DD 형식을 네이버 검색용 YYYY.MM.DD로 변환합니다.
    예: 20260101 → 2026.01.01
    """
    date_text = date_text.strip()
    if re.fullmatch(r"\d{8}", date_text):
        return f"{date_text[:4]}.{date_text[4:6]}.{date_text[6:]}"
    return date_text


def compact_date(date_text: str) -> str:
    """
    YYYY.MM.DD 또는 YYYYMMDD 형식을 네이버 nso 파라미터용 YYYYMMDD로 변환합니다.
    예: 2026.01.01 → 20260101
    """
    return re.sub(r"\D", "", date_text.strip())


def parse_date(date_text: str) -> datetime:
    """YYYYMMDD 또는 YYYY.MM.DD 문자열을 datetime으로 변환합니다."""
    return datetime.strptime(compact_date(date_text), "%Y%m%d")


def last_day_of_month(dt: datetime) -> datetime:
    """해당 날짜가 속한 달의 마지막 날을 반환합니다."""
    if dt.month == 12:
        next_month = datetime(dt.year + 1, 1, 1)
    else:
        next_month = datetime(dt.year, dt.month + 1, 1)
    return next_month - timedelta(days=1)


def make_date_ranges(start_date: str, end_date: str, split_mode: str) -> list[tuple[str, str]]:
    """
    긴 기간 검색 결과 누락을 줄이기 위해 날짜 범위를 나눕니다.

    split_mode:
    - none: 전체 기간 한 번만 검색
    - month: 월 단위 검색
    - week: 주 단위 검색
    - day: 일 단위 검색
    """
    start = parse_date(start_date)
    end = parse_date(end_date)

    if start > end:
        raise ValueError("시작일이 종료일보다 늦습니다.")

    split_mode = split_mode.lower().strip()
    if split_mode not in {"none", "month", "week", "day"}:
        split_mode = "month"

    if split_mode == "none":
        return [(start.strftime("%Y.%m.%d"), end.strftime("%Y.%m.%d"))]

    ranges: list[tuple[str, str]] = []
    current = start

    while current <= end:
        if split_mode == "day":
            chunk_end = current
        elif split_mode == "week":
            chunk_end = min(current + timedelta(days=6), end)
        else:
            chunk_end = min(last_day_of_month(current), end)

        ranges.append((current.strftime("%Y.%m.%d"), chunk_end.strftime("%Y.%m.%d")))
        current = chunk_end + timedelta(days=1)

    return ranges


# -----------------------------------------------------------------------------
# 검색 링크 수집
# -----------------------------------------------------------------------------


def setup_chromedriver() -> webdriver.Chrome:
    """네이버 검색 결과 페이지를 열기 위한 ChromeDriver를 준비합니다."""
    chromedriver_autoinstaller.install()

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("lang=ko_KR")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    return driver


def is_selenium_navigation_error(message: str) -> bool:
    """Selenium/ChromeDriver 네트워크 오류인지 확인합니다."""
    msg = str(message).lower()
    error_keywords = [
        "err_name_not_resolved",
        "err_internet_disconnected",
        "err_connection_timed_out",
        "err_timed_out",
        "err_connection_reset",
        "err_connection_refused",
        "err_network_changed",
        "timeout",
    ]
    return any(keyword in msg for keyword in error_keywords)


def safe_driver_get(
    driver: webdriver.Chrome,
    url: str,
    wait_min: float,
    wait_max: float,
    retry_count: int = 2,
) -> tuple[bool, str]:
    """
    driver.get()에서 DNS/네트워크 오류가 나도 프로그램 전체가 죽지 않게 처리합니다.

    반환값:
    - (True, ""): 접속 성공
    - (False, 오류메시지): 재시도 후에도 실패
    """
    last_error = ""

    for attempt in range(1, retry_count + 1):
        try:
            driver.get(url)
            return True, ""
        except (TimeoutException, WebDriverException) as exc:
            last_error = str(exc).splitlines()[0]
            print(f"[경고] 검색 페이지 접속 실패 {attempt}/{retry_count}: {last_error}")

            # 네트워크 계열 오류는 잠시 쉬고 재시도합니다.
            if is_selenium_navigation_error(last_error) and attempt < retry_count:
                random_sleep(wait_min, wait_max)
                continue

            break

    return False, last_error


def is_naver_news_url(url: str) -> bool:
    """URL이 네이버 뉴스 계열인지 확인합니다."""
    try:
        host = urlparse(url).netloc.lower()
        return host in NAVER_NEWS_HOSTS
    except Exception:
        return False


def normalize_naver_news_url(url: str) -> str | None:
    """
    네이버 뉴스 기사 URL을 canonical URL로 정규화합니다.

    수집 대상 예:
    - https://n.news.naver.com/mnews/article/001/0012345678
    - https://n.news.naver.com/article/001/0012345678
    - https://news.naver.com/main/read.naver?oid=001&aid=0012345678
    - https://m.news.naver.com/article/001/0012345678
    """
    if not url:
        return None

    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url

    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    if host not in NAVER_NEWS_HOSTS:
        return None

    match = re.search(r"/(?:mnews/)?article/(\d{3})/(\d{10})", path)
    if match:
        oid, aid = match.group(1), match.group(2)
        return f"https://n.news.naver.com/mnews/article/{oid}/{aid}"

    query = parse_qs(parsed.query)
    oid = query.get("oid", [None])[0]
    aid = query.get("aid", [None])[0]
    if oid and aid and re.fullmatch(r"\d{3}", oid) and re.fullmatch(r"\d{10}", aid):
        return f"https://n.news.naver.com/mnews/article/{oid}/{aid}"

    return None


def is_probably_article_url(url: str) -> bool:
    """
    언론사 원문 링크가 실제 기사 URL에 가까운지 판별합니다.

    v8에서는 네이버 검색 결과 카드 선택자가 현재 HTML과 맞지 않으면 링크가 0개가 되는 문제가 있었습니다.
    v9에서는 전체 a[href]도 보조로 보되, 원문 사이트의 메인/카테고리/서비스 링크가 섞이지 않도록
    기사 URL처럼 보이는 주소만 통과시킵니다.
    """
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()
        query = parsed.query.lower()

        if not host:
            return False

        if host in NAVER_NEWS_HOSTS:
            return normalize_naver_news_url(url) is not None

        if any(blocked in host for blocked in EXCLUDED_HOST_KEYWORDS):
            return False

        # 언론사 메인 페이지, 섹션 첫 화면 등은 기사로 보지 않습니다.
        if path in {"", "/"}:
            return False

        # 명백히 기사와 무관한 파일/리소스 링크 제외
        if re.search(r"\.(jpg|jpeg|png|gif|webp|svg|pdf|zip|mp4|mp3|css|js)$", path):
            return False

        article_patterns = [
            "article", "articles", "news", "view", "read", "contents", "content",
            "mnews", "newsview", "articleview", "news_view", "idxno", "aid", "oid",
        ]

        combined = f"{path}?{query}"
        if any(pattern in combined for pattern in article_patterns):
            return True

        # 기사 URL은 보통 경로나 쿼리에 긴 숫자 ID가 들어갑니다.
        if re.search(r"\d{5,}", combined):
            return True

        return False
    except Exception:
        return False


def normalize_general_url(url: str) -> str | None:
    """언론사 원문 URL을 정규화하되, 기사 URL로 보이는 주소만 남깁니다."""
    if not url:
        return None

    url = url.strip()

    if url.startswith("//"):
        url = "https:" + url

    if not url.startswith(("http://", "https://")):
        return None

    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if not host:
        return None

    if any(blocked in host for blocked in EXCLUDED_HOST_KEYWORDS):
        return None

    if host in NAVER_NEWS_HOSTS:
        return normalize_naver_news_url(url)

    cleaned = parsed._replace(fragment="")
    cleaned_url = urlunparse(cleaned)

    if not is_probably_article_url(cleaned_url):
        return None

    return cleaned_url

def build_search_url(
    query: str,
    start_date: str,
    end_date: str,
    page: int,
    news_office_id: str | None = None,
) -> str:
    """네이버 뉴스 검색 URL을 생성합니다."""
    display_start = format_date(start_date)
    display_end = format_date(end_date)
    nso_start = compact_date(start_date)
    nso_end = compact_date(end_date)

    params = {
        "where": "news",
        "query": query,
        "sm": "tab_opt",
        "sort": 1,  # 최신순
        "photo": 0,
        "field": 0,
        "pd": 3,
        "ds": display_start,
        "de": display_end,
        "start": (page - 1) * 10 + 1,
        "nso": f"so:dd,p:from{nso_start}to{nso_end},a:all",
    }

    if news_office_id:
        params.update(
            {
                "mynews": 1,
                "office_type": 1,
                "office_section_code": 1,
                "news_office_checked": news_office_id,
            }
        )

    return "https://search.naver.com/search.naver?" + urlencode(params, doseq=True)



# -----------------------------------------------------------------------------
# 검색 진단 유틸
# -----------------------------------------------------------------------------


def detect_block_or_captcha(html: str, title: str = "") -> bool:
    """검색 결과 HTML이 차단/캡차/비정상 접근 안내처럼 보이는지 대략 판별합니다."""
    text = f"{title} {html[:3000]}".lower()
    suspicious_keywords = [
        "captcha", "자동입력", "보안문자", "접근이 제한", "비정상적인 접근",
        "일시적으로 제한", "robot", "bot", "too many requests", "429", "forbidden",
    ]
    return any(keyword.lower() in text for keyword in suspicious_keywords)


def check_search_response_status(search_url: str, timeout: int = 10) -> dict[str, str | int | bool]:
    """
    Selenium은 HTTP 상태 코드를 직접 제공하지 않으므로, 첫 페이지 URL에 한해
    requests로 별도 진단 요청을 보내 상태 코드를 확인합니다.

    주의: 실제 링크 수집은 Selenium HTML 기준으로 진행됩니다.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    }
    try:
        response = requests.get(search_url, headers=headers, timeout=timeout, allow_redirects=True)
        sample = clean_text(response.text[:DEBUG_HTML_SAMPLE_CHARS])
        return {
            "status_code": response.status_code,
            "final_url": response.url,
            "blocked_hint": detect_block_or_captcha(response.text, ""),
            "error": "",
            "sample": sample,
        }
    except Exception as e:
        return {
            "status_code": "ERR",
            "final_url": "",
            "blocked_hint": False,
            "error": str(e),
            "sample": "",
        }


def get_anchor_title_text(anchor) -> str:
    """검색 결과 링크의 표시 제목을 최대한 안정적으로 가져옵니다."""
    if anchor is None:
        return ""
    title_attr = anchor.get("title", "")
    text = anchor.get_text(" ", strip=True)
    return clean_text(title_attr or text)


def get_block_title_text(block) -> str:
    """
    네이버 검색 결과 카드 안에서 기사 제목으로 보이는 텍스트를 찾습니다.

    a.info의 '네이버뉴스' 같은 보조 링크 문구가 제목으로 잡히지 않도록,
    news_tit을 먼저 보고, 없으면 카드 안에서 가장 긴 앵커 텍스트를 제목 후보로 사용합니다.
    """
    if block is None:
        return ""

    title_anchor = block.select_one("a.news_tit[href]")
    title_text = get_anchor_title_text(title_anchor)
    if title_text:
        return title_text

    candidates: list[str] = []
    for a in block.select("a[href]"):
        text = get_anchor_title_text(a)
        if not text:
            continue
        # 보조 버튼/서비스 링크 텍스트는 제목 후보에서 제외합니다.
        if text in {"네이버뉴스", "언론사 선정", "동영상기사", "포토", "뉴스", "더보기"}:
            continue
        if len(text) >= 6:
            candidates.append(text)

    if not candidates:
        return ""

    return max(candidates, key=len)


# URL은 다르지만 검색 결과 제목이 같은 링크를 링크 수집 단계에서 제거하기 위한 전역 매핑입니다.
# 예: 네이버뉴스 링크와 언론사 원문 링크가 같은 제목이면 하나만 남깁니다.
SEARCH_TITLE_KEY_BY_URL: dict[str, str] = {}


def extract_article_links_from_search_soup(
    soup: BeautifulSoup,
    include_original_links: bool = True,
) -> list[str]:
    """
    네이버 뉴스 검색 결과 HTML에서 기사 링크를 수집합니다.

    v17_late_dedup 수정점:
    - 링크 수집 단계에서는 제목 중복을 바로 버리지 않습니다.
      먼저 남긴 링크가 본문 수집에 실패하면 같은 제목의 다른 링크를 대체로 시도해야 하기 때문입니다.
    - 같은 검색 결과 카드에서 네이버뉴스 링크를 먼저 넣고, 원문 링크는 두 번째 후보로 넣습니다.
      이후 본문 수집에 성공한 제목만 최종 중복 기준으로 확정합니다.
    - 검색 결과 카드에서 링크가 잡혔으면 전체 a[href] fallback을 실행하지 않습니다.
    - fallback은 카드 선택자가 완전히 실패해서 링크가 0개일 때만 제한적으로 실행합니다.
    """
    links: list[str] = []
    seen_urls: set[str] = set()

    def add_link(url: str | None, search_title: str = "") -> None:
        if not url or url in seen_urls:
            return

        seen_urls.add(url)
        title_key = make_article_title_key(search_title) if search_title else ""
        if title_key:
            SEARCH_TITLE_KEY_BY_URL[url] = title_key
        links.append(url)

    result_blocks = soup.select(
        "div.news_area, div.news_wrap, li.bx, div.group_news li, div.news_contents, "
        "div.total_wrap, div.sds-comps-base-layout, div.sds-comps-vertical-layout"
    )

    for block in result_blocks:
        search_title = get_block_title_text(block)
        naver_candidates: list[str] = []
        original_candidates: list[str] = []

        # 카드 안의 모든 링크를 보되, 최종 선택은 카드당 1개만 합니다.
        for a in block.select("a[href]"):
            href = a.get("href", "")
            naver_url = normalize_naver_news_url(href)
            if naver_url:
                naver_candidates.append(naver_url)
                continue

            if include_original_links:
                original_url = normalize_general_url(href)
                if original_url:
                    original_candidates.append(original_url)

        # 같은 카드에서 네이버뉴스 링크를 먼저 시도하고, 원문 링크는 대체 후보로 남깁니다.
        # 최종 중복 제거는 본문 수집 성공 후에 합니다.
        if naver_candidates:
            add_link(naver_candidates[0], search_title)
        if include_original_links and original_candidates:
            add_link(original_candidates[0], search_title)

    # 카드 선택자가 실패해서 아무 링크도 못 잡은 경우에만 fallback을 사용합니다.
    if not links:
        fallback_anchors = soup.select("a.news_tit[href], div.news_area a[href], div.news_wrap a[href], li.bx a[href]")
        for a in fallback_anchors:
            href = a.get("href", "")
            search_title = get_anchor_title_text(a)

            naver_url = normalize_naver_news_url(href)
            if naver_url:
                add_link(naver_url, search_title)
                continue

            if include_original_links:
                original_url = normalize_general_url(href)
                if original_url:
                    add_link(original_url, search_title)

    return links


def collect_article_links(
    query: str,
    start_date: str,
    end_date: str,
    max_pages: int = 100,
    news_office_id: str | None = None,
    include_original_links: bool = True,
    sleep_sec: float = DEFAULT_SEARCH_SLEEP_MIN,
    sleep_max_sec: float = DEFAULT_SEARCH_SLEEP_MAX,
    empty_page_limit: int | None = DEFAULT_EMPTY_PAGE_LIMIT,
    block_page_limit: int | None = DEFAULT_BLOCK_PAGE_LIMIT,
    driver: webdriver.Chrome | None = None,
) -> tuple[list[str], dict[str, object]]:
    """
    지정한 기간에서 기사 링크를 수집합니다.

    반환값:
    - links: 해당 날짜 조각 안에서 중복 제거된 기사 링크 목록
    - stats: 차단/중복 여부 확인용 진단 정보
    """
    own_driver = driver is None
    driver = driver or setup_chromedriver()

    links: list[str] = []
    seen: set[str] = set()
    empty_pages = 0
    consecutive_blocked_pages = 0

    stats: dict[str, object] = {
        "start_date": start_date,
        "end_date": end_date,
        "first_request_url": "",
        "first_status_code": "",
        "first_final_url": "",
        "first_status_error": "",
        "first_status_blocked_hint": False,
        "first_status_sample": "",
        "first_selenium_url": "",
        "first_selenium_title": "",
        "pages_checked": 0,
        "noresult_pages": 0,
        "empty_pages": 0,
        "blocked_hint_pages": 0,
        "consecutive_blocked_pages": 0,
        "stopped_by_block": False,
        "stopped_by_navigation_error": False,
        "navigation_error_pages": 0,
        "last_navigation_error": "",
        "block_page_limit": block_page_limit,
        "raw_found_links": 0,
        "unique_links_in_chunk": 0,
    }

    try:
        for page in tqdm(range(1, max_pages + 1), desc=f"링크 수집 {start_date}~{end_date}"):
            search_url = build_search_url(
                query=query,
                start_date=start_date,
                end_date=end_date,
                page=page,
                news_office_id=news_office_id,
            )

            if page == 1:
                status_info = check_search_response_status(search_url)
                stats["first_request_url"] = search_url
                stats["first_status_code"] = status_info.get("status_code", "")
                stats["first_final_url"] = status_info.get("final_url", "")
                stats["first_status_error"] = status_info.get("error", "")
                stats["first_status_blocked_hint"] = status_info.get("blocked_hint", False)
                stats["first_status_sample"] = status_info.get("sample", "")

            ok, navigation_error = safe_driver_get(
                driver=driver,
                url=search_url,
                wait_min=sleep_sec,
                wait_max=sleep_max_sec,
                retry_count=2,
            )
            if not ok:
                stats["pages_checked"] = int(stats["pages_checked"]) + 1
                stats["navigation_error_pages"] = int(stats.get("navigation_error_pages", 0)) + 1
                stats["last_navigation_error"] = navigation_error
                consecutive_blocked_pages += 1
                stats["consecutive_blocked_pages"] = consecutive_blocked_pages

                print(f"[건너뜀] 검색 페이지 접속 실패: {search_url}")
                print(f"        원인: {navigation_error}")

                if block_page_limit is not None and consecutive_blocked_pages >= block_page_limit:
                    stats["stopped_by_navigation_error"] = True
                    print(
                        f"[중단] {start_date}~{end_date}: "
                        f"검색 페이지 접속 실패가 {consecutive_blocked_pages}회 연속 감지되어 "
                        "이 날짜 조각의 링크 수집을 중단합니다."
                    )
                    break

                random_sleep(sleep_sec, sleep_max_sec)
                continue

            random_sleep(sleep_sec, sleep_max_sec)

            html = driver.page_source
            soup = BeautifulSoup(html, "html.parser")
            stats["pages_checked"] = int(stats["pages_checked"]) + 1

            if page == 1:
                stats["first_selenium_url"] = driver.current_url
                stats["first_selenium_title"] = driver.title

            if detect_block_or_captcha(html, driver.title):
                stats["blocked_hint_pages"] = int(stats["blocked_hint_pages"]) + 1
                consecutive_blocked_pages += 1
                stats["consecutive_blocked_pages"] = consecutive_blocked_pages

                if block_page_limit is not None and consecutive_blocked_pages >= block_page_limit:
                    stats["stopped_by_block"] = True
                    print(
                        f"[중단] {start_date}~{end_date}: "
                        f"차단 의심 페이지가 {consecutive_blocked_pages}회 연속 감지되어 "
                        "이 날짜 조각의 링크 수집을 중단합니다."
                    )
                    break

                continue
            else:
                consecutive_blocked_pages = 0
                stats["consecutive_blocked_pages"] = 0

            if soup.select_one("div.api_noresult_wrap"):
                stats["noresult_pages"] = int(stats["noresult_pages"]) + 1
                empty_pages += 1
                stats["empty_pages"] = empty_pages
                continue

            page_links = extract_article_links_from_search_soup(
                soup=soup,
                include_original_links=include_original_links,
            )
            stats["raw_found_links"] = int(stats["raw_found_links"]) + len(page_links)

            new_count = 0
            for href in page_links:
                if href not in seen:
                    seen.add(href)
                    links.append(href)
                    new_count += 1

            if new_count == 0:
                empty_pages += 1
            else:
                empty_pages = 0
            stats["empty_pages"] = empty_pages

            if empty_page_limit is not None and empty_pages >= empty_page_limit:
                break

    finally:
        if own_driver:
            driver.quit()

    stats["unique_links_in_chunk"] = len(links)
    return links, stats




def collect_article_links_by_date_chunks(
    query: str,
    start_date: str,
    end_date: str,
    max_pages_per_range: int,
    split_mode: str = "month",
    news_office_id: str | None = None,
    include_original_links: bool = True,
    sleep_sec: float = DEFAULT_SEARCH_SLEEP_MIN,
    sleep_max_sec: float = DEFAULT_SEARCH_SLEEP_MAX,
    empty_page_limit: int | None = DEFAULT_EMPTY_PAGE_LIMIT,
    block_page_limit: int | None = DEFAULT_BLOCK_PAGE_LIMIT,
) -> tuple[list[str], list[dict[str, object]]]:
    """날짜 범위를 나눠서 기사 링크를 수집하고, 조각별 진단 정보를 함께 반환합니다."""
    date_ranges = make_date_ranges(start_date, end_date, split_mode)
    print(f"날짜 분할 방식: {split_mode}")
    print(f"검색 기간 조각 수: {len(date_ranges)}")
    print(f"언론사 원문 링크 포함: {'예' if include_original_links else '아니오'}")
    print("진단 로그: 각 날짜 조각마다 요청 URL, HTTP 상태, 발견 링크, 새 링크, 중복 링크를 출력합니다.")

    driver = setup_chromedriver()
    all_links: list[str] = []
    seen: set[str] = set()
    debug_rows: list[dict[str, object]] = []

    try:
        for ds, de in date_ranges:
            chunk_links, stats = collect_article_links(
                query=query,
                start_date=ds,
                end_date=de,
                max_pages=max_pages_per_range,
                news_office_id=news_office_id,
                include_original_links=include_original_links,
                sleep_sec=sleep_sec,
                sleep_max_sec=sleep_max_sec,
                empty_page_limit=empty_page_limit,
                block_page_limit=block_page_limit,
                driver=driver,
            )

            before_count = len(all_links)
            search_title_duplicate_count = 0
            for link in chunk_links:
                # v17: 제목 기준 중복은 링크 수집 단계에서 버리지 않습니다.
                # 먼저 남긴 URL이 본문 수집에 실패할 수 있으므로, 최종 중복 제거는 기사 수집 성공 후에 합니다.
                if link not in seen:
                    seen.add(link)
                    all_links.append(link)

            added_count = len(all_links) - before_count
            duplicate_count = len(chunk_links) - added_count

            row = dict(stats)
            row.update(
                {
                    "chunk_unique_links": len(chunk_links),
                    "global_new_links": added_count,
                    "global_duplicate_links": duplicate_count,
                    "search_title_duplicate_links": search_title_duplicate_count,
                    "global_total_links": len(all_links),
                }
            )
            debug_rows.append(row)

            print(f"{ds} ~ {de}")
            print(f"  요청 URL(1페이지): {row.get('first_request_url', '')}")
            print(
                f"  HTTP 상태(진단용): {row.get('first_status_code', '')} "
                f"/ 최종 URL: {row.get('first_final_url', '')}"
            )
            if row.get("first_status_error"):
                print(f"  HTTP 상태 확인 오류: {row.get('first_status_error')}")
            print(
                f"  확인 페이지: {row.get('pages_checked', 0)}개, "
                f"검색결과없음 페이지: {row.get('noresult_pages', 0)}개, "
                f"차단 의심 페이지: {row.get('blocked_hint_pages', 0)}개, "
                f"차단으로 중단: {'예' if row.get('stopped_by_block') else '아니오'}"
            )
            print(
                f"  이번 기간 발견 링크: {row.get('raw_found_links', 0)}개, "
                f"기간 내 고유 링크: {len(chunk_links)}개, "
                f"전체 기준 새 링크: {added_count}개, "
                f"전체 기준 중복 링크: {duplicate_count}개, "
                f"제목 중복 링크: {search_title_duplicate_count}개, "
                f"누적: {len(all_links)}개"
            )
            if row.get("first_status_blocked_hint") or int(row.get("blocked_hint_pages", 0)) > 0:
                print("  [주의] 차단/캡차/비정상 접근 안내로 의심되는 문구가 감지되었습니다.")
            if int(row.get("navigation_error_pages", 0)) > 0:
                print(f"  [주의] 검색 페이지 접속 실패가 있었습니다: {row.get('last_navigation_error', '')}")
            if row.get("first_status_code") not in {200, "200"}:
                print("  [주의] 진단용 HTTP 상태 코드가 200이 아닙니다.")

            if row.get("stopped_by_block"):
                print("[전체 중단] 차단 의심 상태가 확인되어 남은 날짜 조각 수집을 중단합니다.")
                break
            if row.get("stopped_by_navigation_error"):
                print("[전체 중단] 검색 페이지 접속 실패가 반복되어 남은 날짜 조각 수집을 중단합니다.")
                break

    finally:
        driver.quit()

    print(f"수집된 전체 기사 링크 수: {len(all_links)}")
    return all_links, debug_rows



# -----------------------------------------------------------------------------
# 기사 제목/본문 추출
# -----------------------------------------------------------------------------


def remove_unwanted_tags(soup: BeautifulSoup) -> None:
    """본문 추출에 방해되는 태그를 제거합니다."""
    for tag in soup.select(
        "script, style, noscript, iframe, svg, form, input, button, "
        "nav, header, footer, aside, figure, figcaption"
    ):
        tag.decompose()


def get_meta_content(soup: BeautifulSoup, *selectors: str) -> str:
    """meta 태그 content 값을 가져옵니다."""
    for selector in selectors:
        element = soup.select_one(selector)
        if element:
            content = clean_text(element.get("content", ""))
            if content:
                return content
    return ""


def extract_naver_title(soup: BeautifulSoup) -> str:
    """네이버 뉴스 기사 HTML에서 제목을 추출합니다."""
    selectors = [
        "h2#title_area > span",
        "h2.media_end_head_headline > span",
        "div.media_end_head_title span",
    ]

    for selector in selectors:
        element = soup.select_one(selector)
        if element:
            title = clean_text(element.get_text(" ", strip=True))
            if title:
                return title

    return get_meta_content(
        soup,
        "meta[property='og:title']",
        "meta[name='twitter:title']",
    )


def extract_naver_body(soup: BeautifulSoup) -> str:
    """네이버 뉴스 기사 HTML에서 본문을 추출합니다."""
    selectors = [
        "article#dic_area",
        "#dic_area",
        "div#newsct_article",
        "div#articeBody",
        "article",
    ]

    for selector in selectors:
        body = soup.select_one(selector)
        if not body:
            continue

        for tag in body.select("script, style, iframe, button, figure, em, strong.media_end_summary"):
            tag.decompose()

        text = clean_text(body.get_text(" ", strip=True))
        if len(text) >= 30:
            return text

    return ""


def extract_general_title(soup: BeautifulSoup) -> str:
    """언론사 원문 기사 HTML에서 제목을 범용 방식으로 추출합니다."""
    title = get_meta_content(
        soup,
        "meta[property='og:title']",
        "meta[name='twitter:title']",
        "meta[name='title']",
    )
    if title:
        return title

    for selector in ["h1", "h2"]:
        element = soup.select_one(selector)
        if element:
            title = clean_text(element.get_text(" ", strip=True))
            if title:
                return title

    if soup.title:
        return clean_text(soup.title.get_text(" ", strip=True))

    return ""


def text_from_element(element) -> str:
    """선택된 HTML 요소에서 문장성이 있는 텍스트를 추출합니다."""
    # p 태그가 충분히 있으면 p만 모으는 편이 메뉴/광고 노이즈가 적습니다.
    paragraphs = []
    for p in element.select("p"):
        text = clean_text(p.get_text(" ", strip=True))
        if len(text) >= 20:
            paragraphs.append(text)

    if paragraphs:
        return clean_text(" ".join(paragraphs))

    return clean_text(element.get_text(" ", strip=True))


def extract_general_body(soup: BeautifulSoup) -> str:
    """언론사 원문 기사 HTML에서 본문을 범용 방식으로 추출합니다."""
    remove_unwanted_tags(soup)

    selectors = [
        "article",
        "#articleBody",
        "#articleBodyContents",
        "#article_body",
        "#articeBody",
        "#news_body_area",
        "#newsView",
        "#newsct_article",
        "#article-view-content-div",
        ".article_body",
        ".article-body",
        ".article_view",
        ".article-view",
        ".article_txt",
        ".article_text",
        ".article-content",
        ".article_content",
        ".news_body",
        ".news-body",
        ".news_content",
        ".news-content",
        ".view_cont",
        ".view-content",
        ".view_con",
        ".story-news",
        ".contents",
        "#contents",
    ]

    candidates: list[str] = []

    for selector in selectors:
        for element in soup.select(selector):
            text = text_from_element(element)
            if len(text) >= MIN_BODY_LEN:
                candidates.append(text)

    if candidates:
        # 가장 긴 텍스트를 본문 후보로 사용합니다.
        return max(candidates, key=len)

    # 마지막 fallback: 문단 전체를 이어 붙입니다.
    paragraphs = []
    for p in soup.select("p"):
        text = clean_text(p.get_text(" ", strip=True))
        if len(text) >= 20:
            paragraphs.append(text)

    fallback = clean_text(" ".join(paragraphs))
    if len(fallback) >= MIN_BODY_LEN:
        return fallback

    # 그래도 안 되면 description을 사용합니다. 본문이 아니라 요약일 수 있습니다.
    return get_meta_content(
        soup,
        "meta[property='og:description']",
        "meta[name='description']",
        "meta[name='twitter:description']",
    )


def fetch_html(url: str, timeout: int = 10) -> BeautifulSoup:
    """URL에서 HTML을 가져와 BeautifulSoup 객체로 변환합니다."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    }

    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()

    # 언론사 원문은 인코딩이 섞일 수 있어 apparent_encoding을 반영합니다.
    if response.apparent_encoding:
        response.encoding = response.apparent_encoding

    return BeautifulSoup(response.text, "html.parser")


def collect_article_title_body(url: str, timeout: int = 10) -> dict[str, str]:
    """기사 URL 하나에서 제목과 본문만 추출합니다."""
    try:
        soup = fetch_html(url, timeout=timeout)

        if is_naver_news_url(url):
            title = extract_naver_title(soup)
            body = extract_naver_body(soup)
            source_type = "naver_news"
        else:
            title = extract_general_title(soup)
            body = extract_general_body(soup)
            source_type = "original_site"

        return {
            "url": url,
            "source_type": source_type,
            "title": title,
            "body": body,
        }

    except Exception as e:
        print(f"기사 수집 실패: {url} / {e}")
        return {
            "url": url,
            "source_type": "unknown",
            "title": "",
            "body": "",
        }



# -----------------------------------------------------------------------------
# 네이버 뉴스 댓글 수집
# -----------------------------------------------------------------------------

COMMENT_TEXT_SELECTORS = [
    # 네이버 댓글의 실제 본문은 보통 이 클래스에 들어갑니다.
    # 이전 버전의 [class*='comment'] [class*='content'] 같은 넓은 선택자는
    # 로그인 박스, 검색 기록, 클린봇 안내문까지 댓글로 잡는 문제가 있어 제거했습니다.
    "span.u_cbox_contents",
    ".u_cbox_comment_box span.u_cbox_contents",
    ".u_cbox_area span.u_cbox_contents",
    ".u_cbox_text_wrap span.u_cbox_contents",
]


COMMENT_UI_TEXT_PATTERNS = [
    "등록된 댓글이 없습니다",
    "댓글이 없습니다",
    "댓글을 작성",
    "댓글 작성",
    "댓글 더보기",
    "클린봇",
    "악성댓글",
    "프로필 사진",
    "로그아웃",
    "@naver.com",
    "네이버ID",
    "네이버 멤버십",
    "네이버 멤버쉽",
    "N Pay",
    "내 페이",
    "최근 검색 기록",
    "검색 기록이 없습니다",
    "뉴스 댓글 운영",
    "댓글 운영",
    "댓글 정책",
    "운영규정",
    "개인정보",
    "서비스 이용약관",
    "작성자에 의해 삭제된 댓글",
    "삭제된 댓글입니다",
    "내가 차단한 이용자의 댓글",
    "차단한 이용자",
]


def is_valid_comment_text(text: str) -> bool:
    """댓글 본문으로 보기 어려운 UI/안내/빈 문장을 제외합니다."""
    text = clean_text(text)
    if len(text) < 2:
        return False

    exact_blocklist = {
        "댓글",
        "답글",
        "더보기",
        "공감",
        "비공감",
        "삭제",
        "확인",
        "취소",
        "전체 댓글",
        "광고",
        "최신순",
        "순공감순",
        "과거순",
    }
    if text in exact_blocklist:
        return False

    for pattern in COMMENT_UI_TEXT_PATTERNS:
        if pattern in text:
            return False

    # 로그인/내정보 패널처럼 여러 UI 단어가 한 덩어리로 붙은 경우를 제거합니다.
    ui_hit_count = sum(
        1
        for pattern in ["로그아웃", "네이버", "N Pay", "최근 검색", "프로필", "멤버십", "보안설정"]
        if pattern in text
    )
    if ui_hit_count >= 2:
        return False

    # 따옴표, 기호, 이모지만 남은 값은 Word2Vec 학습용 댓글로 쓰기 어렵습니다.
    # 예: "", '', …, 🤮🤮 같은 값은 제외합니다.
    if not re.search(r"[가-힣A-Za-z0-9]", text):
        return False

    # CSV/HTML 처리 과정에서 따옴표만 반복된 값이 들어오는 경우를 제거합니다.
    if re.fullmatch(r"[\s\"'‘’“”`´.,…·ㆍ~!?:;\-_=+\[\](){}<>|/\\]+", text):
        return False

    return True


COMMENT_MORE_BUTTON_SELECTORS = [
    ".u_cbox_btn_more",
    "a.u_cbox_btn_more",
    "button.u_cbox_btn_more",
    "a[class*='u_cbox_btn_more']",
    "button[class*='u_cbox_btn_more']",
    "a[class*='more']",
    "button[class*='more']",
]


def extract_naver_article_ids(url: str) -> tuple[str, str] | None:
    """네이버 뉴스 URL에서 언론사 ID(oid)와 기사 ID(aid)를 추출합니다."""
    patterns = [
        r"/mnews/article/(\d+)/(\d+)",
        r"/article/(\d+)/(\d+)",
        r"/article/comment/(\d+)/(\d+)",
        r"/mnews/article/comment/(\d+)/(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1), match.group(2)
    return None


def build_naver_comment_urls(article_url: str) -> list[str]:
    """네이버 뉴스 기사 URL에서 댓글 페이지 후보 URL을 만듭니다."""
    ids = extract_naver_article_ids(article_url)
    if not ids:
        return []

    oid, aid = ids
    parsed = urlparse(article_url)
    query = parse_qs(parsed.query)
    sid = query.get("sid", [""])[0]
    sid_query = f"?sid={sid}" if sid else ""

    return [
        f"https://n.news.naver.com/mnews/article/comment/{oid}/{aid}{sid_query}",
        f"https://n.news.naver.com/article/comment/{oid}/{aid}{sid_query}",
    ]


def _extract_comment_texts_from_current_context(driver: webdriver.Chrome) -> list[str]:
    """
    현재 문서 또는 iframe 안에서 네이버 댓글 본문만 추출합니다.

    핵심:
    - span.u_cbox_contents 중심으로만 추출합니다.
    - 실제 댓글 박스 안에 있는 노드만 통과시킵니다.
    - 로그인 패널, 검색 기록, 클린봇 안내문 같은 UI 문구는 이후 Python 필터에서 제거합니다.
    """
    script = """
        const selectors = arguments[0];
        const results = [];

        function isVisible(node) {
            return !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
        }

        for (const selector of selectors) {
            const nodes = document.querySelectorAll(selector);
            for (const node of nodes) {
                if (!isVisible(node)) continue;

                // 네이버 댓글 영역 내부의 실제 댓글 후보만 허용합니다.
                const commentBox =
                    node.closest('.u_cbox_comment') ||
                    node.closest('.u_cbox_comment_box') ||
                    node.closest('li.u_cbox_comment') ||
                    node.closest('.u_cbox_area');

                if (!commentBox) continue;

                const text = (node.innerText || node.textContent || '').trim();
                if (text) results.push(text);
            }
        }
        return results;
    """
    try:
        return driver.execute_script(script, COMMENT_TEXT_SELECTORS) or []
    except Exception:
        return []


def _click_comment_more_in_current_context(driver: webdriver.Chrome) -> bool:
    script = """
        const selectors = arguments[0];
        for (const selector of selectors) {
            const nodes = Array.from(document.querySelectorAll(selector));
            for (const node of nodes) {
                const text = (node.innerText || node.textContent || '').trim();
                const visible = !!(node.offsetWidth || node.offsetHeight || node.getClientRects().length);
                if (!visible) continue;
                if (text.includes('더보기') || text.includes('댓글 더보기') || selector.includes('more')) {
                    node.scrollIntoView({block: 'center'});
                    node.click();
                    return true;
                }
            }
        }
        return false;
    """
    try:
        return bool(driver.execute_script(script, COMMENT_MORE_BUTTON_SELECTORS))
    except Exception:
        return False


def extract_comment_texts_from_page(driver: webdriver.Chrome) -> list[str]:
    texts: list[str] = []

    driver.switch_to.default_content()
    texts.extend(_extract_comment_texts_from_current_context(driver))

    try:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
    except Exception:
        frames = []

    for frame in frames:
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(frame)
            texts.extend(_extract_comment_texts_from_current_context(driver))
        except Exception:
            continue
    driver.switch_to.default_content()

    cleaned: list[str] = []
    seen: set[str] = set()
    for text in texts:
        text = clean_text(text)
        if not is_valid_comment_text(text):
            continue
        if text not in seen:
            seen.add(text)
            cleaned.append(text)
    return cleaned


def click_comment_more(driver: webdriver.Chrome) -> bool:
    driver.switch_to.default_content()
    if _click_comment_more_in_current_context(driver):
        driver.switch_to.default_content()
        return True

    try:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
    except Exception:
        frames = []

    for frame in frames:
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(frame)
            if _click_comment_more_in_current_context(driver):
                driver.switch_to.default_content()
                return True
        except Exception:
            continue

    driver.switch_to.default_content()
    return False


def collect_naver_comments(
    article_url: str,
    driver: webdriver.Chrome,
    max_comments: int = 50,
    max_more_clicks: int = 5,
    wait_min: float = 1.5,
    wait_max: float = 3.0,
) -> list[str]:
    """
    네이버 뉴스 기사 댓글을 수집합니다.

    제한:
    - 네이버 뉴스 URL에서만 동작합니다.
    - 네이버 댓글 구조가 바뀌거나 로그인/차단/댓글 비허용 기사면 빈 목록을 반환할 수 있습니다.
    """
    if not is_naver_news_url(article_url):
        return []

    comment_urls = build_naver_comment_urls(article_url)
    if not comment_urls:
        return []

    all_comments: list[str] = []
    seen_comments: set[str] = set()

    for comment_url in comment_urls:
        ok, navigation_error = safe_driver_get(
            driver=driver,
            url=comment_url,
            wait_min=wait_min,
            wait_max=wait_max,
            retry_count=2,
        )
        if not ok:
            print(f"[댓글 건너뜀] 댓글 페이지 접속 실패: {comment_url} / {navigation_error}")
            continue

        random_sleep(wait_min, wait_max)

        for click_idx in range(max_more_clicks + 1):
            try:
                driver.switch_to.default_content()
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            except Exception:
                pass

            random_sleep(wait_min, wait_max)
            comments = extract_comment_texts_from_page(driver)
            for comment in comments:
                if comment not in seen_comments:
                    seen_comments.add(comment)
                    all_comments.append(comment)
                    if len(all_comments) >= max_comments:
                        return all_comments[:max_comments]

            if click_idx >= max_more_clicks:
                break
            if not click_comment_more(driver):
                break

        if all_comments:
            break

    return all_comments[:max_comments]


def join_comments_for_csv(comments: list[str]) -> str:
    return " || ".join(clean_text(comment) for comment in comments if clean_text(comment))


# -----------------------------------------------------------------------------
# 결과 저장
# -----------------------------------------------------------------------------


def make_word2vec_line(title: str, body: str, body_chars: int, comments: list[str] | None = None) -> str:
    """
    기사 제목, 본문, 댓글을 Word2Vec 입력용 한 줄 문장으로 합칩니다.

    body_chars:
    - 200이면 본문 앞 200자만 사용합니다.
    - 0이면 본문 전체를 사용합니다.
    - comments가 있으면 댓글도 같은 줄 뒤에 붙입니다.
    """
    title = clean_text(title)
    body = clean_text(body)

    if body_chars > 0:
        body = body[:body_chars]

    comment_text = " ".join(clean_text(comment) for comment in (comments or []) if clean_text(comment))
    return clean_text(f"{title} {body} {comment_text}")


def save_word2vec_txt(articles: list[dict[str, object]], output_txt: str, body_chars: int) -> int:
    """
    기사 목록을 '한 기사 = 한 줄' TXT 파일로 저장합니다.

    댓글 수집을 켠 경우에는 해당 기사의 댓글도 같은 줄 뒤에 붙여 저장합니다.
    """
    output_path = Path(output_txt)
    saved_count = 0

    with output_path.open("w", encoding="utf-8") as f:
        for article in articles:
            comments = article.get("comments_list", [])
            if not isinstance(comments, list):
                comments = []
            line = make_word2vec_line(
                title=str(article.get("title", "")),
                body=str(article.get("body", "")),
                body_chars=body_chars,
                comments=comments,
            )
            if line:
                f.write(line + "\n")
                saved_count += 1

    return saved_count


def save_debug_csv(articles: list[dict[str, object]], output_csv: str) -> None:
    """
    확인용 CSV를 저장합니다.

    댓글은 comments 하나의 칸에 합치지 않고, comment_1, comment_2 ...처럼
    댓글 하나당 CSV의 마지막 열 하나씩 배치합니다.
    """
    max_comment_count = 0
    for article in articles:
        comments = article.get("comments_list", [])
        if isinstance(comments, list):
            max_comment_count = max(max_comment_count, len(comments))

    comment_fields = [f"comment_{i}" for i in range(1, max_comment_count + 1)]
    fieldnames = ["url", "source_type", "title", "body"] + comment_fields

    with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for article in articles:
            comments = article.get("comments_list", [])
            if not isinstance(comments, list):
                comments = []

            row = {
                "url": article.get("url", ""),
                "source_type": article.get("source_type", ""),
                "title": article.get("title", ""),
                "body": article.get("body", ""),
            }
            for index, comment in enumerate(comments, start=1):
                row[f"comment_{index}"] = clean_text(comment)

            writer.writerow(row)



def save_search_debug_csv(debug_rows: list[dict[str, object]], output_csv: str) -> None:
    """날짜 조각별 검색 진단 로그를 CSV로 저장합니다."""
    if not debug_rows:
        return

    fieldnames = [
        "start_date", "end_date", "first_request_url", "first_status_code", "first_final_url",
        "first_status_error", "first_status_blocked_hint", "first_selenium_url",
        "first_selenium_title", "pages_checked", "noresult_pages", "empty_pages",
        "blocked_hint_pages", "consecutive_blocked_pages", "stopped_by_block", "block_page_limit",
        "raw_found_links", "unique_links_in_chunk",
        "chunk_unique_links", "global_new_links", "global_duplicate_links", "global_total_links",
        "first_status_sample",
    ]
    with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(debug_rows)


def crawl_and_make_word2vec_input(
    query: str,
    start_date: str,
    end_date: str,
    max_pages_per_range: int,
    output_txt: str,
    body_chars: int = DEFAULT_BODY_CHARS,
    split_mode: str = "month",
    news_office_id: str | None = None,
    include_original_links: bool = True,
    search_sleep_min: float = DEFAULT_SEARCH_SLEEP_MIN,
    search_sleep_max: float = DEFAULT_SEARCH_SLEEP_MAX,
    article_sleep_min: float = DEFAULT_ARTICLE_SLEEP_MIN,
    article_sleep_max: float = DEFAULT_ARTICLE_SLEEP_MAX,
    block_page_limit: int | None = DEFAULT_BLOCK_PAGE_LIMIT,
    save_csv: bool = False,
    require_keywords_in_title: bool = True,
    title_filter_keywords: list[str] | None = None,
    save_search_debug_csv_file: bool = True,
    collect_comments: bool = False,
    max_comments_per_article: int = 50,
    max_comment_more_clicks: int = 5,
) -> str:
    """뉴스 제목/본문을 수집하고 Word2Vec 입력용 TXT 파일만 생성합니다."""
    links, search_debug_rows = collect_article_links_by_date_chunks(
        query=query,
        start_date=start_date,
        end_date=end_date,
        max_pages_per_range=max_pages_per_range,
        split_mode=split_mode,
        news_office_id=news_office_id,
        include_original_links=include_original_links,
        sleep_sec=search_sleep_min,
        sleep_max_sec=search_sleep_max,
        empty_page_limit=None,
        block_page_limit=block_page_limit,
    )

    articles: list[dict[str, str]] = []
    seen_article_title_keys: set[str] = set()
    seen_article_content_keys: set[str] = set()
    saved_search_title_keys: set[str] = set()
    failed_body_count = 0
    filtered_by_title_count = 0
    duplicate_prefetch_title_count = 0
    duplicate_title_count = 0
    duplicate_content_count = 0
    naver_success = 0
    original_success = 0
    comments_success = 0
    comments_total = 0
    comments_driver: webdriver.Chrome | None = None

    if collect_comments:
        print("네이버 뉴스 댓글 수집: 사용")
        print(f"기사당 최대 댓글 수: {max_comments_per_article}")
        comments_driver = setup_chromedriver()
    else:
        print("네이버 뉴스 댓글 수집: 미사용")

    title_filter_keywords = title_filter_keywords or [query]

    try:
        for url in tqdm(links, desc="제목/본문/댓글 수집" if collect_comments else "제목/본문 수집"):
            link_search_title_key = SEARCH_TITLE_KEY_BY_URL.get(url, "")
            if link_search_title_key and link_search_title_key in saved_search_title_keys:
                # 같은 검색 결과 제목의 다른 URL은 이미 본문 수집에 성공했으므로 열어보지 않고 건너뜁니다.
                duplicate_prefetch_title_count += 1
                continue

            article = collect_article_title_body(url)
            if article["title"] and article["body"]:
                if require_keywords_in_title and not contains_any_keyword(article["title"], title_filter_keywords):
                    filtered_by_title_count += 1
                    random_sleep(article_sleep_min, article_sleep_max)
                    continue

                # 1차 중복 제거: 제목이 같으면 같은 기사로 봅니다.
                # 네이버 링크와 언론사 원문 링크는 URL/본문 구조가 달라도 제목이 같은 경우가 많습니다.
                title_key = make_article_title_key(article["title"])
                if title_key in seen_article_title_keys:
                    duplicate_title_count += 1
                    random_sleep(article_sleep_min, article_sleep_max)
                    continue
                seen_article_title_keys.add(title_key)
                if link_search_title_key:
                    saved_search_title_keys.add(link_search_title_key)

                # 2차 중복 제거: 제목은 조금 다르지만 본문 앞부분이 거의 같은 기사까지 한 번 더 제거합니다.
                article_key = make_article_content_key(article["title"], article["body"])
                if article_key in seen_article_content_keys:
                    duplicate_content_count += 1
                    random_sleep(article_sleep_min, article_sleep_max)
                    continue
                seen_article_content_keys.add(article_key)

                article["comments_list"] = []
                article["comments"] = ""
                if collect_comments and article.get("source_type") == "naver_news" and comments_driver is not None:
                    comments = collect_naver_comments(
                        article_url=article["url"],
                        driver=comments_driver,
                        max_comments=max_comments_per_article,
                        max_more_clicks=max_comment_more_clicks,
                        wait_min=article_sleep_min,
                        wait_max=article_sleep_max,
                    )
                    article["comments_list"] = comments
                    # 내부 호환용 문자열입니다. CSV 저장 시에는 이 값을 쓰지 않고 comment_1, comment_2 ... 열로 나눕니다.
                    article["comments"] = join_comments_for_csv(comments)
                    comments_total += len(comments)
                    if comments:
                        comments_success += 1

                articles.append(article)
                if article.get("source_type") == "naver_news":
                    naver_success += 1
                elif article.get("source_type") == "original_site":
                    original_success += 1
            else:
                failed_body_count += 1
            random_sleep(article_sleep_min, article_sleep_max)

    finally:
        if comments_driver is not None:
            comments_driver.quit()

    saved_count = save_word2vec_txt(articles, output_txt, body_chars=body_chars)

    if save_csv:
        csv_name = str(Path(output_txt).with_suffix(".csv"))
        save_debug_csv(articles, csv_name)
        print(f"확인용 CSV 저장 완료: {os.path.abspath(csv_name)}")

    if save_search_debug_csv_file:
        search_debug_name = str(Path(output_txt).with_name(Path(output_txt).stem + "_search_debug.csv"))
        save_search_debug_csv(search_debug_rows, search_debug_name)
        print(f"검색 진단 CSV 저장 완료: {os.path.abspath(search_debug_name)}")

    print("=" * 60)
    print("Word2Vec 입력용 TXT 생성 완료")
    print(f"검색어: {query}")
    print(f"수집된 기사 링크 수: {len(links)}")
    print(f"검색 진단 기간 수: {len(search_debug_rows)}")
    print(f"검색 페이지 랜덤 대기: {search_sleep_min}~{search_sleep_max}초")
    print(f"기사 본문 랜덤 대기: {article_sleep_min}~{article_sleep_max}초")
    print(f"차단 의심 연속 감지 중단 기준: {block_page_limit}페이지")
    print(f"제목/본문 수집 성공 기사 수: {len(articles)}")
    print(f"  - 네이버 뉴스 성공: {naver_success}")
    print(f"  - 언론사 원문 성공: {original_success}")
    if collect_comments:
        print(f"댓글 수집 성공 기사 수: {comments_success}")
        print(f"수집된 댓글 수: {comments_total}")
    if require_keywords_in_title:
        print(f"제목 필터 키워드: {', '.join(title_filter_keywords)}")
        print(f"제목 필터로 제외된 기사 수: {filtered_by_title_count}")
    print(f"본문 수집 전 제목 중복으로 건너뛴 링크 수: {duplicate_prefetch_title_count}")
    print(f"제목 중복으로 제외된 수: {duplicate_title_count}")
    print(f"내용 중복으로 제외된 수: {duplicate_content_count}")
    print(f"중복 기사로 제외된 총수: {duplicate_prefetch_title_count + duplicate_title_count + duplicate_content_count}")
    print(f"제목/본문 실패 기사 수: {failed_body_count}")
    print(f"TXT 저장 줄 수: {saved_count}")
    print(f"본문 사용 길이: {'전체' if body_chars == 0 else str(body_chars) + '자'}")
    print(f"저장 파일: {os.path.abspath(output_txt)}")
    print("=" * 60)

    return output_txt



# -----------------------------------------------------------------------------
# GUI 실행
# -----------------------------------------------------------------------------

import contextlib
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


class QueueWriter:
    """print/tqdm 출력을 Tkinter Text 위젯으로 보내기 위한 writer입니다."""

    def __init__(self, log_queue: queue.Queue):
        self.log_queue = log_queue

    def write(self, text: str) -> None:
        if text:
            self.log_queue.put(text)

    def flush(self) -> None:
        pass


class NaverNewsCrawlerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("네이버 뉴스 → Word2Vec 입력 TXT 생성기")
        self.geometry("940x760")
        self.minsize(860, 640)

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.worker_thread: threading.Thread | None = None

        self.query_var = tk.StringVar(value="MZ세대")
        self.start_date_var = tk.StringVar(value="20260101")
        self.end_date_var = tk.StringVar(value="20260701")
        self.split_mode_var = tk.StringVar(value="week")
        self.max_pages_var = tk.StringVar(value="20")
        self.body_chars_var = tk.StringVar(value="200")
        self.news_office_id_var = tk.StringVar(value="")
        self.include_original_var = tk.BooleanVar(value=True)
        self.require_title_filter_var = tk.BooleanVar(value=True)
        self.title_keywords_var = tk.StringVar(value="MZ")
        self.save_csv_var = tk.BooleanVar(value=True)
        self.save_search_debug_csv_var = tk.BooleanVar(value=True)
        self.collect_comments_var = tk.BooleanVar(value=False)
        self.max_comments_var = tk.StringVar(value="50")
        self.max_comment_more_clicks_var = tk.StringVar(value="5")
        self.output_txt_var = tk.StringVar(value="MZ_word2vec_input.txt")
        self.search_sleep_min_var = tk.StringVar(value=str(DEFAULT_SEARCH_SLEEP_MIN))
        self.search_sleep_max_var = tk.StringVar(value=str(DEFAULT_SEARCH_SLEEP_MAX))
        self.article_sleep_min_var = tk.StringVar(value=str(DEFAULT_ARTICLE_SLEEP_MIN))
        self.article_sleep_max_var = tk.StringVar(value=str(DEFAULT_ARTICLE_SLEEP_MAX))
        self.block_page_limit_var = tk.StringVar(value=str(DEFAULT_BLOCK_PAGE_LIMIT))

        self._build_ui()
        self._poll_log_queue()
        self._sync_title_keyword_state()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        input_frame = ttk.LabelFrame(root, text="수집 설정", padding=12)
        input_frame.pack(fill="x")

        for col in range(4):
            input_frame.columnconfigure(col, weight=1)

        self._add_labeled_entry(input_frame, "검색어", self.query_var, 0, 0)
        self._add_labeled_entry(input_frame, "시작일", self.start_date_var, 0, 2)
        self._add_labeled_entry(input_frame, "종료일", self.end_date_var, 1, 2)

        ttk.Label(input_frame, text="날짜 분할").grid(row=1, column=0, sticky="w", pady=5)
        split_combo = ttk.Combobox(
            input_frame,
            textvariable=self.split_mode_var,
            values=["none", "month", "week", "day"],
            state="readonly",
            width=20,
        )
        split_combo.grid(row=1, column=1, sticky="ew", padx=(8, 16), pady=5)

        self._add_labeled_entry(input_frame, "조각당 최대 페이지", self.max_pages_var, 2, 0)
        self._add_labeled_entry(input_frame, "본문 사용 글자 수", self.body_chars_var, 2, 2)
        self._add_labeled_entry(input_frame, "언론사 ID", self.news_office_id_var, 3, 0)
        self._add_labeled_entry(input_frame, "검색 최소 대기초", self.search_sleep_min_var, 3, 2)
        self._add_labeled_entry(input_frame, "검색 최대 대기초", self.search_sleep_max_var, 4, 0)
        self._add_labeled_entry(input_frame, "기사 최소 대기초", self.article_sleep_min_var, 4, 2)
        self._add_labeled_entry(input_frame, "기사 최대 대기초", self.article_sleep_max_var, 5, 0)
        self._add_labeled_entry(input_frame, "차단 중단 페이지", self.block_page_limit_var, 5, 2)
        self._add_labeled_entry(input_frame, "기사당 최대 댓글 수", self.max_comments_var, 6, 0)
        self._add_labeled_entry(input_frame, "댓글 더보기 클릭 수", self.max_comment_more_clicks_var, 6, 2)

        option_frame = ttk.Frame(input_frame)
        option_frame.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        option_frame.columnconfigure(3, weight=1)

        ttk.Checkbutton(
            option_frame,
            text="언론사 원문 사이트 포함",
            variable=self.include_original_var,
        ).grid(row=0, column=0, sticky="w", padx=(0, 22))

        ttk.Checkbutton(
            option_frame,
            text="제목 키워드 필터 사용",
            variable=self.require_title_filter_var,
            command=self._sync_title_keyword_state,
        ).grid(row=0, column=1, sticky="w", padx=(0, 22))

        ttk.Checkbutton(
            option_frame,
            text="확인용 CSV 저장",
            variable=self.save_csv_var,
        ).grid(row=0, column=2, sticky="w", padx=(0, 22))

        ttk.Checkbutton(
            option_frame,
            text="검색 진단 CSV 저장",
            variable=self.save_search_debug_csv_var,
        ).grid(row=0, column=3, sticky="w", padx=(0, 22))

        ttk.Checkbutton(
            option_frame,
            text="네이버 댓글 수집",
            variable=self.collect_comments_var,
        ).grid(row=0, column=4, sticky="w", padx=(0, 22))

        ttk.Label(option_frame, text="제목 필터 키워드").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.title_keywords_entry = ttk.Entry(option_frame, textvariable=self.title_keywords_var)
        self.title_keywords_entry.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(8, 0))

        output_frame = ttk.LabelFrame(root, text="저장 위치", padding=12)
        output_frame.pack(fill="x", pady=(12, 0))
        output_frame.columnconfigure(1, weight=1)

        ttk.Label(output_frame, text="TXT 파일").grid(row=0, column=0, sticky="w")
        ttk.Entry(output_frame, textvariable=self.output_txt_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(output_frame, text="찾기", command=self._browse_output).grid(row=0, column=2, sticky="e")

        run_frame = ttk.Frame(root)
        run_frame.pack(fill="x", pady=(12, 0))
        run_frame.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(run_frame, textvariable=self.status_var).grid(row=0, column=0, sticky="w")

        self.progress = ttk.Progressbar(run_frame, mode="indeterminate", length=220)
        self.progress.grid(row=0, column=1, sticky="e", padx=(8, 8))

        self.start_button = ttk.Button(run_frame, text="수집 시작", command=self._start)
        self.start_button.grid(row=0, column=2, sticky="e")

        log_frame = ttk.LabelFrame(root, text="실행 로그", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(12, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap="word", height=18)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        guide = (
            "주의: 차단 의심 페이지가 연속 감지되면 자동 중단합니다. "
            "검색어와 제목 필터 키워드는 분리할 수 있습니다. 예: 검색어=MZ세대, 제목 필터=MZ"
        )
        ttk.Label(root, text=guide, foreground="#555555").pack(fill="x", pady=(8, 0))

    def _add_labeled_entry(self, parent, label: str, variable: tk.StringVar, row: int, col: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", pady=5)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=col + 1, sticky="ew", padx=(8, 16), pady=5)

    def _browse_output(self) -> None:
        initial = self.output_txt_var.get().strip() or f"{safe_filename(self.query_var.get())}_word2vec_input.txt"
        path = filedialog.asksaveasfilename(
            title="TXT 저장 파일 선택",
            initialfile=initial,
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.output_txt_var.set(path)

    def _sync_title_keyword_state(self) -> None:
        if self.require_title_filter_var.get():
            self.title_keywords_entry.configure(state="normal")
        else:
            self.title_keywords_entry.configure(state="disabled")

    def _validate_inputs(self) -> dict:
        query = self.query_var.get().strip()
        start_date = self.start_date_var.get().strip()
        end_date = self.end_date_var.get().strip()
        output_txt = self.output_txt_var.get().strip()

        if not query:
            raise ValueError("검색어를 입력해야 합니다.")
        if not start_date or not end_date:
            raise ValueError("시작일과 종료일을 입력해야 합니다.")
        # 날짜 형식 검증. 내부 함수가 YYYYMMDD / YYYY.MM.DD를 처리합니다.
        parse_date(start_date)
        parse_date(end_date)

        max_pages_per_range = int(self.max_pages_var.get().strip())
        if max_pages_per_range <= 0:
            raise ValueError("조각당 최대 페이지는 1 이상이어야 합니다.")

        body_chars = int(self.body_chars_var.get().strip())
        if body_chars < 0:
            raise ValueError("본문 사용 글자 수는 0 이상이어야 합니다. 전체 사용은 0입니다.")

        search_sleep_min = float(self.search_sleep_min_var.get().strip())
        search_sleep_max = float(self.search_sleep_max_var.get().strip())
        article_sleep_min = float(self.article_sleep_min_var.get().strip())
        article_sleep_max = float(self.article_sleep_max_var.get().strip())
        block_page_limit = int(self.block_page_limit_var.get().strip())
        max_comments_per_article = int(self.max_comments_var.get().strip())
        max_comment_more_clicks = int(self.max_comment_more_clicks_var.get().strip())

        if min(search_sleep_min, search_sleep_max, article_sleep_min, article_sleep_max) < 0:
            raise ValueError("대기초는 0 이상이어야 합니다.")
        if search_sleep_max < search_sleep_min:
            raise ValueError("검색 최대 대기초는 검색 최소 대기초보다 작을 수 없습니다.")
        if article_sleep_max < article_sleep_min:
            raise ValueError("기사 최대 대기초는 기사 최소 대기초보다 작을 수 없습니다.")
        if block_page_limit <= 0:
            raise ValueError("차단 중단 페이지는 1 이상이어야 합니다.")
        if max_comments_per_article < 0:
            raise ValueError("기사당 최대 댓글 수는 0 이상이어야 합니다.")
        if max_comment_more_clicks < 0:
            raise ValueError("댓글 더보기 클릭 수는 0 이상이어야 합니다.")

        split_mode = self.split_mode_var.get().strip().lower()
        if split_mode not in {"none", "month", "week", "day"}:
            raise ValueError("날짜 분할은 none/month/week/day 중 하나여야 합니다.")

        if not output_txt:
            output_txt = f"{safe_filename(query)}_word2vec_input.txt"
            self.output_txt_var.set(output_txt)

        title_filter_keywords = []
        if self.require_title_filter_var.get():
            keyword_text = self.title_keywords_var.get().strip() or query
            title_filter_keywords = split_filter_keywords(keyword_text)
            if not title_filter_keywords:
                raise ValueError("제목 필터 키워드가 비어 있습니다.")

        if self.collect_comments_var.get() and not self.save_csv_var.get():
            self.save_csv_var.set(True)
            messagebox.showinfo("CSV 저장 자동 설정", "댓글은 CSV의 comments 칸에 저장되므로 확인용 CSV 저장을 자동으로 켰습니다.")

        return {
            "query": query,
            "start_date": start_date,
            "end_date": end_date,
            "max_pages_per_range": max_pages_per_range,
            "output_txt": output_txt,
            "body_chars": body_chars,
            "split_mode": split_mode,
            "news_office_id": self.news_office_id_var.get().strip() or None,
            "include_original_links": self.include_original_var.get(),
            "search_sleep_min": search_sleep_min,
            "search_sleep_max": search_sleep_max,
            "article_sleep_min": article_sleep_min,
            "article_sleep_max": article_sleep_max,
            "block_page_limit": block_page_limit,
            "save_csv": self.save_csv_var.get(),
            "save_search_debug_csv_file": self.save_search_debug_csv_var.get(),
            "require_keywords_in_title": self.require_title_filter_var.get(),
            "title_filter_keywords": title_filter_keywords,
            "collect_comments": self.collect_comments_var.get(),
            "max_comments_per_article": max_comments_per_article,
            "max_comment_more_clicks": max_comment_more_clicks,
        }

    def _start(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("실행 중", "이미 수집이 진행 중입니다.")
            return

        try:
            params = self._validate_inputs()
        except Exception as e:
            messagebox.showerror("입력 오류", str(e))
            return

        self.log_text.delete("1.0", "end")
        self.status_var.set("수집 중")
        self.start_button.configure(state="disabled")
        self.progress.start(10)

        self.worker_thread = threading.Thread(target=self._run_worker, args=(params,), daemon=True)
        self.worker_thread.start()

    def _run_worker(self, params: dict) -> None:
        writer = QueueWriter(self.log_queue)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                print("=" * 60)
                print("네이버 뉴스 제목/본문/댓글 → Word2Vec 입력 TXT 생성기 GUI v21_comment_csv_quote_fixed")
                print("=" * 60)
                crawl_and_make_word2vec_input(**params)
            self.log_queue.put("\n[완료]\n")
            self.after(0, self._finish_success)
        except Exception as e:
            error_message = str(e)
            self.log_queue.put(f"\n[오류] {error_message}\n")
            self.after(0, lambda msg=error_message: self._finish_error(msg))

    def _finish_success(self) -> None:
        self.progress.stop()
        self.status_var.set("완료")
        self.start_button.configure(state="normal")
        messagebox.showinfo("완료", "TXT 생성이 완료되었습니다.")

    def _finish_error(self, message: str) -> None:
        self.progress.stop()
        self.status_var.set("오류")
        self.start_button.configure(state="normal")
        messagebox.showerror("오류", message)

    def _poll_log_queue(self) -> None:
        try:
            while True:
                text = self.log_queue.get_nowait()
                # tqdm 출력의 \r은 GUI 로그에서 줄을 어지럽힐 수 있어 줄바꿈으로 바꿉니다.
                text = text.replace("\r", "\n")
                self.log_text.insert("end", text)
                self.log_text.see("end")
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)


if __name__ == "__main__":
    app = NaverNewsCrawlerGUI()
    app.mainloop()