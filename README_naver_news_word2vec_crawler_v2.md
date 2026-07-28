# 네이버 뉴스 Word2Vec 입력 TXT 생성기 사용법

네이버 뉴스 검색 결과에서 기사 제목과 본문을 수집한 뒤, Word2Vec 학습에 바로 넣을 수 있는 TXT 파일을 생성하는 도구입니다.

이 도구는 Word2Vec 학습 자체를 수행하지 않습니다. 역할은 뉴스 기사 수집과 입력 파일 생성입니다.

```text
네이버 뉴스 검색
→ 기사 링크 수집
→ 네이버 뉴스/언론사 원문 본문 수집
→ 제목·본문 중복 제거
→ Word2Vec 입력용 TXT 저장
→ 확인용 CSV 저장
```

## 파일

| 파일 | 설명 |
|---|---|
| `naver_news_to_word2vec_gui_v17_late_dedup.py` | GUI 방식 실행 파일. 창에서 검색어와 옵션을 입력합니다. |
| `naver_news_to_word2vec_input_v17_late_dedup.py` | 콘솔 입력 방식 실행 파일. 터미널에서 값을 입력합니다. |

GUI 방식이 사용하기 더 쉽고, 콘솔 방식은 코드 테스트나 반복 실행에 적합합니다.

## 사용 라이브러리

이 도구는 Word2Vec 학습 라이브러리인 `gensim`을 사용하지 않습니다. 역할은 뉴스 기사 수집과 Word2Vec 입력용 TXT 생성이므로, 크롤링·HTML 파싱·GUI·CSV 저장에 필요한 라이브러리만 사용합니다.

### 외부 설치 라이브러리

| 라이브러리 | 코드 import | 사용 위치 | 역할 |
|---|---|---|---|
| `requests` | `import requests` | `check_search_response_status()`, `fetch_html()` | 검색 URL의 HTTP 상태를 진단하고, 기사 본문 HTML을 요청합니다. |
| `beautifulsoup4` | `from bs4 import BeautifulSoup` | `extract_article_links_from_search_soup()`, `extract_naver_title()`, `extract_naver_body()`, `extract_general_title()`, `extract_general_body()` | 검색 결과 HTML과 기사 HTML에서 링크, 제목, 본문을 파싱합니다. |
| `selenium` | `from selenium import webdriver` | `setup_chromedriver()`, `safe_driver_get()`, `collect_article_links()` | 네이버 뉴스 검색 결과 페이지를 Chrome 브라우저로 열고 렌더링된 HTML을 가져옵니다. |
| `chromedriver-autoinstaller` | `import chromedriver_autoinstaller` | `setup_chromedriver()` | 현재 Chrome 버전에 맞는 ChromeDriver를 자동 설치·연결합니다. |
| `tqdm` | `from tqdm import tqdm` | `collect_article_links()`, `crawl_and_make_word2vec_input()` | 링크 수집과 본문 수집 진행률을 콘솔에 표시합니다. |

기존 README에 `pandas`가 포함되어 있었다면, v17 기준 코드에서는 필수는 아닙니다. CSV 저장은 파이썬 기본 `csv` 모듈로 처리합니다.

### 파이썬 기본 라이브러리

| 라이브러리 | 코드 import | 역할 |
|---|---|---|
| `csv` | `import csv` | 확인용 CSV와 검색 진단 CSV를 저장합니다. |
| `os` | `import os` | 저장 파일의 절대 경로 출력 등에 사용합니다. |
| `random` | `import random` | 검색/기사 요청 사이의 랜덤 대기 시간을 만듭니다. |
| `re` | `import re` | 키워드 정규화, URL 패턴 검사, 제목 비교에 사용합니다. |
| `time` | `import time` | 요청 사이 대기 처리에 사용합니다. |
| `datetime`, `timedelta` | `from datetime import datetime, timedelta` | 시작일·종료일을 월/주/일 단위 날짜 조각으로 나눕니다. |
| `pathlib` | `from pathlib import Path` | 파일 경로 처리에 사용합니다. |
| `urllib.parse` | `parse_qs`, `urlencode`, `urlparse`, `urlunparse` | 검색 URL 생성과 기사 URL 정규화에 사용합니다. |
| `tkinter` | `import tkinter as tk` | GUI 창, 입력칸, 버튼, 체크박스, 파일 선택창을 만듭니다. GUI 버전에만 사용됩니다. |
| `threading` | `import threading` | GUI가 멈추지 않게 크롤링을 백그라운드 스레드에서 실행합니다. GUI 버전에만 사용됩니다. |
| `queue` | `import queue` | 백그라운드 스레드의 로그를 GUI 로그창으로 전달합니다. GUI 버전에만 사용됩니다. |
| `contextlib` | `import contextlib` | GUI에서 표준 출력 로그를 안전하게 리다이렉트할 때 사용합니다. |


