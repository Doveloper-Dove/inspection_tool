"""
다중 AI 뉴스 댓글 담론 분류기 GUI
=================================

지원 제공업체
- Google Gemini
- Groq
- OpenRouter
- OpenAI
- Ollama(로컬)

보안 원칙
- API 키는 GUI 입력칸에서만 받습니다.
- API 키를 파일, 환경변수, 설정 파일에 저장하지 않습니다.
- API 키는 결과 CSV와 로그에 기록하지 않습니다.
- 프로그램을 종료하면 입력한 키도 함께 사라집니다.

입력 CSV 구조
- url
- source_type
- title
- body
- comment_1, comment_2, ... comment_50

공통 분석 규칙
- 기사마다 comment_열에 들어 있는 모든 댓글을 선택합니다.
- 같은 기사에 속한 모든 댓글을 AI 요청 1회에 묶습니다.
- 프로그램 실행 1회당 실제 AI 요청은 최대 5회입니다.
- 실패 요청과 재시도도 요청 횟수에 포함됩니다.
- 댓글 중복 검사는 하지 않습니다.
- 기사 제목은 보조 맥락이고, 실제 분석 대상은 댓글입니다.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import queue
import re
import threading
import time
import traceback
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk


# =============================================================================
# ★★★ 중요 설정 1: 기사당 댓글 개수 제한 해제 ★★★
# =============================================================================
# comment_1, comment_2, ... 형식의 모든 열을 숫자 순서대로 확인합니다.
# 빈 댓글만 제외하고, 내용이 있는 댓글은 개수 제한 없이 전부 분석합니다.
#
# 예:
# comment_1부터 comment_50까지 모두 내용이 있으면 50개 전체를 사용합니다.
COMMENTS_PER_ARTICLE_LIMIT = None
# =============================================================================
# ★★★ 기사당 댓글 제한 없음 ★★★
# =============================================================================


# =============================================================================
# ★★★ 중요 설정 2: 프로그램 실행 1회당 실제 AI 요청 최대 횟수 ★★★
# =============================================================================
# 현재는 반복 테스트를 위해 5회로 고정합니다.
#
# 같은 기사에 속한 모든 댓글을 요청 1회에 묶으므로:
#   요청 1회 = 기사 1개 = 해당 기사에 있는 모든 비어 있지 않은 댓글
#   요청 5회 = 기사 최대 5개
#
# 분석 댓글 수는 각 기사에 실제로 들어 있는 댓글 수에 따라 달라집니다.
#
# 성공하지 못한 요청과 재시도 요청도 각각 1회로 계산됩니다.
# 이 숫자를 바꾸지 않는 한 실행 한 번에 5회를 절대 초과하지 않습니다.
MAX_AI_CALLS_PER_RUN = 5
# =============================================================================
# ★★★ 실행당 AI 요청 제한 설정 끝 ★★★
# =============================================================================


DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_DELAY_SECONDS = 2.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_COMMENT_CHARS = 4_000
DEFAULT_BODY_CONTEXT_CHARS = 0

VALID_LABELS = {
    "일반화",
    "개인의 선택 문제(도덕적 비난)",
    "타자화",
    "결핍/일탈",
    "중립",
}

RESULT_COLUMNS = [
    "AI_담론존재",
    "AI_담론분류",
    "AI_분류이유",
    "AI_근거표현",
    "AI_확신도",
    "AI_분석상태",
    "AI_제공업체",
    "AI_모델",
    "AI_API호출번호",
]

BASE_COLUMNS = ["url", "source_type", "title", "body"]


@dataclass(frozen=True)
class ProviderInfo:
    code: str
    display_name: str
    default_model: str
    endpoint: str
    key_required: bool
    key_hint: str


PROVIDERS: dict[str, ProviderInfo] = {
    "Gemini": ProviderInfo(
        code="gemini",
        display_name="Google Gemini",
        default_model="gemini-3.1-flash-lite",
        endpoint="https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        key_required=True,
        key_hint="Google AI Studio에서 발급한 Gemini API 키",
    ),
    "Groq": ProviderInfo(
        code="groq",
        display_name="Groq",
        default_model="openai/gpt-oss-20b",
        endpoint="https://api.groq.com/openai/v1/chat/completions",
        key_required=True,
        key_hint="Groq Console에서 발급한 API 키",
    ),
    "OpenRouter": ProviderInfo(
        code="openrouter",
        display_name="OpenRouter",
        default_model="openai/gpt-oss-20b:free",
        endpoint="https://openrouter.ai/api/v1/chat/completions",
        key_required=True,
        key_hint="OpenRouter에서 발급한 API 키",
    ),
    "OpenAI": ProviderInfo(
        code="openai",
        display_name="OpenAI",
        default_model="gpt-5.6-luna",
        endpoint="https://api.openai.com/v1/chat/completions",
        key_required=True,
        key_hint="OpenAI Platform에서 발급한 API 키",
    ),
    "Ollama": ProviderInfo(
        code="ollama",
        display_name="Ollama(로컬)",
        default_model="deepseek-v4-flash:cloud",
        endpoint="http://localhost:11434/api/chat",
        key_required=False,
        key_hint="Ollama는 API 키가 필요하지 않습니다.",
    ),
}


BATCH_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "comment_id": {
                        "type": "string",
                        "description": "입력 댓글 ID를 그대로 반환",
                    },
                    "label": {
                        "type": "string",
                        "enum": [
                            "일반화",
                            "개인의 선택 문제(도덕적 비난)",
                            "타자화",
                            "결핍/일탈",
                            "중립",
                        ],
                    },
                    "reason": {
                        "type": "string",
                        "description": "댓글 자체의 표현을 근거로 한 분류 이유",
                    },
                    "evidence": {
                        "type": "string",
                        "description": "댓글 속 실제 근거 표현",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                },
                "required": [
                    "comment_id",
                    "label",
                    "reason",
                    "evidence",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


SYSTEM_INSTRUCTION = """
당신은 한국 뉴스 댓글의 세대 담론을 분석하는 연구용 분류기다.

가장 중요한 원칙:
- 실제 분석 대상은 각 댓글이다.
- 기사 제목과 기사 본문 일부는 댓글의 대상을 이해하기 위한 보조 자료일 뿐이다.
- 기사 제목이나 본문의 표현을 댓글의 표현으로 간주하지 않는다.
- 댓글마다 서로 독립적으로 판단한다.
- 같은 기사에 달린 다른 댓글의 판단을 현재 댓글에 전가하지 않는다.
- 댓글이 기사, 정부, 기업, 정치인, 기자를 비판하더라도
  청년·MZ세대에 대한 아래 담론이 없다면 중립이다.
- 욕설이나 공격성이 있다는 사실만으로 세대 담론으로 판단하지 않는다.
- 짧거나 의미가 불명확하고 명확한 근거가 없으면 중립이다.
- 여러 담론이 동시에 보이면 가장 중심적인 유형 하나만 선택한다.

라벨 기준:

