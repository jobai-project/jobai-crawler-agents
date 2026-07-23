"""
선언적 수집 엔진 (reference implementation). YAML 명세 하나로 공고를 수집·정규화. LLM 0개.

지원:
  source_type=json  → 응답 JSON을 점 표기법(`a.b`, `a[].b`)으로 매핑
  source_type=html  → HTML을 CSS 셀렉터로 매핑 (selector/attr/regex/index/split)
  pagination=page_number | offset | none
  filter            → 매핑된 레코드 중 특정 필드 값만 남김
이 파이썬 구현이 곧 Java DeclarativeCrawler가 따라야 할 '실행 가능한 계약서'다.
"""
from __future__ import annotations
import json
import re

MAX_PAGES = 500
DEFAULT_REQUIRED = ("job_id", "title", "updated_at")


# ---------- 공통 ----------
def _apply_url(out, spec):
    tpl = (spec.get("apply_url") or {}).get("template")
    if tpl:
        try:
            out["apply_url"] = tpl.format(**out)   # 레코드의 아무 필드나 끼움 (job_id, recruit_number 등)
        except (KeyError, IndexError):
            pass
    return out


def _apply_filter(records, spec):
    """매핑된 레코드를 filter 로 거른다.
      - filter.in        : field 값이 목록에 든 것만 남김(포함, 기존)
      - filter.exclude_contains : 지정 필드 값에 이 문자열들 중 하나가 들어가면 제외
          예) 인재풀/Talent Pool 같은 '공고 아님' 노이즈 제거.
              {field: title, exclude_contains: ["인재풀","Talent Pool","상시"]}
    """
    f = spec.get("filter")
    if not f:
        return records
    out = records
    if f.get("in") is not None:
        allowed = set(f["in"])
        out = [r for r in out if r.get(f["field"]) in allowed]
    if f.get("exclude_contains"):
        needles = f["exclude_contains"]
        field = f.get("field", "title")
        def keep(r):
            val = r.get(field)
            s = " ".join(val) if isinstance(val, list) else str(val or "")
            return not any(nd.lower() in s.lower() for nd in needles)
        out = [r for r in out if keep(r)]
    return out


def check(records, spec=None):
    """1층(결정적) 점검 — 비용 0. 통과하면 빈 리스트."""
    spec = spec or {}
    required = tuple(spec.get("required") or DEFAULT_REQUIRED)
    issues = []
    n = len(records)
    if n == 0:
        return ["0건 수집 — url / 셀렉터 / 경로 / 필터 확인 필요"]
    for f in required:
        missing = sum(1 for r in records if not r.get(f))
        if missing:
            issues.append(f"필수필드 '{f}' 비어있음: {missing}/{n}")
    ids = [r.get("job_id") for r in records]
    if len(set(ids)) != len(ids):
        issues.append(f"job_id 중복: 고유 {len(set(ids))} / 전체 {n}")
    return issues


# ---------- JSON: 경로 매핑 ----------
def resolve(obj, path):
    """점 표기법 + `필드[].하위필드` 배열맵."""
    if obj is None or path is None or path == "":
        return obj
    seg, _, rest = path.partition(".")
    if seg.endswith("[]"):
        key = seg[:-2]
        lst = obj.get(key) if isinstance(obj, dict) else None
        if not isinstance(lst, list):
            return None
        return [resolve(item, rest) for item in lst]
    nxt = obj.get(seg) if isinstance(obj, dict) else None
    return resolve(nxt, rest)


def _resolve_field(raw, p):
    """필드 스펙이 리스트면 각 경로를 해석해 빈 값 빼고 줄바꿈으로 이어붙임.
    (Lever 처럼 본문이 여러 조각으로 나뉜 경우)"""
    if isinstance(p, list):
        parts = [resolve(raw, x) for x in p]
        parts = [str(x).strip() for x in parts if x not in (None, "")]
        return "\n\n".join(parts) if parts else None
    return resolve(raw, p)


def _apply_null_values(out, spec):
    """null_values 규칙 적용: 필드 값에 지정 문자열이 포함되면 None 으로 치환.
    예) null_values: {deadline: "2999"} → deadline 에 "2999" 가 포함되면 None."""
    nv = spec.get("null_values")
    if not nv:
        return out
    for field, marker in nv.items():
        val = out.get(field)
        if val is not None and str(marker) in str(val):
            out[field] = None
    return out


def map_record_json(raw, spec):
    out = {col: _resolve_field(raw, p) for col, p in spec.get("fields", {}).items()}
    me = spec.get("metadata_extraction")
    if me:
        arr = resolve(raw, me["source_field"]) or []
        by = {item.get(me["match_by"]): item.get(me["value_from"])
              for item in arr if isinstance(item, dict)}
        for col, key in me.get("mappings", {}).items():
            val = by.get(key)
            if val not in (None, ""):
                out[col] = val
    extra = spec.get("extra", {})
    if extra:
        out["extra"] = {k: _resolve_field(raw, p) for k, p in extra.items()}
    return _apply_null_values(_apply_url(out, spec), spec)


