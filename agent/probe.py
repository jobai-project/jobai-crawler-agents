"""결정적 사이트 탐침 (LLM 0개). entry_url을 찔러 classify가 판정할 단서를 모은다.

수집 항목:
  status, content_type, body_sample
  signatures   : 탐지된 ATS 이름들 (greenhouse/lever/workday)
  ats          : 공개 API가 200으로 확인된 ATS (보드 토큰 포함) 또는 None
  endpoints    : 본문에서 발견한 API 경로 (/api/..., *.do, page-data.json 등)
  markers      : SSR/SSG 프레임워크 힌트 (__NEXT_DATA__, ___gatsby, .do ...)
이 모듈은 네 회사(kakao/naver/coupang/toss)에서 손으로 하던 일을 자동화한 것.
"""
from __future__ import annotations
import json
import re

ATS_HINTS = {
    "greenhouse": ["gh_jid", "greenhouse.io", "boards.greenhouse.io"],
    "lever": ["jobs.lever.co", "api.lever.co", "lever.co/"],
    "workday": ["myworkdayjobs.com", ".workday.com"],
}

# ATS별 공개 API: (토큰으로 URL 만들기, 응답이 그 ATS 맞는지 판별)
ATS_API = {
    "greenhouse": {
        "url": lambda tok: f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs",
        "ok": lambda body, ct: ('"jobs"' in body or "json" in ct.lower()),
    },
    "lever": {
        "url": lambda tok: f"https://api.lever.co/v0/postings/{tok}?mode=json",
        # Lever 는 최상위가 배열이고 hostedUrl/applyUrl/categories 를 가짐
        "ok": lambda body, ct: (body.strip()[:1] == "[" and
                                ("hostedUrl" in body or "applyUrl" in body or "categories" in body)),
    },
}
# 본문에서 API/데이터 엔드포인트 후보 추출
_ENDPOINT_RE = re.compile(r"""['"(]([^'"()\s]*?(?:/api/[^'"()\s]*|\.do(?:\?[^'"()\s]*)?|page-data[^'"()\s]*\.json))['")]""")
_NEXT_DATA_RE = re.compile(r"/_next/data/[^/]+/")
_GH_TOKEN_RE = re.compile(r"boards(?:-api)?\.greenhouse\.io/v1/boards/([a-z0-9\-]+)", re.I)
_LV_TOKEN_RE = re.compile(r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?([a-z0-9\-]+)", re.I)


BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/json,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


def _get(client, url, timeout=20.0, **kw):
    """주입된 client(.request) 우선, 없으면 httpx 로 GET. (status, ctype, text) 반환."""
    if client is not None:
        r = client.request("GET", url, **kw)
    else:
        import httpx
        with httpx.Client(headers=BROWSER_HEADERS, follow_redirects=True) as c:
            r = c.request("GET", url, timeout=timeout, **kw)
    status = getattr(r, "status_code", 0)
    ctype = ""
    headers = getattr(r, "headers", {}) or {}
    try:
        ctype = headers.get("content-type", "")
    except Exception:
        ctype = ""
    return status, ctype, getattr(r, "text", "") or ""


def _scan(text):
    """본문에서 시그니처/엔드포인트/마커 추출."""
    low = text.lower()
    sigs = [name for name, hints in ATS_HINTS.items() if any(h in low for h in hints)]
    eps = set(m.group(1) for m in _ENDPOINT_RE.finditer(text))
    # 채용 관련 경로를 앞세움 (recruit/job/career/api) → map 이 목록 API 고르기 쉬움
    JOBISH = ("recruit", "job", "career", "/api/", "채용")
    endpoints = sorted(eps, key=lambda e: (not any(k in e.lower() for k in JOBISH), e))
    markers = []
    for mk in ("__NEXT_DATA__", "___gatsby", "__NUXT__", "loadJobList"):
        if mk in text:
            markers.append(mk)
    if re.search(r"\b\w+\.do\b", text):
        markers.append(".do")
    return sigs, endpoints, sorted(set(markers))


_OFFSET_PARAMS = {"firstindex", "offset", "start", "skip", "from", "startindex",
                   "beginindex", "startrow"}
_PAGE_PARAMS = {"page", "pageno", "pagenumber", "currentpage", "pageindex", "pageidx"}
_SIZE_PARAMS = {"pagesize", "size", "limit", "count", "rows", "perpage", "per_page"}
_TOTAL_KEYS = {"totalcount", "total", "totalelements", "total_count", "totaldatacount",
               "totalrows", "recordcount", "allcount", "totalsize", "total_size"}
_TOTAL_PAGES_KEYS = {"totalpages", "pagecount", "lastpage", "total_pages", "totalpagecnt"}


def _find_main_array(data):
    """응답에서 공고 목록 배열과 그 길이를 찾는다. (data, array_len) 반환."""
    if isinstance(data, list):
        return data, len(data)
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                return v, len(v)
    return None, 0


def _find_total(data):
    """응답 래퍼에서 total 계열 값과 totalPages 계열 값을 찾는다."""
    total = None
    total_pages = None
    total_pages_key = None
    if not isinstance(data, dict):
        return total, total_pages, total_pages_key
    for k, v in data.items():
        kl = k.lower().replace("_", "").replace("-", "")
        if kl in _TOTAL_KEYS and isinstance(v, (int, float)):
            total = int(v)
        if kl in _TOTAL_PAGES_KEYS and isinstance(v, (int, float)):
            total_pages = int(v)
            total_pages_key = k
    return total, total_pages, total_pages_key


def _match_param(params: dict, candidates: set):
    """params 의 키 중 candidates 에 매칭되는 원본 키와 값을 반환."""
    for k, v in params.items():
        if k.lower().replace("_", "").replace("-", "") in candidates:
            try:
                return k, int(v)
            except (ValueError, TypeError):
                return k, 0
    return None, None


def detect_pagination(data, params: dict | None = None) -> dict:
    """API 응답 원본(data)과 요청 파라미터(params)를 분석하여 페이지네이션 설정을 추론.

    Returns:
        {"type": "none"}
        {"type": "offset", "param": "firstIndex", "start": 0}
        {"type": "page_number", "param": "page", "start": 1, "total_pages_path": "totalPages"}
    """
    params = params or {}
    _, array_len = _find_main_array(data)
    total, total_pages, total_pages_key = _find_total(data)

    # total <= array_len 이면 한 번에 전부 옴 → 페이지네이션 불필요
    if total is not None and total <= array_len:
        return {"type": "none"}

    # 요청 파라미터에서 offset/page 계열 탐색
    off_param, off_val = _match_param(params, _OFFSET_PARAMS)
    pg_param, pg_val = _match_param(params, _PAGE_PARAMS)
    size_param, size_val = _match_param(params, _SIZE_PARAMS)

    # offset 계열 파라미터가 있으면 offset 타입
    if off_param is not None:
        return {"type": "offset", "param": off_param, "start": off_val if off_val is not None else 0}

    # page 계열 파라미터가 있으면 page_number 타입
    if pg_param is not None:
        hint = {"type": "page_number", "param": pg_param, "start": pg_val if pg_val is not None else 1}
        if total_pages_key:
            hint["total_pages_path"] = total_pages_key
        return hint

    # 파라미터에는 없지만 total > array_len 이면 페이지네이션 필요 확정
    # (파라미터 이름을 못 찾은 경우: POST body 안에 있을 수 있음)
    if total is not None and array_len > 0 and total > array_len:
        return {"type": "unknown_needed"}

    return {"type": "none"}


def _scan_html_for_params(html: str) -> dict:
    """HTML/JS 소스에서 offset/page 계열 파라미터 이름과 값을 추출.
    JS 코드에서 `firstIndex: 0`, `"page": 1`, `"&firstIndex=" + var` 등의 패턴을 찾는다."""
    params = {}
    all_candidates = _OFFSET_PARAMS | _PAGE_PARAMS | _SIZE_PARAMS
    # 패턴 1: JS 객체 리터럴 — key: value 또는 "key": value
    for m in re.finditer(r"""['"]?(\w+)['"]?\s*[:=]\s*(\d+)""", html):
        name, val = m.group(1), m.group(2)
        if name.lower().replace("_", "").replace("-", "") in all_candidates:
            params[name] = int(val)
    # 패턴 2: 쿼리스트링 조립 — "&firstIndex=" + var 또는 "firstIndex=" + var
    for m in re.finditer(r"""[&?](\w+)=["']?\s*\+""", html):
        name = m.group(1)
        nl = name.lower().replace("_", "").replace("-", "")
        if nl in all_candidates and name not in params:
            # offset 계열은 기본 start=0, page 계열은 기본 start=1
            params[name] = 0 if nl in _OFFSET_PARAMS else 1
    return params


def _extract_params(url: str, body: dict | None = None) -> dict:
    """URL 의 query string + POST body 의 최상위 키를 합쳐 파라미터 dict 로 반환."""
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(url).query)
    params = {k: v[0] if len(v) == 1 else v for k, v in qs.items()}
    if isinstance(body, dict):
        params.update(body)
    return params