## 준비

필요 패키지를 설치합니다.

```powershell
pip install requests beautifulsoup4 selenium chromedriver-autoinstaller tqdm
```

Python 가상환경을 사용한다면 해당 가상환경을 활성화한 뒤 설치하세요.

예시:

```powershell
C:\Users\Woongs\PycharmProjects\frame-break\.venv\Scripts\python.exe -m pip install requests beautifulsoup4 selenium chromedriver-autoinstaller tqdm
```

## 실행

### GUI 방식

```powershell
python naver_news_to_word2vec_gui_v17_late_dedup.py
```

PyCharm에서는 파일을 열고 우클릭한 뒤 `Run`을 누르면 됩니다.

### 콘솔 입력 방식

```powershell
python naver_news_to_word2vec_input_v17_late_dedup.py
```

콘솔 입력 방식은 실행 후 터미널에 나오는 질문에 직접 값을 입력하는 방식입니다.

예시:

```text
검색어: MZ세대
시작일: 20260101
종료일: 20260701
날짜 분할: month
각 기간 조각당 최대 페이지 수: 10
본문 몇 자까지 사용할지: 200
언론사 ID, 모르면 엔터:
언론사 원문 사이트도 포함할까요?: y
제목 필터 키워드: MZ
```

## 추천 설정

처음 테스트할 때는 아래처럼 설정하는 것을 권장합니다.

| 항목 | 추천값 |
|---|---|
| 검색어 | `MZ세대` |
| 시작일 | `20260101` |
| 종료일 | `20260701` |
| 날짜 분할 | `month` |
| 조각당 최대 페이지 | `5` ~ `10` |
| 본문 사용 글자 수 | `200` |
| 언론사 원문 링크 포함 | `예` |
| 제목 키워드 필터 사용 | `예` |
| 제목 필터 키워드 | `MZ` |
| 검색 최소 대기초 | `5` |
| 검색 최대 대기초 | `10` |
| 기사 최소 대기초 | `2` |
| 기사 최대 대기초 | `5` |
| 차단 중단 페이지 | `3` |

`week × 20페이지`처럼 요청 수가 큰 설정은 차단/캡차/접속 실패가 발생하기 쉽습니다. 먼저 `month × 5~10페이지`로 정상 수집되는지 확인한 뒤 늘리는 편이 안전합니다.

## 주요 옵션

| 옵션 | 설명 | 권장값 |
|---|---|---|
| 검색어 | 네이버 뉴스에서 검색할 단어입니다. | `MZ세대` |
| 시작일 | 수집 시작 날짜입니다. `YYYYMMDD` 또는 `YYYY.MM.DD` 형식을 사용할 수 있습니다. | `20260101` |
| 종료일 | 수집 종료 날짜입니다. | `20260701` |
| 날짜 분할 | 전체 기간을 나누어 검색하는 방식입니다. `none`, `month`, `week`, `day` 중 선택합니다. | `month` |
| 조각당 최대 페이지 | 각 날짜 조각에서 확인할 검색 결과 페이지 수입니다. | `5~10` |
| 본문 사용 글자 수 | TXT에 저장할 본문 길이입니다. `0`이면 전체 본문을 저장합니다. | `200` |
| 언론사 ID | 특정 언론사만 검색할 때 사용합니다. 모르면 비워둡니다. | 빈칸 |
| 언론사 원문 링크 포함 | 네이버 뉴스 링크 외에 언론사 원문 링크도 수집할지 정합니다. | 예 |
| 제목 키워드 필터 | 최종 저장할 기사 제목에 특정 단어가 들어가야 하는지 검사합니다. | 예 |
| 제목 필터 키워드 | 제목에 포함되어야 하는 단어입니다. 여러 개는 쉼표로 구분합니다. | `MZ` |
| 확인용 CSV 저장 | TXT 외에 URL, 제목, 본문, 출처 유형을 CSV로 저장합니다. | 예 |
| 검색 대기초 | 검색 페이지 요청 사이의 랜덤 대기 시간입니다. | `5~10` |
| 기사 대기초 | 기사 본문 요청 사이의 랜덤 대기 시간입니다. | `2~5` |
| 차단 중단 페이지 | 차단 의심 페이지가 연속으로 몇 번 나오면 중단할지 정합니다. | `3` |