def collect_json(spec, client):
    ls = spec["list"]
    pg = spec.get("pagination", {"type": "none"})
    ptype = pg.get("type", "none")
    rec_path = ls.get("record_path")     # 배열의 각 항목에서 한 단계 더 들어갈 때(토스 primary_job)
    params = dict(ls.get("params", {}))
    body = dict(ls["body"]) if ls.get("body") is not None else None   # POST JSON body (카카오뱅크 등)
    method = ls.get("method", "POST" if body is not None else "GET")
    page = pg.get("start", 1)
    offset = pg.get("start", 0)
    seen, raw = 0, []
    while True:
        # 페이지네이션 값을 params 또는 body 에 반영 (body 우선 — POST API 는 보통 body 로 페이징)
        target = body if body is not None else params
        if ptype == "page_number":
            target[pg["param"]] = page
        elif ptype == "offset":
            target[pg["param"]] = offset
        r = client.request(method, ls["url"], params=params,
                           json=body, headers=ls.get("headers", {}), timeout=20.0)
        r.raise_for_status()
        batch = resolve(r.json(), ls["response_path"]) or []
        if rec_path:
            batch = [resolve(item, rec_path) for item in batch]
            batch = [b for b in batch if b is not None]
        raw.extend(batch)
        seen += 1
        if ptype == "none" or seen >= MAX_PAGES:
            break
        if ptype == "page_number":
            if not batch:                   # 빈 배치 → 더 이상 데이터 없음
                break
            total = resolve(r.json(), pg.get("total_pages_path"))
            # total 이 문자열 숫자("63")로 올 수 있으므로 int 변환 시도
            if total is not None and not isinstance(total, int):
                try:
                    total = int(total)
                except (ValueError, TypeError):
                    total = None
            if total is not None and page >= total:
                break
            page += 1
        elif ptype == "offset":
            if not batch:
                break
            offset += len(batch)
    return [map_record_json(x, spec) for x in raw]


# ---------- HTML: 셀렉터 매핑 ----------
def extract_field(row, fspec):
    """카드(row)에서 필드 하나 추출.
    fspec: 문자열(=셀렉터, 텍스트) 또는 dict:
      selector / index / attr / regex / split+take
    """
    if isinstance(fspec, str):
        fspec = {"selector": fspec}
    sel = fspec.get("selector")
    els = row.select(sel) if sel else [row]
    idx = fspec.get("index", 0)
    if not els or idx >= len(els):
        return None
    el = els[idx]
    attr = fspec.get("attr")
    if attr:
        val = el.get(attr)
        if isinstance(val, list):
            val = " ".join(val)
        val = val or ""
    else:
        val = el.get_text(strip=True)
    if fspec.get("regex"):
        m = re.search(fspec["regex"], val)
        val = m.group(1) if m else None
    if val is not None and "split" in fspec:
        parts = val.split(fspec["split"])
        take = fspec.get("take", 0)
        val = parts[take].strip() if take < len(parts) else None
    return val


def map_record_html(row, spec):
    out = {col: extract_field(row, fs) for col, fs in spec.get("fields", {}).items()}
    extra = spec.get("extra", {})
    if extra:
        out["extra"] = {k: extract_field(row, fs) for k, fs in extra.items()}
    return _apply_url(out, spec)


def collect_html(spec, client):
    from bs4 import BeautifulSoup
    ls = spec["list"]
    r = client.request(ls.get("method", "GET"), ls["url"],
                       params=ls.get("params", {}), headers=ls.get("headers", {}), timeout=20.0)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return [map_record_html(row, spec) for row in soup.select(ls["row_selector"])]


def _extract_embedded(html, script_id):
    """HTML 에서 특정 <script id=...> 안의 JSON 을 꺼낸다 (__NEXT_DATA__ 등 SSG 임베드)."""
    import re as _re
    m = _re.search(r'<script[^>]*id="%s"[^>]*>(.*?)</script>' % _re.escape(script_id),
                   html, _re.S)
    if not m:
        return None
    return json.loads(m.group(1))


def collect_embedded_json(spec, client):
    """SSG 페이지에 박힌 JSON(__NEXT_DATA__ 등)에서 공고 배열을 꺼내 매핑.
    list.script_id (기본 __NEXT_DATA__) 로 스크립트 지정, response_path 로 배열 위치."""
    ls = spec["list"]
    r = client.request(ls.get("method", "GET"), ls["url"],
                       params=ls.get("params", {}), headers=ls.get("headers", {}), timeout=20.0)
    r.raise_for_status()
    data = _extract_embedded(r.text, ls.get("script_id", "__NEXT_DATA__"))
    if data is None:
        return []
    sel = ls.get("select")
    if sel:
        # 배열(select.array)에서 match_field==match_value 인 항목을 찾아 take 경로를 꺼냄.
        # React Query 캐시(queries[].queryKey)처럼 "조건 맞는 한 항목"을 고를 때.
        arr = resolve(data, sel["array"]) or []
        want = sel["match_value"]
        picked = None
        for item in arr:
            if resolve(item, sel["match_field"]) == want:
                picked = resolve(item, sel["take"])
                break
        batch = picked or []
    else:
        batch = resolve(data, ls["response_path"]) or []
    return [map_record_json(x, spec) for x in batch]