1. 일반화
- 일부 사람이나 제한된 사례를 청년, MZ세대, 2030세대 전체의 특성으로 확대한다.
- 단순히 MZ세대를 언급하는 것만으로는 일반화가 아니다.

2. 개인의 선택 문제(도덕적 비난)
- 주거, 취업, 결혼, 출산, 소비, 노동 문제를 구조적 조건보다
  개인의 노력, 인내심, 책임감, 소비 습관, 눈높이 또는 도덕성 부족 탓으로 돌린다.

3. 타자화
- 청년이나 MZ세대를 이해하기 어렵고 낯설며 이질적인 집단으로 묘사한다.
- 별종, 신인류, 다른 종족처럼 거리감을 만드는 표현을 포함한다.

4. 결핍/일탈
- 청년이나 MZ세대를 기성세대 또는 사회적 정상 기준과 비교하여
  책임감, 예의, 근성, 충성심, 사회성 등이 부족하거나 비정상이라고 평가한다.

5. 중립
- 위 네 담론이 댓글 자체에서 확인되지 않는다.
- 기사·정책·기업·기관·정치인 비판, 개인 경험, 질문, 사실 전달은
  청년 집단에 대한 위 담론이 없다면 중립이다.

충돌 시:
- 사회 문제의 책임을 청년 개인의 태도와 노력에 돌리면 개인의 선택 문제.
- 부족함, 비정상성, 과거 대비 퇴행이 핵심이면 결핍/일탈.
- 낯섦과 이질성이 핵심이면 타자화.
- 위 의미 없이 집단 전체를 단정하는 방식이 핵심이면 일반화.
- 근거가 부족하면 중립.