## 코드에서 각 동작이 실행되는 위치

아래 표는 `naver_news_to_word2vec_gui_v17_late_dedup.py`와 `naver_news_to_word2vec_input_v17_late_dedup.py` 기준입니다. 줄 번호는 파일 수정에 따라 달라질 수 있으므로, PyCharm에서는 `Ctrl + F`로 함수명을 검색하는 것이 가장 정확합니다.

### 전체 실행 흐름

```text
GUI 또는 콘솔에서 옵션 입력
→ crawl_and_make_word2vec_input() 실행
→ make_date_ranges()로 날짜 조각 생성
→ collect_article_links_by_date_chunks()로 날짜 조각별 링크 수집
→ collect_article_links()가 각 검색 결과 페이지 접속
→ extract_article_links_from_search_soup()가 기사 후보 링크 추출
→ collect_article_title_body()가 각 기사 URL에서 제목/본문 추출
→ 제목 필터·중복 제거 적용
→ save_word2vec_txt()로 TXT 저장
→ save_debug_csv(), save_search_debug_csv()로 CSV 저장
```

### 실행 시작 지점

| 동작 | GUI 파일 위치 | 콘솔 파일 위치 | 설명 |
|---|---|---|---|
| GUI 창 생성 | `NaverNewsCrawlerGUI.__init__()` | 없음 | 검색어, 날짜, 페이지 수, 필터, 저장 경로 입력창을 만듭니다. |
| GUI 화면 구성 | `NaverNewsCrawlerGUI._build_ui()` | 없음 | 라벨, 입력칸, 체크박스, 실행 버튼, 로그창을 배치합니다. |
| GUI 입력값 검사 | `NaverNewsCrawlerGUI._validate_inputs()` | 없음 | 날짜, 페이지 수, 대기 시간, 제목 필터 키워드 등을 검사합니다. |
| GUI 실행 버튼 처리 | `NaverNewsCrawlerGUI._start()` | 없음 | 실행 버튼을 누르면 백그라운드 스레드를 시작합니다. |
| GUI 백그라운드 실행 | `NaverNewsCrawlerGUI._run_worker()` | 없음 | GUI가 멈추지 않도록 실제 크롤링을 별도 스레드에서 실행합니다. |
| 콘솔 입력 시작 | 없음 | `if __name__ == "__main__":` | 터미널에 질문을 출력하고 사용자가 입력한 값을 받습니다. |
| 전체 크롤링 시작 | `crawl_and_make_word2vec_input()` | `crawl_and_make_word2vec_input()` | GUI와 콘솔 모두 최종적으로 이 함수를 호출합니다. |

### 날짜 분할과 검색 URL 생성

| 동작 | 함수 | 설명 |
|---|---|---|
| 날짜 형식 정리 | `format_date()` | `20260101`을 `2026.01.01` 형식으로 바꿉니다. |
| 날짜 압축 | `compact_date()` | `2026.01.01`을 `20260101` 형식으로 바꿉니다. |
| 날짜 객체 변환 | `parse_date()` | 문자열 날짜를 `datetime` 객체로 바꿉니다. |
| 월 마지막 날 계산 | `last_day_of_month()` | `month` 분할에서 월 단위 종료일을 계산합니다. |
| 날짜 조각 생성 | `make_date_ranges()` | 전체 기간을 `none`, `month`, `week`, `day` 방식으로 나눕니다. |
| 네이버 검색 URL 생성 | `build_search_url()` | 검색어, 날짜 조각, 페이지 번호, 언론사 ID를 이용해 네이버 뉴스 검색 URL을 만듭니다. |

### 검색 페이지 접속과 링크 수집