# ---------- 상세 본문 (선택: 공고마다 상세 페이지 1회 더 호출) ----------
def extract_detail(html, spec):
    from bs4 import BeautifulSoup
    d = spec.get("detail") or {}
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    if d.get("body_selector"):
        el = soup.select_one(d["body_selector"])
        if el is not None:
            out["description"] = el.get_text("\n", strip=True)
    if d.get("section_box"):
        sections = {}
        for box in soup.select(d["section_box"]):
            t = box.select_one(d["section_title"])
            title = t.get_text(strip=True) if t is not None else ""
            if not title:                      # 제목 없는 박스(전형절차/유의사항 등)는 건너뜀
                continue
            # p.detail_text 안에 <p>가 중첩돼 파서가 끊는 경우가 있어,
            # 박스 전체 텍스트에서 제목만 떼는 방식이 더 견고하다.
            body = box.get_text("\n", strip=True)
            if body.startswith(title):
                body = body[len(title):].strip()
            sections[title] = body
        out["sections"] = sections
    return out


def _flatten_value(val):
    """resolve 결과가 단순 값이면 그대로, 객체 배열이면 텍스트로 평탄화.
    예) [{title:"주요 업무", contents:["a","b"]}, ...] → "주요 업무\na\nb\n\n..."
    """
    if not isinstance(val, list) or not val or not isinstance(val[0], dict):
        return val
    parts = []
    for item in val:
        title = item.get("title", "") or ""
        contents = item.get("contents") or []
        if isinstance(contents, list):
            contents = "\n".join(str(c) for c in contents if c)
        else:
            contents = str(contents)
        text = f"{title}\n{contents}".strip() if title else contents.strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts) if parts else val


def fetch_detail(record, spec, client):
    d = spec.get("detail") or {}
    # URL 결정: url_template(레코드 필드로 조립) 우선, 없으면 url_from 필드값.
    tmpl = d.get("url_template")
    if tmpl:
        try:
            url = tmpl.format(**record)         # 예: ".../recruits/{recruit_number}"
        except (KeyError, IndexError):
            return record
    else:
        url = record.get(d.get("url_from", "apply_url"))
    if not url:
        return record
    r = client.request("GET", url, headers=(spec.get("list", {}).get("headers") or {}), timeout=20.0)
    r.raise_for_status()

    if d.get("source_type") == "json":
        # JSON 상세: 응답에서 지정 경로의 본문/필드를 추출 (배민 recruitContents 등)
        data = r.json()
        for col, path in (d.get("fields") or {}).items():
            val = resolve(data, path)
            if val not in (None, ""):
                record[col] = _flatten_value(val)
    elif d.get("source_type") == "embedded_json":
        # 상세페이지 __NEXT_DATA__ 에서 추출 (그리팅 등). select 로 쿼리 고르고 base 경로 기준 매핑.
        data = _extract_embedded(r.text, d.get("script_id", "__NEXT_DATA__"))
        if data is not None:
            base = data
            sel = d.get("select")
            if sel:
                arr = resolve(data, sel["array"]) or []
                for item in arr:
                    key = resolve(item, sel["match_field"])
                    mv = sel.get("match_value")
                    mp = sel.get("match_prefix")
                    hit = (key == mv) if mv is not None else (isinstance(key, list) and key[:len(mp)] == mp)
                    if hit:
                        base = resolve(item, sel["take"])
                        break
            for col, path in (d.get("fields") or {}).items():
                val = resolve(base, path)
                if val not in (None, ""):
                    record[col] = val
    else:
        record.update(extract_detail(r.text, spec))
    return record


# ---------- 진입점 ----------
def collect(spec, client=None):
    st = spec.get("source_type")
    own = client is None
    if own:
        import httpx
        client = httpx.Client(headers={"User-Agent": "Mozilla/5.0 (joba-collector/0.1)"},
                              follow_redirects=True)   # 그리팅 커스텀 도메인(301) 등 대응
    try:
        if st == "json":
            records = collect_json(spec, client)
        elif st == "html":
            records = collect_html(spec, client)
        elif st == "embedded_json":
            records = collect_embedded_json(spec, client)
        else:
            raise NotImplementedError(f"source_type={st} 미지원")
        records = _apply_filter(records, spec)
        if (spec.get("detail") or {}).get("enabled"):
            max_d = spec.get("_detail_max")   # verify 시 2건만 테스트, 실 수집 시 None(전체)
            targets = records[:max_d] if max_d else records
            for rec in targets:
                try:
                    fetch_detail(rec, spec, client)
                except Exception as e:
                    rec["detail_error"] = str(e)
        return records
    finally:
        if own:
            client.close()
