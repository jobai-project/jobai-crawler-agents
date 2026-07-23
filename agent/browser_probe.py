"""playwright 로 SPA 의 숨은 목록 API 를 자동 발견 (분석 때 1회만; 수집은 declarative 유지).
설계:
  - declarative 추측이 실패한 SPA 에서만 호출 (probe 의 최후 수단).
  - 브라우저로 entry_url 을 열고, 발생하는 네트워크 응답 중 '공고 배열 같은 JSON' 을 고른다.
  - 찾으면 {url, method, post_data, sample} 반환 → probe 가 api_url/api_sample 로 사용.
  - playwright 미설치 환경에서는 import 가 실패하므로, 호출부에서 try/except 로 감싼다.
무겁다(브라우저 실행). probe 가 가벼운 경로를 다 시도한 뒤에만 쓴다.
"""
from __future__ import annotations
import json


# 공고 목록 응답인지 판별: 배열이 있고, 그 안 객체가 제목/ID 같은 키를 가진가.
_JOBISH_KEYS = ("title", "name", "recruitname", "jobtitle", "openingname",
                "recruitnoticename", "position", "subject")
_ARRAY_HINTS = ("list", "items", "data", "results", "content", "jobs",
                "openings", "recruits", "postings")


def _job_array_len(obj) -> int:
    """obj 안에서 job-ish 배열의 최대 길이. 없으면 0."""
    best = 0
    def walk(o, depth=0):
        nonlocal best
        if depth > 4:
            return
        if isinstance(o, list):
            if o and isinstance(o[0], dict):
                keys = {k.lower() for k in o[0].keys()}
                if keys & set(_JOBISH_KEYS):
                    best = max(best, len(o))
        elif isinstance(o, dict):
            for v in o.values():
                walk(v, depth + 1)
    walk(obj)
    return best


# URL 에 이런 신호가 있으면 '공고 목록 API' 일 확률 높음 (job-groups/codes 등 보조 API 와 구분)
_URL_STRONG = ("job-posting", "jobposting", "recruits", "recruit-notice",
               "openings", "/postings", "vacancies", "recruitlist", "getrecruit",
               "joblist", "getjob", "noticelist")
_URL_WEAK = ("job", "recruit", "position", "career")
_URL_BAD = ("group", "summary", "code", "corporation", "filter", "count",
            "category", "series", "link", "account", "renew", "translation",
            "autocomplete", "suggest", "keyword", "popular")


def _score(url: str, obj) -> int:
    """공고 목록일 가능성 점수. 높을수록 진짜 목록."""
    n = _job_array_len(obj)
    if n == 0:
        return 0
    u = url.lower()
    s = min(n, 100)                       # 배열이 길수록 가점(상한 100)
    if any(w in u for w in _URL_STRONG):
        s += 1000                         # 강한 URL 신호 (recruit-list 등)
    elif any(w in u for w in _URL_WEAK):
        s += 100
    if any(w in u for w in _URL_BAD):
        s -= 1500                         # 보조 API 신호 (autocomplete/summary 등) — 강하게 감점
    # 응답 형태 신호: {totalCount, list} 같은 목록 래퍼면 가점
    if isinstance(obj, dict):
        keys = {k.lower() for k in obj.keys()}
        if "list" in keys and ("totalcount" in keys or "total" in keys or "success" in keys):
            s += 300
    return s


def discover_api(entry_url: str, timeout_ms: int = 15000) -> dict | None:
    """브라우저로 entry_url 을 열고 공고 목록 API 를 가로채 반환. 못 찾으면 None."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None   # 미설치 → 조용히 포기 (호출부가 Mode B 로 감)

    candidates = []   # (score, url, method, post_data, data)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (joba-browser-probe)")

        def on_response(resp):
            ct = (resp.headers or {}).get("content-type", "")
            if "json" not in ct.lower():
                return
            try:
                data = resp.json()
            except Exception:
                return
            sc = _score(resp.url, data)
            if sc > 0:
                req = resp.request
                candidates.append((sc, resp.url, req.method, req.post_data, data))

        page.on("response", on_response)
        try:
            page.goto(entry_url, wait_until="networkidle", timeout=timeout_ms)
        except Exception:
            pass
        browser.close()

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)   # 점수 최고 선택
    sc, url, method, post_data, data = candidates[0]
    return {"url": url, "method": method, "post_data": post_data, "sample": data}