출력 규칙:
- 입력된 모든 comment_id에 대해 결과를 정확히 하나씩 반환한다.
- comment_id는 입력값을 변경하지 않고 그대로 반환한다.
- 입력에 없는 comment_id를 만들지 않는다.
- evidence에는 해당 댓글 속 표현만 사용한다.
- 지정된 JSON 스키마만 반환한다.
"""


class UserStopRequested(RuntimeError):
    pass


class CallLimitReached(RuntimeError):
    pass


class ProviderHTTPError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code


@dataclass
class RunConfig:
    provider_name: str
    provider: ProviderInfo
    model: str
    api_key: str
    input_path: Path
    output_dir: Path
    resume: bool
    prepare_only: bool
    delay_seconds: float
    max_retries: int
    body_context_chars: int
    max_comment_chars: int
    timeout_seconds: int


@dataclass
class CallBudget:
    maximum: int = MAX_AI_CALLS_PER_RUN
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.maximum - self.used)

    def consume(self) -> int:
        if self.used >= self.maximum:
            raise CallLimitReached(
                f"이번 실행의 AI 요청 제한 {self.maximum}회에 도달했습니다."
            )
        self.used += 1
        return self.used


def safe_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value)


def normalize_whitespace(text: str) -> str:
    text = html.unescape(safe_text(text))
    text = text.replace("\u200b", " ").replace("\ufeff", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_comment(value: object) -> str:
    text = normalize_whitespace(safe_text(value))
    if not text:
        return ""

    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                text = " ".join(
                    normalize_whitespace(str(item))
                    for item in parsed
                    if item is not None and normalize_whitespace(str(item))
                )
        except (json.JSONDecodeError, TypeError):
            pass

    for _ in range(3):
        removed = False
        for left, right in [
            ('"', '"'),
            ("'", "'"),
            ("“", "”"),
            ("‘", "’"),
        ]:
            if len(text) >= 2 and text.startswith(left) and text.endswith(right):
                inner = text[len(left): len(text) - len(right)].strip()
                if left not in inner and right not in inner:
                    text = inner
                    removed = True
                    break
        if not removed:
            break

    text = normalize_whitespace(text)
    if text.lower() in {"nan", "none", "null", "[]", '""', "''"}:
        return ""
    return text


def clean_title(value: object) -> str:
    return normalize_whitespace(re.sub(r"<[^>]+>", " ", safe_text(value)))


def clean_body_context(value: object, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    text = safe_text(value)
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize_whitespace(text)[:max_chars]


def truncate_comment(comment: str, max_chars: int) -> str:
    if len(comment) <= max_chars:
        return comment
    front = int(max_chars * 0.75)
    back = max_chars - front
    return (
        comment[:front]
        + "\n[댓글 중간 일부 생략]\n"
        + comment[-back:]
    )


def make_comment_id(
    url: str,
    article_row: int,
    comment_column: str,
    comment: str,
) -> str:
    # 중복 검사 목적이 아니라 중단 후 이어서 실행할 때 사용하는 ID입니다.
    raw = f"{url}\n{article_row}\n{comment_column}\n{comment}"
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:24]


def read_csv_with_fallback(path: Path) -> tuple[pd.DataFrame, str]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return pd.read_csv(path, encoding=encoding), encoding
        except UnicodeDecodeError as error:
            last_error = error
    if last_error:
        raise last_error
    raise RuntimeError("CSV 파일을 읽을 수 없습니다.")


def get_comment_columns(df: pd.DataFrame) -> list[str]:
    matched: list[tuple[int, str]] = []
    for column in df.columns:
        match = re.fullmatch(r"comment_(\d+)", str(column))
        if match:
            matched.append((int(match.group(1)), str(column)))
    matched.sort(key=lambda item: item[0])
    return [column for _, column in matched]


def validate_input_dataframe(df: pd.DataFrame) -> None:
    missing = [column for column in BASE_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(
            "CSV에 필요한 기본 열이 없습니다: "
            + ", ".join(missing)
            + "\n필수 열: "
            + ", ".join(BASE_COLUMNS)
        )
    if not get_comment_columns(df):
        raise ValueError(
            "comment_1, comment_2 형식의 댓글 열을 찾지 못했습니다."
        )


def build_long_comment_dataframe(source_df: pd.DataFrame) -> pd.DataFrame:
    comment_columns = get_comment_columns(source_df)
    rows: list[dict[str, object]] = []

    for article_index, article in source_df.iterrows():
        article_row = int(article_index) + 2
        url = safe_text(article.get("url", "")).strip()
        source_type = safe_text(article.get("source_type", "")).strip()
        title = clean_title(article.get("title", ""))
        body = safe_text(article.get("body", ""))

        # =====================================================================
        # ★★★ 기사당 댓글 개수 제한 없음 ★★★
        # =====================================================================
        # comment_숫자 열을 모두 확인하고 빈 댓글만 건너뜁니다.
        # 내용이 있는 댓글은 개수와 관계없이 전부 결과에 포함합니다.
        for comment_column in comment_columns:
            comment = clean_comment(article.get(comment_column, ""))
            if not comment:
                continue
        # =====================================================================
        # ★★★ 기사당 모든 댓글 선택 구간 ★★★
        # =====================================================================

            match = re.fullmatch(r"comment_(\d+)", comment_column)
            comment_number = int(match.group(1)) if match else 0

            rows.append(
                {
                    "댓글ID": make_comment_id(
                        url,
                        article_row,
                        comment_column,
                        comment,
                    ),
                    "기사원본행": article_row,
                    "url": url,
                    "source_type": source_type,
                    "title": title,
                    "body": body,
                    "댓글번호": comment_number,
                    "댓글원본열": comment_column,
                    "comment": comment,
                    "AI_담론존재": "",
                    "AI_담론분류": "",
                    "AI_분류이유": "",
                    "AI_근거표현": "",
                    "AI_확신도": "",
                    "AI_분석상태": "",
                    "AI_제공업체": "",
                    "AI_모델": "",
                    "AI_API호출번호": "",
                }
            )

    result = pd.DataFrame(rows, dtype="object")
    if result.empty:
        raise ValueError("내용이 있는 댓글을 찾지 못했습니다.")
    return result


def sanitize_filename(text: str) -> str:
    sanitized = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", text).strip("._")
    return sanitized or "model"


def make_output_paths(config: RunConfig) -> tuple[Path, Path]:
    model_part = sanitize_filename(config.model)
    stem = config.input_path.stem
    detail = config.output_dir / (
        f"{stem}_댓글담론분류결과_{config.provider.code}_{model_part}.csv"
    )
    summary = config.output_dir / (
        f"{stem}_댓글담론요약_{config.provider.code}_{model_part}.csv"
    )
    return detail, summary


def save_dataframe(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def make_summary(df: pd.DataFrame) -> pd.DataFrame:
    status = df["AI_분석상태"].fillna("").astype(str)
    completed = df[
        status.str.startswith("성공")
        & df["AI_담론분류"].isin(VALID_LABELS)
    ]

    completed_count = len(completed)
    error_count = int(status.str.startswith("오류").sum())
    total_count = len(df)
    unprocessed_count = max(
        0,
        total_count - completed_count - error_count,
    )

    rows: list[dict[str, object]] = []
    for label in [
        "일반화",
        "개인의 선택 문제(도덕적 비난)",
        "타자화",
        "결핍/일탈",
        "중립",
    ]:
        count = int((completed["AI_담론분류"] == label).sum())
        percentage = (
            round(count / completed_count * 100, 2)
            if completed_count
            else 0.0
        )
        rows.append(
            {
                "구분": label,
                "댓글수": count,
                "분석완료댓글중_비율(%)": percentage,
            }
        )

    rows.extend(
        [
            {
                "구분": "분석 완료 합계",
                "댓글수": completed_count,
                "분석완료댓글중_비율(%)": 100.0 if completed_count else 0.0,
            },
            {
                "구분": "오류",
                "댓글수": error_count,
                "분석완료댓글중_비율(%)": "",
            },
            {
                "구분": "미분석",
                "댓글수": unprocessed_count,
                "분석완료댓글중_비율(%)": "",
            },
            {
                "구분": "전체 댓글",
                "댓글수": total_count,
                "분석완료댓글중_비율(%)": "",
            },
        ]
    )
    return pd.DataFrame(rows, dtype="object")


def save_results(
    comments_df: pd.DataFrame,
    detail_path: Path,
    summary_path: Path,
) -> None:
    export_df = comments_df.drop(columns=["body"], errors="ignore")
    save_dataframe(export_df, detail_path)
    save_dataframe(make_summary(export_df), summary_path)


def row_is_complete(row: pd.Series) -> bool:
    return (
        safe_text(row.get("AI_담론분류", "")).strip() in VALID_LABELS
        and safe_text(row.get("AI_분석상태", "")).startswith("성공")
    )


def load_previous_results(
    current_df: pd.DataFrame,
    detail_path: Path,
    config: RunConfig,
) -> tuple[pd.DataFrame, int]:
    if not config.resume or not detail_path.exists():
        return current_df, 0

    previous_df, _ = read_csv_with_fallback(detail_path)
    if "댓글ID" not in previous_df.columns:
        return current_df, 0

    previous_df = previous_df.drop_duplicates(
        subset=["댓글ID"],
        keep="last",
    ).set_index("댓글ID")

    copied = 0
    for index, row in current_df.iterrows():
        comment_id = safe_text(row["댓글ID"])
        if comment_id not in previous_df.index:
            continue

        old = previous_df.loc[comment_id]
        if safe_text(old.get("comment", "")) != safe_text(row["comment"]):
            continue

        old_provider = safe_text(old.get("AI_제공업체", ""))
        old_model = safe_text(old.get("AI_모델", ""))
        if old_provider and old_provider != config.provider.code:
            continue
        if old_model and old_model != config.model:
            continue

        for column in RESULT_COLUMNS:
            if column in previous_df.columns:
                current_df.at[index, column] = old.get(column, "")
        copied += 1

    return current_df, copied


def make_article_batches(comments_df: pd.DataFrame) -> list[list[int]]:
    groups: OrderedDict[int, list[int]] = OrderedDict()

    for index, row in comments_df.iterrows():
        if row_is_complete(row):
            continue
        article_row = int(row["기사원본행"])
        groups.setdefault(article_row, []).append(index)

    return list(groups.values())


def build_batch_prompt(
    batch_rows: pd.DataFrame,
    body_context_chars: int,
    max_comment_chars: int,
) -> tuple[str, set[str]]:
    first = batch_rows.iloc[0]
    title = clean_title(first["title"])
    body_context = clean_body_context(
        first.get("body", ""),
        body_context_chars,
    )

    blocks: list[str] = []
    expected_ids: set[str] = set()

    for _, row in batch_rows.iterrows():
        comment_id = safe_text(row["댓글ID"])
        expected_ids.add(comment_id)
        comment = truncate_comment(
            clean_comment(row["comment"]),
            max_comment_chars,
        )
        blocks.append(
            f"[댓글 ID: {comment_id}]\n"
            f"[댓글 번호: {row['댓글번호']}]\n"
            f"{comment}"
        )

    body_section = ""
    if body_context:
        body_section = (
            "\n[기사 본문 일부 — 보조 맥락이며 분석 대상 아님]\n"
            + body_context
            + "\n"
        )

    prompt = (
        f"아래는 기사 한 건에 달린 댓글 {len(blocks)}개다.\n"
        "각 댓글을 서로 독립적으로 분석하여 댓글마다 결과 하나를 반환하라.\n\n"
        "[기사 제목 — 보조 맥락이며 분석 대상 아님]\n"
        f"{title or '제목 없음'}\n"
        f"{body_section}\n"
        "[분석 대상 댓글 목록]\n\n"
        + "\n\n".join(blocks)
        + "\n\n필수 조건:\n"
        "- 각 댓글 ID에 대한 결과를 정확히 하나씩 반환한다.\n"
        "- 기사 제목이나 다른 댓글의 표현을 해당 댓글에 전가하지 않는다.\n"
        "- results의 comment_id는 입력된 댓글 ID와 정확히 같아야 한다.\n"
        "- JSON 이외의 설명문이나 마크다운 코드 블록을 출력하지 않는다."
    )
    return prompt, expected_ids


def extract_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:1_500] or response.reason

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return safe_text(error.get("message") or error)[:1_500]
        if error:
            return safe_text(error)[:1_500]
        if payload.get("message"):
            return safe_text(payload["message"])[:1_500]
    return safe_text(payload)[:1_500]


def post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=timeout_seconds,
    )
    if response.status_code >= 400:
        raise ProviderHTTPError(
            response.status_code,
            extract_error_message(response),
        )
    try:
        result = response.json()
    except ValueError as error:
        raise RuntimeError(
            "AI 서버가 JSON 형식이 아닌 응답을 반환했습니다."
        ) from error
    if not isinstance(result, dict):
        raise RuntimeError("AI 서버 응답 구조가 올바르지 않습니다.")
    return result


def request_gemini(
    config: RunConfig,
    prompt: str,
) -> str:
    model_encoded = quote(config.model, safe="")
    url = config.provider.endpoint.format(model=model_encoded)

    payload = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_INSTRUCTION}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0,
            "responseFormat": {
                "text": {
                    "mimeType": "application/json",
                    "schema": BATCH_JSON_SCHEMA,
                }
            },
        },
    }

    result = post_json(
        url,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": config.api_key,
        },
        payload=payload,
        timeout_seconds=config.timeout_seconds,
    )

    try:
        parts = result["candidates"][0]["content"]["parts"]
        texts = [
            safe_text(part.get("text", ""))
            for part in parts
            if isinstance(part, dict) and part.get("text")
        ]
        content = "".join(texts).strip()
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(
            "Gemini 응답에서 결과 텍스트를 찾지 못했습니다."
        ) from error

    if not content:
        raise RuntimeError("Gemini가 빈 응답을 반환했습니다.")
    return content


def request_openai_compatible(
    config: RunConfig,
    prompt: str,
) -> str:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.api_key}",
    }

    if config.provider.code == "openrouter":
        headers.update(
            {
                "HTTP-Referer": "http://localhost",
                "X-Title": "AI Comment Discourse Classifier",
            }
        )

    payload: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_INSTRUCTION,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "batch_discourse_response",
                "strict": True,
                "schema": BATCH_JSON_SCHEMA,
            },
        },
        "stream": False,
    }

    # OpenRouter에서는 구조화 출력을 지원하는 제공업체만 선택하도록 요청합니다.
    if config.provider.code == "openrouter":
        payload["provider"] = {
            "require_parameters": True
        }

    result = post_json(
        config.provider.endpoint,
        headers=headers,
        payload=payload,
        timeout_seconds=config.timeout_seconds,
    )

    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(
            "AI 응답에서 choices[0].message.content를 찾지 못했습니다."
        ) from error

    # 일부 API는 content를 문자열이 아닌 조각 배열로 반환할 수 있습니다.
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    pieces.append(text)
        content = "".join(pieces)

    content = safe_text(content).strip()
    if not content:
        raise RuntimeError("AI가 빈 응답을 반환했습니다.")
    return content


def request_ollama(
    config: RunConfig,
    prompt: str,
) -> str:
    payload = {
        "model": config.model,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_INSTRUCTION,
            },
            {
                "role": "user",
                "content": (
                    prompt
                    + "\n\n반환할 JSON Schema:\n"
                    + json.dumps(
                        BATCH_JSON_SCHEMA,
                        ensure_ascii=False,
                    )
                ),
            },
        ],
        "format": BATCH_JSON_SCHEMA,
        "stream": False,
        "options": {
            "temperature": 0,
        },
    }

    result = post_json(
        config.provider.endpoint,
        headers={"Content-Type": "application/json"},
        payload=payload,
        timeout_seconds=config.timeout_seconds,
    )

    try:
        content = result["message"]["content"]
    except (KeyError, TypeError) as error:
        raise RuntimeError(
            "Ollama 응답에서 message.content를 찾지 못했습니다."
        ) from error

    content = safe_text(content).strip()
    if not content:
        raise RuntimeError("Ollama가 빈 응답을 반환했습니다.")
    return content


def request_provider(
    config: RunConfig,
    prompt: str,
) -> str:
    if config.provider.code == "gemini":
        return request_gemini(config, prompt)

    if config.provider.code in {
        "groq",
        "openrouter",
        "openai",
    }:
        return request_openai_compatible(config, prompt)

    if config.provider.code == "ollama":
        return request_ollama(config, prompt)

    raise RuntimeError(
        f"지원하지 않는 AI 제공업체입니다: {config.provider.code}"
    )


def strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def validate_ai_result(
    raw_text: str,
    expected_ids: set[str],
) -> dict[str, dict[str, Any]]:
    raw_text = strip_json_fence(raw_text)

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"AI 응답 JSON 해석 실패: {error}"
        ) from error

    if not isinstance(payload, dict):
        raise ValueError("AI 응답의 최상위 값은 객체여야 합니다.")

    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("AI 응답에 results 배열이 없습니다.")

    result_by_id: dict[str, dict[str, Any]] = {}

    for item in results:
        if not isinstance(item, dict):
            raise ValueError("results 내부 항목이 객체가 아닙니다.")

        comment_id = safe_text(item.get("comment_id", "")).strip()
        label = safe_text(item.get("label", "")).strip()
        reason = safe_text(item.get("reason", "")).strip()
        evidence = safe_text(item.get("evidence", "")).strip()
        confidence = item.get("confidence")

        if not comment_id:
            raise ValueError("comment_id가 비어 있습니다.")
        if comment_id in result_by_id:
            raise ValueError(f"중복 comment_id 응답: {comment_id}")
        if label not in VALID_LABELS:
            raise ValueError(f"허용되지 않은 라벨: {label}")
        if not reason:
            raise ValueError(f"{comment_id}의 reason이 비어 있습니다.")
        if not evidence:
            raise ValueError(f"{comment_id}의 evidence가 비어 있습니다.")

        try:
            confidence_float = float(confidence)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"{comment_id}의 confidence가 숫자가 아닙니다."
            ) from error

        if not 0 <= confidence_float <= 1:
            raise ValueError(
                f"{comment_id}의 confidence가 0~1 범위를 벗어났습니다."
            )

        result_by_id[comment_id] = {
            "label": label,
            "reason": reason,
            "evidence": evidence,
            "confidence": confidence_float,
        }

    returned_ids = set(result_by_id)
    missing = expected_ids - returned_ids
    extra = returned_ids - expected_ids

    if missing or extra:
        raise ValueError(
            "AI 응답 댓글 ID 불일치: "
            f"누락={sorted(missing)}, 추가={sorted(extra)}"
        )

    return result_by_id


def is_quota_error(error: Exception) -> bool:
    text = str(error).lower()
    if isinstance(error, ProviderHTTPError) and error.status_code == 429:
        return True
    return any(
        token in text
        for token in (
            "quota",
            "resource_exhausted",
            "rate limit",
            "too many requests",
            "requests per day",
            "requests per minute",
            "insufficient_quota",
        )
    )


def is_non_retryable_error(error: Exception) -> bool:
    if isinstance(error, ProviderHTTPError):
        return error.status_code in {
            400,
            401,
            402,
            403,
            404,
            405,
            422,
        }
    return False


def call_batch_with_budget(
    config: RunConfig,
    batch_rows: pd.DataFrame,
    budget: CallBudget,
    stop_event: threading.Event,
    emit,
) -> tuple[dict[str, dict[str, Any]], int]:
    prompt, expected_ids = build_batch_prompt(
        batch_rows,
        config.body_context_chars,
        config.max_comment_chars,
    )

    last_error: Exception | None = None

    for attempt in range(1, config.max_retries + 1):
        if stop_event.is_set():
            raise UserStopRequested("사용자가 중지를 요청했습니다.")

        # =====================================================================
        # ★★★ 실제 네트워크 요청 직전에 사용 횟수 1회 증가 ★★★
        # =====================================================================
        # requests를 직접 사용하므로 SDK 내부의 숨은 자동 재시도가 없습니다.
        # 성공·실패·재시도를 포함해 정확히 최대 5회까지만 요청합니다.
        call_number = budget.consume()
        # =====================================================================
        # ★★★ AI 요청 횟수 계산 구간 끝 ★★★
        # =====================================================================

        emit(
            "calls",
            used=budget.used,
            maximum=budget.maximum,
        )
        emit(
            "log",
            text=(
                f"실제 AI 요청 {call_number}/{budget.maximum}회 "
                f"(남은 횟수 {budget.remaining}회)"
            ),
        )

        try:
            raw_text = request_provider(config, prompt)
            result_by_id = validate_ai_result(
                raw_text,
                expected_ids,
            )
            return result_by_id, call_number

        except Exception as error:
            last_error = error

        if is_quota_error(last_error):
            raise RuntimeError(
                "AI 서비스의 할당량 또는 호출 제한에 도달했습니다.\n"
                f"원본 오류: {last_error}"
            )

        if is_non_retryable_error(last_error):
            raise RuntimeError(
                "설정, API 키, 모델 또는 요청 형식을 확인해야 합니다.\n"
                f"원본 오류: {last_error}"
            )

        if attempt >= config.max_retries:
            break

        if budget.remaining <= 0:
            raise CallLimitReached(
                "재시도가 필요하지만 이번 실행의 요청 제한 "
                f"{budget.maximum}회를 모두 사용했습니다."
            )

        wait_seconds = min(15, 2 * attempt)
        emit(
            "log",
            text=(
                f"응답 오류: {last_error}\n"
                f"{wait_seconds}초 후 재시도합니다. "
                "재시도도 요청 1회로 계산됩니다."
            ),
        )

        for _ in range(wait_seconds * 10):
            if stop_event.is_set():
                raise UserStopRequested("사용자가 중지를 요청했습니다.")
            time.sleep(0.1)

    raise RuntimeError(
        f"묶음 분석이 {config.max_retries}회 실패했습니다: {last_error}"
    )


def run_analysis(
    config: RunConfig,
    event_queue: queue.Queue,
    stop_event: threading.Event,
) -> None:
    def emit(event_type: str, **payload: Any) -> None:
        event_queue.put((event_type, payload))

    detail_path, summary_path = make_output_paths(config)

    try:
        emit("status", text="CSV 파일을 읽는 중...")
        source_df, encoding = read_csv_with_fallback(config.input_path)
        validate_input_dataframe(source_df)

        emit("status", text="기사별 모든 댓글을 정리하는 중...")
        comments_df = build_long_comment_dataframe(source_df)

        copied = 0
        if config.resume:
            comments_df, copied = load_previous_results(
                comments_df,
                detail_path,
                config,
            )

        batches = make_article_batches(comments_df)

        emit(
            "log",
            text=(
                "=" * 66
                + "\n다중 AI 댓글 담론 분류기\n"
                + "=" * 66
                + f"\nAI: {config.provider.display_name}"
                + f"\n모델: {config.model}"
                + f"\n입력: {config.input_path}"
                + f"\n입력 인코딩: {encoding}"
                + f"\n기사 행: {len(source_df):,}개"
                + f"\n분석 대상 댓글: {len(comments_df):,}개"
                + "\n기사당 댓글 제한: 없음"
                + f"\n실행당 AI 요청 제한: {MAX_AI_CALLS_PER_RUN}회"
                + f"\n이어받은 댓글 결과: {copied:,}개"
                + f"\n미분석 기사 묶음: {len(batches):,}개"
                + "\nAPI 키 저장: 하지 않음"
            ),
        )

        emit(
            "paths",
            detail=str(detail_path),
            summary=str(summary_path),
        )

        if config.prepare_only:
            save_results(comments_df, detail_path, summary_path)
            emit(
                "done",
                message=(
                    "API를 사용하지 않고 댓글 세로형 결과 구조만 저장했습니다."
                ),
                detail=str(detail_path),
                summary=str(summary_path),
            )
            return

        if not batches:
            save_results(comments_df, detail_path, summary_path)
            emit(
                "done",
                message="이미 모든 댓글 분석이 완료되어 있습니다.",
                detail=str(detail_path),
                summary=str(summary_path),
            )
            return

        budget = CallBudget()
        success_batches = 0
        success_comments = 0
        failed_batches = 0
        total_run_batches = min(
            len(batches),
            MAX_AI_CALLS_PER_RUN,
        )

        emit(
            "progress_max",
            maximum=total_run_batches,
        )

        for batch_position, indices in enumerate(
            batches,
            start=1,
        ):
            if stop_event.is_set():
                raise UserStopRequested("사용자가 중지를 요청했습니다.")

            if budget.remaining <= 0:
                break

            batch_rows = comments_df.loc[indices]
            first = batch_rows.iloc[0]

            emit(
                "status",
                text=(
                    f"기사 묶음 {batch_position} 분석 중 "
                    f"({len(indices)}개 댓글)"
                ),
            )
            emit(
                "log",
                text=(
                    f"\n[기사 묶음 {batch_position}] "
                    f"CSV 원본 {first['기사원본행']}행\n"
                    f"제목: {safe_text(first['title'])[:120]}\n"
                    f"묶음 댓글 수: {len(indices)}개"
                ),
            )

            try:
                result_by_id, call_number = call_batch_with_budget(
                    config,
                    batch_rows,
                    budget,
                    stop_event,
                    emit,
                )

                for index in indices:
                    comment_id = safe_text(comments_df.at[index, "댓글ID"])
                    result = result_by_id[comment_id]
                    label = result["label"]

                    comments_df.at[index, "AI_담론존재"] = (
                        "없음" if label == "중립" else "있음"
                    )
                    comments_df.at[index, "AI_담론분류"] = label
                    comments_df.at[index, "AI_분류이유"] = result["reason"]
                    comments_df.at[index, "AI_근거표현"] = result["evidence"]
                    comments_df.at[index, "AI_확신도"] = round(
                        result["confidence"],
                        4,
                    )
                    comments_df.at[index, "AI_분석상태"] = "성공(기사별 묶음)"
                    comments_df.at[index, "AI_제공업체"] = config.provider.code
                    comments_df.at[index, "AI_모델"] = config.model
                    comments_df.at[index, "AI_API호출번호"] = call_number

                    emit(
                        "log",
                        text=(
                            f"댓글 {comments_df.at[index, '댓글번호']}: "
                            f"{label} / 확신도 "
                            f"{result['confidence']:.2f}"
                        ),
                    )

                success_batches += 1
                success_comments += len(indices)

            except CallLimitReached as error:
                emit("log", text=f"\n요청 제한: {error}")
                break

            except UserStopRequested:
                raise

            except Exception as error:
                failed_batches += 1

                for index in indices:
                    comments_df.at[index, "AI_분석상태"] = (
                        f"오류: {type(error).__name__}: {error}"
                    )
                    comments_df.at[index, "AI_제공업체"] = config.provider.code
                    comments_df.at[index, "AI_모델"] = config.model

                emit(
                    "log",
                    text=f"묶음 분석 오류: {error}",
                )

                # 인증·모델·할당량 문제는 같은 실행에서 계속 요청하지 않습니다.
                if (
                    "API 키" in str(error)
                    or "할당량" in str(error)
                    or "HTTP 401" in str(error)
                    or "HTTP 403" in str(error)
                    or "HTTP 404" in str(error)
                ):
                    save_results(comments_df, detail_path, summary_path)
                    raise

            save_results(comments_df, detail_path, summary_path)

            emit(
                "progress",
                value=min(
                    success_batches + failed_batches,
                    total_run_batches,
                ),
            )

            if budget.remaining <= 0:
                break

            for _ in range(int(config.delay_seconds * 10)):
                if stop_event.is_set():
                    raise UserStopRequested("사용자가 중지를 요청했습니다.")
                time.sleep(0.1)

        save_results(comments_df, detail_path, summary_path)

        emit(
            "done",
            message=(
                f"실행이 끝났습니다.\n\n"
                f"실제 AI 요청: {budget.used}/{budget.maximum}회\n"
                f"성공 기사 묶음: {success_batches}개\n"
                f"성공 댓글: {success_comments}개\n"
                f"실패 기사 묶음: {failed_batches}개\n\n"
                "다음 묶음을 분석하려면 '기존 결과 이어서'를 체크한 채 "
                "다시 시작하십시오."
            ),
            detail=str(detail_path),
            summary=str(summary_path),
        )

    except UserStopRequested:
        try:
            if "comments_df" in locals() and "detail_path" in locals():
                save_results(comments_df, detail_path, summary_path)
        finally:
            emit(
                "stopped",
                message="중지 요청을 확인했습니다. 현재까지의 결과를 저장했습니다.",
            )

    except Exception as error:
        error_text = (
            f"{type(error).__name__}: {error}\n\n"
            + traceback.format_exc()
        )
        emit("error", message=error_text)


class DiscourseClassifierGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title("다중 AI 뉴스 댓글 담론 분류기")
        self.geometry("980x760")
        self.minsize(880, 680)

        self.event_queue: queue.Queue = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.stop_event = threading.Event()

        self.provider_var = tk.StringVar(value="Gemini")
        self.model_var = tk.StringVar(value=PROVIDERS["Gemini"].default_model)
        self.api_key_var = tk.StringVar()
        self.show_key_var = tk.BooleanVar(value=False)
        self.input_path_var = tk.StringVar()
        self.output_dir_var = tk.StringVar()
        self.resume_var = tk.BooleanVar(value=True)
        self.prepare_only_var = tk.BooleanVar(value=False)
        self.delay_var = tk.StringVar(value=str(DEFAULT_DELAY_SECONDS))
        self.retries_var = tk.StringVar(value=str(DEFAULT_MAX_RETRIES))
        self.body_context_var = tk.StringVar(
            value=str(DEFAULT_BODY_CONTEXT_CHARS)
        )
        self.max_comment_chars_var = tk.StringVar(
            value=str(DEFAULT_MAX_COMMENT_CHARS)
        )
        self.timeout_var = tk.StringVar(
            value=str(DEFAULT_TIMEOUT_SECONDS)
        )
        self.status_var = tk.StringVar(value="대기 중")
        self.calls_var = tk.StringVar(
            value=f"AI 요청 사용: 0/{MAX_AI_CALLS_PER_RUN}회"
        )
        self.output_paths_var = tk.StringVar(value="")

        self._build_style()
        self._build_ui()
        self._on_provider_change()
        self.after(100, self._process_events)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        style.configure("Title.TLabel", font=("맑은 고딕", 17, "bold"))
        style.configure("Section.TLabelframe.Label", font=("맑은 고딕", 10, "bold"))
        style.configure("Warning.TLabel", font=("맑은 고딕", 10, "bold"), foreground="#b00020")
        style.configure("Muted.TLabel", foreground="#555555")
        style.configure("Primary.TButton", font=("맑은 고딕", 10, "bold"))

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=14)
        container.pack(fill="both", expand=True)

        ttk.Label(
            container,
            text="다중 AI 뉴스 댓글 담론 분류기",
            style="Title.TLabel",
        ).pack(anchor="w")

        ttk.Label(
            container,
            text=(
                "기사별 모든 댓글을 요청 1회에 묶으며, "
                "실행 한 번당 실제 AI 요청은 최대 5회입니다. "
                "댓글이 많은 기사는 응답 생성 시간이 길어질 수 있습니다."
            ),
            style="Warning.TLabel",
        ).pack(anchor="w", pady=(4, 12))

        settings = ttk.LabelFrame(
            container,
            text="AI 및 파일 설정",
            style="Section.TLabelframe",
            padding=12,
        )
        settings.pack(fill="x")

        settings.columnconfigure(1, weight=1)

        ttk.Label(settings, text="AI 제공업체").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=5
        )
        self.provider_combo = ttk.Combobox(
            settings,
            textvariable=self.provider_var,
            values=list(PROVIDERS.keys()),
            state="readonly",
            width=18,
        )
        self.provider_combo.grid(
            row=0, column=1, sticky="w", pady=5
        )
        self.provider_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._on_provider_change(),
        )

        ttk.Label(settings, text="모델 이름").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=5
        )
        self.model_entry = ttk.Entry(
            settings,
            textvariable=self.model_var,
        )
        self.model_entry.grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=5
        )
        self.model_entry.bind(
            "<KeyRelease>",
            lambda _event: self._update_output_preview(),
        )

        ttk.Label(settings, text="API 키").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=5
        )
        self.api_key_entry = ttk.Entry(
            settings,
            textvariable=self.api_key_var,
            show="●",
        )
        self.api_key_entry.grid(
            row=2, column=1, sticky="ew", pady=5
        )
        self.show_key_check = ttk.Checkbutton(
            settings,
            text="키 표시",
            variable=self.show_key_var,
            command=self._toggle_key_visibility,
        )
        self.show_key_check.grid(
            row=2, column=2, sticky="w", padx=(8, 0), pady=5
        )

        self.key_hint_label = ttk.Label(
            settings,
            text="",
            style="Muted.TLabel",
        )
        self.key_hint_label.grid(
            row=3, column=1, columnspan=2, sticky="w", pady=(0, 6)
        )

        ttk.Label(
            settings,
            text="API 키는 저장되지 않으며 실행 중 메모리에서만 사용됩니다.",
            style="Warning.TLabel",
        ).grid(
            row=4, column=1, columnspan=2, sticky="w", pady=(0, 8)
        )

        ttk.Label(settings, text="입력 CSV").grid(
            row=5, column=0, sticky="w", padx=(0, 8), pady=5
        )
        ttk.Entry(
            settings,
            textvariable=self.input_path_var,
        ).grid(
            row=5, column=1, sticky="ew", pady=5
        )
        ttk.Button(
            settings,
            text="파일 선택",
            command=self._choose_input_file,
        ).grid(
            row=5, column=2, sticky="ew", padx=(8, 0), pady=5
        )

        ttk.Label(settings, text="결과 저장 폴더").grid(
            row=6, column=0, sticky="w", padx=(0, 8), pady=5
        )
        ttk.Entry(
            settings,
            textvariable=self.output_dir_var,
        ).grid(
            row=6, column=1, sticky="ew", pady=5
        )
        ttk.Button(
            settings,
            text="폴더 선택",
            command=self._choose_output_dir,
        ).grid(
            row=6, column=2, sticky="ew", padx=(8, 0), pady=5
        )

        options = ttk.LabelFrame(
            container,
            text="실행 옵션",
            style="Section.TLabelframe",
            padding=12,
        )
        options.pack(fill="x", pady=(10, 0))

        ttk.Checkbutton(
            options,
            text="기존 결과 이어서",
            variable=self.resume_var,
        ).grid(row=0, column=0, sticky="w", padx=(0, 20))

        ttk.Checkbutton(
            options,
            text="API 없이 변환만",
            variable=self.prepare_only_var,
            command=self._update_api_controls,
        ).grid(row=0, column=1, sticky="w", padx=(0, 20))

        ttk.Label(
            options,
            text="기사당 댓글: 제한 없음",
            style="Warning.TLabel",
        ).grid(row=0, column=2, sticky="w", padx=(0, 20))

        ttk.Label(
            options,
            text=f"실행당 요청: 최대 {MAX_AI_CALLS_PER_RUN}회",
            style="Warning.TLabel",
        ).grid(row=0, column=3, sticky="w")

        advanced = ttk.LabelFrame(
            container,
            text="고급 설정",
            style="Section.TLabelframe",
            padding=12,
        )
        advanced.pack(fill="x", pady=(10, 0))

        advanced.columnconfigure(1, weight=1)
        advanced.columnconfigure(3, weight=1)
        advanced.columnconfigure(5, weight=1)

        ttk.Label(advanced, text="요청 사이 대기(초)").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Entry(
            advanced,
            textvariable=self.delay_var,
            width=8,
        ).grid(row=0, column=1, sticky="w", padx=(6, 20))

        ttk.Label(advanced, text="최대 시도 횟수").grid(
            row=0, column=2, sticky="w"
        )
        ttk.Entry(
            advanced,
            textvariable=self.retries_var,
            width=8,
        ).grid(row=0, column=3, sticky="w", padx=(6, 20))

        ttk.Label(advanced, text="요청 시간 제한(초)").grid(
            row=0, column=4, sticky="w"
        )
        ttk.Entry(
            advanced,
            textvariable=self.timeout_var,
            width=8,
        ).grid(row=0, column=5, sticky="w", padx=(6, 0))

        ttk.Label(advanced, text="기사 본문 보조 문자 수").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Entry(
            advanced,
            textvariable=self.body_context_var,
            width=8,
        ).grid(
            row=1, column=1, sticky="w", padx=(6, 20), pady=(8, 0)
        )

        ttk.Label(advanced, text="댓글 최대 문자 수").grid(
            row=1, column=2, sticky="w", pady=(8, 0)
        )
        ttk.Entry(
            advanced,
            textvariable=self.max_comment_chars_var,
            width=8,
        ).grid(
            row=1, column=3, sticky="w", padx=(6, 20), pady=(8, 0)
        )

        ttk.Label(
            advanced,
            text="기사 본문 보조 문자는 기본값 0으로 두는 것을 권장합니다.",
            style="Muted.TLabel",
        ).grid(
            row=1, column=4, columnspan=2, sticky="w", pady=(8, 0)
        )

        control = ttk.Frame(container)
        control.pack(fill="x", pady=(12, 8))

        self.start_button = ttk.Button(
            control,
            text="분석 시작",
            style="Primary.TButton",
            command=self._start,
        )
        self.start_button.pack(side="left")

        self.stop_button = ttk.Button(
            control,
            text="중지",
            command=self._stop,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=(8, 0))

        ttk.Label(
            control,
            textvariable=self.calls_var,
            style="Warning.TLabel",
        ).pack(side="right")

        progress_frame = ttk.Frame(container)
        progress_frame.pack(fill="x")

        self.progress = ttk.Progressbar(
            progress_frame,
            mode="determinate",
            maximum=MAX_AI_CALLS_PER_RUN,
        )
        self.progress.pack(fill="x")

        ttk.Label(
            progress_frame,
            textvariable=self.status_var,
        ).pack(anchor="w", pady=(4, 0))

        self.output_paths_label = ttk.Label(
            progress_frame,
            textvariable=self.output_paths_var,
            style="Muted.TLabel",
            wraplength=920,
        )
        self.output_paths_label.pack(anchor="w", pady=(2, 0))

        log_frame = ttk.LabelFrame(
            container,
            text="실행 로그",
            style="Section.TLabelframe",
            padding=8,
        )
        log_frame.pack(fill="both", expand=True, pady=(10, 0))

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            wrap="word",
            height=16,
            font=("Consolas", 9),
            state="disabled",
        )
        self.log_text.pack(fill="both", expand=True)

    def _selected_provider(self) -> ProviderInfo:
        return PROVIDERS[self.provider_var.get()]

    def _on_provider_change(self) -> None:
        provider = self._selected_provider()
        self.model_var.set(provider.default_model)
        self.api_key_var.set("")
        self.key_hint_label.configure(text=provider.key_hint)
        self._update_api_controls()
        self._update_output_preview()

    def _update_api_controls(self) -> None:
        provider = self._selected_provider()
        disabled = self.prepare_only_var.get() or not provider.key_required

        if disabled:
            self.api_key_entry.configure(state="disabled")
            self.show_key_check.configure(state="disabled")
        else:
            self.api_key_entry.configure(state="normal")
            self.show_key_check.configure(state="normal")

        if not provider.key_required:
            self.api_key_var.set("")

    def _toggle_key_visibility(self) -> None:
        self.api_key_entry.configure(
            show="" if self.show_key_var.get() else "●"
        )

    def _choose_input_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="댓글 CSV 파일 선택",
            filetypes=[
                ("CSV 파일", "*.csv"),
                ("모든 파일", "*.*"),
            ],
        )
        if not selected:
            return

        path = Path(selected)
        self.input_path_var.set(str(path))

        if not self.output_dir_var.get().strip():
            self.output_dir_var.set(str(path.parent))

        self._update_output_preview()

    def _choose_output_dir(self) -> None:
        selected = filedialog.askdirectory(
            title="결과 저장 폴더 선택"
        )
        if selected:
            self.output_dir_var.set(selected)
            self._update_output_preview()

    def _update_output_preview(self) -> None:
        input_text = self.input_path_var.get().strip()
        output_text = self.output_dir_var.get().strip()
        model = self.model_var.get().strip()
        provider = self._selected_provider()

        if not input_text or not output_text or not model:
            self.output_paths_var.set("")
            return

        try:
            temp_config = RunConfig(
                provider_name=self.provider_var.get(),
                provider=provider,
                model=model,
                api_key="",
                input_path=Path(input_text),
                output_dir=Path(output_text),
                resume=self.resume_var.get(),
                prepare_only=self.prepare_only_var.get(),
                delay_seconds=DEFAULT_DELAY_SECONDS,
                max_retries=DEFAULT_MAX_RETRIES,
                body_context_chars=DEFAULT_BODY_CONTEXT_CHARS,
                max_comment_chars=DEFAULT_MAX_COMMENT_CHARS,
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            )
            detail, summary = make_output_paths(temp_config)
            self.output_paths_var.set(
                f"상세 결과: {detail}\n요약 결과: {summary}"
            )
        except Exception:
            self.output_paths_var.set("")

    def _parse_config(self) -> RunConfig:
        provider = self._selected_provider()
        model = self.model_var.get().strip()
        api_key = self.api_key_var.get().strip()
        input_text = self.input_path_var.get().strip()
        output_text = self.output_dir_var.get().strip()

        if not model:
            raise ValueError("모델 이름을 입력하십시오.")
        if not input_text:
            raise ValueError("입력 CSV 파일을 선택하십시오.")
        if not output_text:
            raise ValueError("결과 저장 폴더를 선택하십시오.")

        input_path = Path(input_text)
        output_dir = Path(output_text)

        if not input_path.exists() or not input_path.is_file():
            raise ValueError(f"입력 CSV 파일이 없습니다: {input_path}")

        if provider.key_required and not self.prepare_only_var.get() and not api_key:
            raise ValueError(f"{provider.display_name} API 키를 입력하십시오.")

        try:
            delay = float(self.delay_var.get())
            retries = int(self.retries_var.get())
            timeout = int(self.timeout_var.get())
            body_chars = int(self.body_context_var.get())
            comment_chars = int(self.max_comment_chars_var.get())
        except ValueError as error:
            raise ValueError(
                "고급 설정 값은 숫자로 입력하십시오."
            ) from error

        if delay < 0:
            raise ValueError("대기 시간은 0 이상이어야 합니다.")
        if not 1 <= retries <= MAX_AI_CALLS_PER_RUN:
            raise ValueError(
                f"최대 시도 횟수는 1~{MAX_AI_CALLS_PER_RUN} 사이여야 합니다."
            )
        if timeout < 10:
            raise ValueError("요청 시간 제한은 10초 이상이어야 합니다.")
        if body_chars < 0:
            raise ValueError("기사 본문 보조 문자 수는 0 이상이어야 합니다.")
        if comment_chars < 100:
            raise ValueError("댓글 최대 문자 수는 100 이상이어야 합니다.")

        return RunConfig(
            provider_name=self.provider_var.get(),
            provider=provider,
            model=model,
            api_key=api_key,
            input_path=input_path,
            output_dir=output_dir,
            resume=self.resume_var.get(),
            prepare_only=self.prepare_only_var.get(),
            delay_seconds=delay,
            max_retries=retries,
            body_context_chars=body_chars,
            max_comment_chars=comment_chars,
            timeout_seconds=timeout,
        )

    def _start(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            return

        try:
            config = self._parse_config()
        except Exception as error:
            messagebox.showerror("입력 오류", str(error))
            return

        self.stop_event.clear()
        self.progress.configure(value=0, maximum=MAX_AI_CALLS_PER_RUN)
        self.calls_var.set(f"AI 요청 사용: 0/{MAX_AI_CALLS_PER_RUN}회")
        self.status_var.set("분석 준비 중...")
        self._clear_log()
        self._set_running(True)

        self.worker_thread = threading.Thread(
            target=run_analysis,
            args=(
                config,
                self.event_queue,
                self.stop_event,
            ),
            daemon=True,
        )
        self.worker_thread.start()

    def _stop(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            self.stop_event.set()
            self.status_var.set(
                "중지 요청됨 — 현재 요청이 끝난 뒤 저장하고 중단합니다."
            )
            self._append_log(
                "\n중지 요청을 보냈습니다. 진행 중인 HTTP 요청은 즉시 강제 종료되지 않습니다."
            )

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(
            state="disabled" if running else "normal"
        )
        self.stop_button.configure(
            state="normal" if running else "disabled"
        )
        self.provider_combo.configure(
            state="disabled" if running else "readonly"
        )

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _process_events(self) -> None:
        try:
            while True:
                event_type, payload = self.event_queue.get_nowait()

                if event_type == "log":
                    self._append_log(payload["text"])

                elif event_type == "status":
                    self.status_var.set(payload["text"])

                elif event_type == "calls":
                    used = payload["used"]
                    maximum = payload["maximum"]
                    self.calls_var.set(
                        f"AI 요청 사용: {used}/{maximum}회"
                    )
                    self.progress.configure(value=used)

                elif event_type == "progress_max":
                    self.progress.configure(
                        maximum=max(
                            1,
                            payload["maximum"],
                        )
                    )

                elif event_type == "progress":
                    # AI 사용 횟수 표시가 핵심이므로 호출 수 이벤트가 우선합니다.
                    pass

                elif event_type == "paths":
                    self.output_paths_var.set(
                        f"상세 결과: {payload['detail']}\n"
                        f"요약 결과: {payload['summary']}"
                    )

                elif event_type == "done":
                    self.status_var.set("완료")
                    self._set_running(False)
                    self._append_log("\n" + payload["message"])
                    messagebox.showinfo(
                        "완료",
                        payload["message"]
                        + "\n\n상세 결과:\n"
                        + payload["detail"]
                        + "\n\n요약 결과:\n"
                        + payload["summary"],
                    )

                elif event_type == "stopped":
                    self.status_var.set("중지됨")
                    self._set_running(False)
                    self._append_log("\n" + payload["message"])
                    messagebox.showinfo(
                        "중지됨",
                        payload["message"],
                    )

                elif event_type == "error":
                    self.status_var.set("오류")
                    self._set_running(False)
                    self._append_log("\n[실행 오류]\n" + payload["message"])
                    messagebox.showerror(
                        "실행 오류",
                        payload["message"][:4_000],
                    )

        except queue.Empty:
            pass

        self.after(100, self._process_events)


def main() -> None:
    app = DiscourseClassifierGUI()
    app.mainloop()


if __name__ == "__main__":
    main()