def probe_site(company: str, entry_url: str, client=None) -> dict:
    """entry_url 탐침 + ATS면 공개 API 확인까지. probe dict 반환."""
    out = {"status": 0, "content_type": "", "body_sample": "",
           "signatures": [], "ats": None, "ats_board": None,
           "endpoints": [], "markers": []}
    try:
        status, ctype, text = _get(client, entry_url)
    except Exception as e:
        out["error"] = str(e)
        return out

    out["status"] = status
    out["content_type"] = ctype
    out["body_sample"] = text[:1500]
    sigs, endpoints, markers = _scan(text)
    out["signatures"] = sigs
    out["endpoints"] = endpoints
    out["markers"] = markers

    # entry_url 자체가 JSON API 인 경우 (Mode B: 사람이 목록 API URL 을 직접 줌).
    # 응답이 JSON 이면 그걸 그대로 api_sample 로 잡고 즉시 반환 → map_spec(LLM) 으로.
    if status == 200 and text.strip()[:1] in "{[":
        try:
            data = json.loads(text)
            out["api_url"] = entry_url
            out["api_sample"] = _shrink(data)
            pg = detect_pagination(data, _extract_params(entry_url))
            if pg.get("type") != "unknown_needed":
                out["pagination_hint"] = pg
            return out
        except Exception:
            pass

    # 그리팅(Greeting) 탐지: 공개 토큰 API 가 없어(인증 필요) 도메인/임베드 시그로 식별.
    #   - 도메인이 *.career.greetinghr.com  또는
    #   - __NEXT_DATA__ 에 ["openings"] 쿼리가 있으면 그리팅 채용페이지.
    if "career.greetinghr.com" in entry_url.lower() or '"openings"' in text:
        out["ats"] = "greeting"
        from urllib.parse import urlparse as _up
        host = _up(entry_url).netloc
        out["ats_board"] = host.split(".")[0] if host.endswith("career.greetinghr.com") else company
        if "greeting" not in out["signatures"]:
            out["signatures"] = sorted(out["signatures"] + ["greeting"])
        # 공고가 실제로 박힌 페이지 경로 찾기 (회사마다 다름: /ko/main, /ko/apply 등).
        # entry_url 응답에 이미 openings 있으면 그게 정답, 아니면 후보를 시도.
        origin = f"{_up(entry_url).scheme}://{host}"
        if '"openings"' in text:
            out["greeting_url"] = entry_url
        else:
            for path in ("/ko/apply", "/ko/positions", "/ko/recruiting", "/ko/main", "/ko/job-list", "/ko/jobs"):
                cand = origin + path
                if cand == entry_url:
                    continue
                try:
                    st, ct, body = _get(client, cand)
                except Exception:
                    continue
                if st == 200 and '"openings"' in body:
                    out["greeting_url"] = cand
                    break
            else:
                out["greeting_url"] = entry_url   # 못 찾으면 일단 entry (verify 가 0건 잡음)
        return out

    # 보드 토큰 후보: 본문에서 추출한 것(ATS별) + company 이름
    def _candidates(token_re):
        cands = []
        mm = token_re.search(text)
        if mm:
            cands.append(mm.group(1))
        cands.append(company)
        return list(dict.fromkeys(c for c in cands if c))   # 중복 제거, 순서 유지

    # entry_url 이 막혔거나(4xx/5xx) 단서가 빈약하면, 시그가 없어도 ATS 보드를 직접 시도.
    weak = status >= 400 or (not sigs and not endpoints)

    # 어떤 ATS 를 시도할지: 탐지된 시그 우선, weak 면 전부 시도
    to_try = [a for a in ATS_API if a in sigs] or (list(ATS_API) if weak else [])
    token_re = {"greenhouse": _GH_TOKEN_RE, "lever": _LV_TOKEN_RE}

    for ats in to_try:
        spec = ATS_API[ats]
        for token in _candidates(token_re.get(ats, _GH_TOKEN_RE)):
            api = spec["url"](token)
            try:
                st, ct, body = _get(client, api)
            except Exception:
                continue
            if st == 200 and spec["ok"](body, ct):
                out["ats"] = ats
                out["ats_board"] = token
                if ats not in out["signatures"]:
                    out["signatures"] = sorted(out["signatures"] + [ats])
                out["endpoints"] = sorted(set(out["endpoints"] + [api]))
                break
        if out["ats"]:
            break
    # SPA(첫 HTML에 API 단서 0개)면 same-origin 흔한 채용 API 경로를 추측 시도
    if status == 200 and not out["ats"] and not endpoints:
        from urllib.parse import urlparse
        u = urlparse(entry_url)
        origin = f"{u.scheme}://{u.netloc}"
        # 실측에서 모은 흔한 채용 목록 API 경로 (카카오·배민·카카오뱅크 등)
        COMMON = ["/public/api/job-list", "/api/jobs", "/api/job-list",
                  "/api/v1/jobs", "/api/recruit/jobs", "/api/recruits",
                  "/api/recruit/list", "/w1/recruits", "/recruits"]
        # entry_url 의 경로에서 파생: /recruits → /api/recruits, /jobs → /api/jobs 등
        path = (u.path or "").rstrip("/")
        derived = []
        if path:
            derived += [path, "/api" + path]
            if not path.startswith("/api"):
                derived.append("/api/v1" + path)
            # Gatsby: /page-data/<path>/page-data.json (___gatsby 마커 없어도 시도)
            derived.insert(0, f"/page-data{path}/page-data.json")
        # 파생 후보를 앞에 두어 우선 시도 (현재 페이지와 같은 명칭일 확률 높음)
        candidates = list(dict.fromkeys(derived + COMMON))
        for cand in candidates:
            try:
                st, ct, body = _get(client, origin + cand, timeout=5.0)
            except Exception:
                continue
            if st == 200 and ("json" in ct.lower() or body.strip()[:1] in "{["):
                out["endpoints"] = sorted(set(out["endpoints"] + [origin + cand]))
                break
        if not out["endpoints"]:
            # declarative 추측 전부 실패 → 최후 수단: playwright 로 브라우저 띄워 API 발견.
            # (분석 때 1회만. 미설치/실패 시 None → Mode B 로.)
            try:
                from agent.browser_probe import discover_api
                hit = discover_api(entry_url)
            except Exception:
                hit = None
            if hit:
                out["api_url"] = hit["url"]
                out["api_sample"] = _shrink(hit["sample"])
                out["endpoints"] = [hit["url"]]
                out["browser_discovered"] = True
                post_body = None
                if hit.get("method") == "POST" and hit.get("post_data"):
                    try:
                        post_body = json.loads(hit["post_data"])
                        out["api_post_body"] = post_body
                    except Exception:
                        out["api_post_body"] = None
                pg = detect_pagination(hit["sample"], _extract_params(hit["url"], post_body))
                if pg.get("type") != "unknown_needed":
                    out["pagination_hint"] = pg
            elif "__NEXT_DATA__" in markers:
                # API 자동발견 실패했지만 __NEXT_DATA__ 가 HTML 에 임베드돼 있음.
                # 별도 엔드포인트 없이 entry_url 페이지에서 직접 추출 가능.
                m = re.search(
                    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
                    text, re.S,
                )
                if m:
                    try:
                        nd_data = json.loads(m.group(1))
                        out["api_url"] = entry_url
                        out["api_sample"] = _shrink(nd_data)
                        out["is_next_data"] = True
                        out["pagination_hint"] = {"type": "none"}
                    except Exception:
                        out["spa_no_api"] = True
                else:
                    out["spa_no_api"] = True
            else:
                out["spa_no_api"] = True   # SPA인데 API 자동발견 실패 → Mode B(사람이 API URL 제공)

    if weak and not out["ats"]:
        out["blocked"] = status >= 400

    # 자체 구축(ATS 아님)인데 API 엔드포인트를 찾았으면, 그 응답 샘플을 확보.
    # → map_spec(LLM)이 '실제 데이터'를 보고 필드를 고르게 (추측 금지).
    if not out["ats"] and out["endpoints"]:
        ep = out["endpoints"][0]              # 채용 관련 경로가 앞에 정렬돼 있음
        url = ep if ep.startswith("http") else _origin_of(entry_url) + ep

        # Next.js /_next/data/<hash>/<page>.json 패턴 감지:
        # 해시는 배포마다 바뀌므로 이 URL 을 spec 에 저장하면 금방 깨진다.
        # 대신 entry_url HTML 에 임베드된 __NEXT_DATA__ 를 직접 파싱해 안정적 URL 사용.
        if _NEXT_DATA_RE.search(url) and "__NEXT_DATA__" in markers:
            m = re.search(
                r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
                text, re.S,
            )
            if m:
                try:
                    nd_data = json.loads(m.group(1))
                    out["api_url"] = entry_url          # 배포에 무관한 페이지 URL
                    out["api_sample"] = _shrink(nd_data)
                    out["is_next_data"] = True
                    out["pagination_hint"] = {"type": "none"}
                    return out
                except Exception:
                    pass                                 # 파싱 실패 시 아래 일반 경로로

        try:
            st, ct, body_resp = _get(client, url)
            if st == 200 and body_resp and body_resp.strip()[:1] in "{[":
                data = json.loads(body_resp)
                out["api_url"] = url
                out["api_sample"] = _shrink(data)   # 토큰 절약: 배열은 앞 2건만
                # Gatsby page-data.json 은 정적 파일 — 쿼리 파라미터를 무시하므로 항상 none
                if re.search(r"/page-data/.+\.json$", url):
                    out["pagination_hint"] = {"type": "none"}
                else:
                    params = _extract_params(url)
                    pg = detect_pagination(data, params)
                    # URL params 만으로 못 찾으면 HTML 소스의 JS 에서 파라미터 탐색
                    if pg.get("type") in ("unknown_needed", "none"):
                        html_params = _scan_html_for_params(text)
                        if html_params:
                            pg2 = detect_pagination(data, html_params)
                            if pg2.get("type") not in ("none", "unknown_needed"):
                                pg = pg2
                    if pg.get("type") != "unknown_needed":
                        out["pagination_hint"] = pg
        except Exception:
            pass
    return out


def _origin_of(u):
    from urllib.parse import urlparse
    p = urlparse(u)
    return f"{p.scheme}://{p.netloc}"


def _shrink(data, list_take=2):
    """LLM 입력용 축약: 깊은 배열은 앞 N건만 남겨 토큰을 아끼되 구조는 보존."""
    if isinstance(data, list):
        return [_shrink(x, list_take) for x in data[:list_take]]
    if isinstance(data, dict):
        return {k: _shrink(v, list_take) for k, v in data.items()}
    return data