| 동작 | 함수 | 설명 |
|---|---|---|
| ChromeDriver 준비 | `setup_chromedriver()` | Chrome 옵션을 설정하고 ChromeDriver를 준비합니다. |
| 검색 페이지 열기 | `safe_driver_get()` | Selenium으로 검색 URL을 열고, DNS 오류나 네트워크 오류가 나면 해당 페이지만 건너뜁니다. |
| 차단 여부 감지 | `detect_block_or_captcha()` | HTML과 페이지 제목에서 캡차, 비정상 접근 안내, 차단 의심 문구를 찾습니다. |
| HTTP 상태 진단 | `check_search_response_status()` | 각 날짜 조각의 1페이지 URL을 `requests`로 확인해 HTTP 상태와 최종 URL을 기록합니다. |
| 네이버뉴스 URL 판별 | `is_naver_news_url()` | URL이 `n.news.naver.com`, `news.naver.com`, `m.news.naver.com` 계열인지 확인합니다. |
| 네이버뉴스 URL 정리 | `normalize_naver_news_url()` | 네이버뉴스 URL에서 불필요한 쿼리와 모바일/데스크톱 차이를 정리합니다. |
| 일반 기사 URL 판별 | `is_probably_article_url()` | 언론사 원문 URL이 실제 기사 URL처럼 보이는지 검사합니다. |
| 일반 URL 정리 | `normalize_general_url()` | 언론사 원문 URL을 정규화하고, 네이버 메일·웹툰·고객센터 같은 비기사 링크를 제외합니다. |
| 검색 결과 제목 추출 | `get_anchor_title_text()`, `get_block_title_text()` | 검색 결과 카드 안의 제목 텍스트를 가져와 중복 후보 판단에 사용합니다. |
| 검색 결과에서 링크 추출 | `extract_article_links_from_search_soup()` | 검색 결과 HTML에서 네이버뉴스 링크와 원문 링크 후보를 추출합니다. |
| 날짜 조각 하나의 링크 수집 | `collect_article_links()` | 특정 날짜 조각의 여러 페이지를 돌며 링크를 수집합니다. |
| 전체 날짜 조각 링크 수집 | `collect_article_links_by_date_chunks()` | `make_date_ranges()`로 만든 모든 날짜 조각을 순서대로 처리합니다. |

### 제목/본문 추출

| 동작 | 함수 | 설명 |
|---|---|---|
| 불필요 태그 제거 | `remove_unwanted_tags()` | `script`, `style`, 광고성 태그 등을 제거합니다. |
| 메타 태그 추출 | `get_meta_content()` | `og:title`, `og:description` 같은 메타 정보를 가져옵니다. |
| 네이버뉴스 제목 추출 | `extract_naver_title()` | 네이버뉴스 페이지 전용 선택자로 제목을 찾습니다. |
| 네이버뉴스 본문 추출 | `extract_naver_body()` | `newsct_article`, `dic_area` 등 네이버뉴스 본문 선택자에서 본문을 찾습니다. |
| 원문 기사 제목 추출 | `extract_general_title()` | 언론사 원문 사이트에서 `h1`, 메타 태그 등을 이용해 제목을 찾습니다. |
| HTML 요소 텍스트화 | `text_from_element()` | 본문 후보 HTML 요소를 일반 텍스트로 변환합니다. |
| 원문 기사 본문 추출 | `extract_general_body()` | `article`, `div`, `section` 등 범용 선택자로 원문 본문을 찾습니다. |
| 기사 HTML 요청 | `fetch_html()` | 기사 URL에 직접 접속해 HTML을 가져오고 `BeautifulSoup` 객체로 바꿉니다. |
| 제목/본문 통합 수집 | `collect_article_title_body()` | URL 종류에 따라 네이버뉴스 전용 추출 또는 원문 범용 추출을 실행합니다. |

### 필터링, 중복 제거, 저장

| 동작 | 함수 | 설명 |
|---|---|---|
| 텍스트 정리 | `clean_text()` | 줄바꿈, 탭, 연속 공백을 한 칸으로 정리합니다. |
| 랜덤 대기 | `random_sleep()` | 검색/기사 요청 사이에 지정 범위 안에서 랜덤하게 쉽니다. |
| 파일명 안전화 | `safe_filename()` | 검색어를 파일명에 사용할 수 있는 문자열로 바꿉니다. |
| 키워드 비교용 정규화 | `normalize_keyword_text()` | 공백과 특수문자 차이를 줄여 제목 비교와 키워드 필터에 사용합니다. |
| 제목 필터 키워드 분리 | `split_filter_keywords()` | `MZ, MZ세대`처럼 쉼표로 입력한 키워드를 리스트로 바꿉니다. |
| 제목 키워드 포함 검사 | `contains_any_keyword()` | 기사 제목에 필터 키워드 중 하나가 들어가는지 확인합니다. |
| 제목 중복 키 생성 | `make_article_title_key()` | 제목 기준 중복 제거용 키를 만듭니다. |
| 내용 중복 키 생성 | `make_article_content_key()` | `제목 + 본문 앞부분` 기준 중복 제거용 키를 만듭니다. |
| Word2Vec 한 줄 생성 | `make_word2vec_line()` | `제목 + 본문 일부`를 한 줄 문자열로 합칩니다. |
| TXT 저장 | `save_word2vec_txt()` | 한 기사당 한 줄인 Word2Vec 입력용 TXT를 저장합니다. |
| 확인용 CSV 저장 | `save_debug_csv()` | URL, 제목, 본문, 출처 유형 등을 CSV로 저장합니다. |
| 검색 진단 CSV 저장 | `save_search_debug_csv()` | 날짜 조각별 요청 URL, HTTP 상태, 발견 링크 수, 차단 의심 페이지 수 등을 CSV로 저장합니다. |

### v17 중복 제거가 실제로 적용되는 위치

중복 제거의 핵심은 `crawl_and_make_word2vec_input()` 내부에서 실행됩니다.

```text
1. 링크 후보를 순서대로 하나씩 본문 수집 시도
2. 제목 키가 이미 성공 저장된 기사와 같으면 건너뜀
3. 본문 수집 성공 후 제목 필터 검사
4. 제목 중복 검사
5. 제목+본문 앞부분 기준 내용 중복 검사
6. 중복이 아니면 articles 리스트에 저장
7. 마지막에 TXT/CSV로 저장
```

이 구조 때문에 네이버 링크와 원문 링크가 둘 다 있어도, 먼저 성공한 하나만 최종 저장되고, 먼저 시도한 링크가 실패하면 같은 제목의 다른 링크를 대체로 시도할 수 있습니다.


## 결과물

실행이 끝나면 기본적으로 TXT 파일이 생성됩니다.

```text
MZ_word2vec_input.txt
```

확인용 CSV 저장을 켜면 CSV도 함께 생성됩니다.

```text
MZ_word2vec_input.csv
```

### TXT 형식

TXT는 한 기사당 한 줄로 저장됩니다.

```text
기사 제목 기사 본문 앞 200자
기사 제목 기사 본문 앞 200자
기사 제목 기사 본문 앞 200자
```

이 파일은 기존 Word2Vec 코드의 `--input` 값으로 넣으면 됩니다.

```powershell
python word2vec_korean.py --input MZ_word2vec_input.txt --words MZ 청년 소비 --save mz_news.model
```

### CSV 형식

CSV에는 보통 아래 정보가 들어갑니다.

| 컬럼 | 설명 |
|---|---|
| `url` | 수집한 기사 URL |
| `title` | 기사 제목 |
| `body` | 기사 본문 |
| `source_type` | `naver_news` 또는 `original_site` |
| `search_query` | 검색어 |
| `date_start` | 해당 검색 날짜 조각 시작일 |
| `date_end` | 해당 검색 날짜 조각 종료일 |

CSV는 수집 결과를 검수할 때 사용합니다. Word2Vec 학습에는 TXT를 사용하면 됩니다.

## 중복 제거 방식

v17 버전은 원문 링크와 네이버 링크가 둘 다 있는 기사에서 하나만 살리기 위해 중복 제거 순서를 조정했습니다.

기존 방식의 문제는 다음과 같았습니다.

```text
네이버 링크와 원문 링크가 같은 제목
→ 링크 수집 단계에서 하나를 먼저 삭제
→ 남긴 링크의 본문 수집 실패
→ 삭제된 대체 링크는 다시 시도하지 못함
→ 최종 결과에서 기사 자체가 사라짐
```

v17 방식은 다음처럼 동작합니다.

```text
1. 링크 수집 단계에서는 네이버 링크와 원문 링크를 후보로 보관
2. 먼저 한 링크에서 본문 수집 시도
3. 성공하면 같은 제목의 다른 링크는 중복으로 제외
4. 실패하면 같은 제목의 다른 링크를 대체로 시도
5. 최종 저장 직전에 제목 중복과 내용 중복을 다시 제거
```

즉, 중복은 제거하되 **최소 한 개의 기사 본문은 살리는 방식**입니다.

## 차단 감지와 안전 대기

네이버 검색을 너무 빠르게 반복하면 차단/캡차/비정상 접근 안내 페이지가 나올 수 있습니다.

이 도구는 차단 의심 페이지가 연속으로 감지되면 자동으로 수집을 중단합니다.

로그 예시:

```text
[중단] 차단 의심 페이지가 3회 연속 감지되어 이 날짜 조각의 링크 수집을 중단합니다.
[전체 중단] 차단 의심 상태가 확인되어 남은 날짜 조각 수집을 중단합니다.
```

이 경우 같은 조건으로 바로 다시 실행하지 말고, 아래처럼 설정을 낮추는 것이 좋습니다.

```text
날짜 분할: month
조각당 최대 페이지: 5
검색 최소 대기초: 8
검색 최대 대기초: 15
언론사 원문 링크 포함: 아니오
```

## 로그 해석

실행 중에는 날짜 조각별로 진단 로그가 출력됩니다.

```text
2026.05.07 ~ 2026.05.13
  요청 URL(1페이지): ...
  HTTP 상태(진단용): 200
  확인 페이지: 10개
  검색결과없음 페이지: 0개
  차단 의심 페이지: 0개
  이번 기간 발견 링크: 120개
  기간 내 고유 링크: 80개
  전체 기준 새 링크: 35개
  전체 기준 중복 링크: 45개
  누적: 600개
```

| 항목 | 의미 |
|---|---|
| 확인 페이지 | 실제로 확인한 검색 결과 페이지 수 |
| 검색결과없음 페이지 | 네이버가 검색 결과 없음으로 응답한 페이지 수 |
| 차단 의심 페이지 | 캡차/비정상 접근 안내로 의심되는 페이지 수 |
| 이번 기간 발견 링크 | 해당 날짜 조각에서 발견한 전체 링크 수 |
| 기간 내 고유 링크 | 해당 날짜 조각 안에서 중복을 뺀 링크 수 |
| 전체 기준 새 링크 | 이전 날짜 조각까지 포함해 처음 발견된 링크 수 |
| 전체 기준 중복 링크 | 이미 수집된 링크와 겹친 수 |
| 누적 | 전체 누적 링크 수 |

`차단 의심 페이지`가 계속 증가하면 요청이 막힌 상태일 가능성이 큽니다.

`이번 기간 발견 링크`는 있는데 `전체 기준 새 링크`가 0이면, 검색 결과가 이전 날짜 조각과 중복으로 반복되는 상황입니다.

## 자주 발생하는 오류

### `net::ERR_NAME_NOT_RESOLVED`

ChromeDriver가 특정 페이지의 도메인을 찾지 못했을 때 발생합니다.

가능한 원인:

```text
인터넷 연결 문제
DNS 문제
네이버 또는 언론사 사이트 접속 실패
너무 빠른 반복 요청
일시적인 사이트 오류
```

v15 이후 버전은 이 오류가 나도 프로그램 전체가 종료되지 않도록 처리합니다. 해당 페이지만 건너뛰고 계속 진행합니다.

### `차단 의심 페이지: 20개`

차단 또는 캡차 안내 페이지가 반복된 상태입니다. 이 경우 수집을 계속하면 더 악화될 수 있습니다.

대응:

```text
1. 프로그램 중단
2. 일정 시간 대기
3. 조각당 페이지 수 줄이기
4. 검색 대기초 늘리기
5. 가능하면 네이버 API 방식 병행
```

### 결과가 0개

다음 순서로 확인하세요.

```text
1. 제목 필터를 꺼서 다시 실행
2. 검색어를 더 넓게 변경
3. 날짜 분할을 month로 변경
4. 조각당 페이지 수를 5로 낮춤
5. 언론사 원문 링크 포함을 끄고 네이버 뉴스만 수집
6. 로그에서 차단 의심 페이지 수 확인
```

## Word2Vec 코드에 넣는 방법

생성된 TXT 파일을 기존 Word2Vec 코드의 `--input` 옵션에 넣습니다.

```powershell
python word2vec_korean.py --input MZ_word2vec_input.txt --words MZ 청년 소비 --top-n 20
```

모델까지 저장하려면 `--save`를 사용합니다.

```powershell
python word2vec_korean.py --input MZ_word2vec_input.txt --words MZ 청년 소비 --save mz_news.model
```

## 참고

- 이 도구는 네이버 공식 API가 아니라 웹 검색 결과 페이지를 이용하는 크롤러입니다.
- 검색 결과 페이지 구조가 바뀌면 링크 수집 선택자를 수정해야 할 수 있습니다.
- 언론사 원문 사이트는 사이트마다 HTML 구조가 달라 본문 추출 실패율이 높을 수 있습니다.
- Word2Vec 학습 목적이라면 본문 전체보다 제목+본문 200자 정도로 시작하는 것이 처리 속도와 품질의 균형이 좋습니다.
- 데이터 양을 안정적으로 확보해야 한다면 네이버 API로 제목/요약/링크를 먼저 수집하고, 크롤러는 본문 보완용으로 사용하는 방식이 더 적합합니다.
