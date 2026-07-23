"""노드 함수들. 각 노드: state(dict)를 받아 갱신할 부분(dict)을 반환.
LangGraph로 옮길 때 각 함수가 그대로 노드가 된다 (graph.add_node).

지금은 probe/map 의 '실제 알맹이'(HTTP 탐침, LLM 호출)를 가짜로 둔 스켈레톤.
classify/verify/finalize 의 '결정 로직'은 진짜 — 그래서 흐름을 키 없이 검증 가능.
"""
from __future__ import annotations
from collector.engine import collect, check
from agent.probe import probe_site


def _load_env():
    """.env 파일을 환경변수로 로드 (python-dotenv 있으면). 키를 코드가 자동 인식하게."""
    try:
        from dotenv import load_dotenv, find_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except Exception:
        pass

# 알려진 ATS 시그니처 (probe 결과의 body_sample/endpoints 에서 탐지)
ATS_SIGNATURES = {
    "greenhouse": ["gh_jid", "boards-api.greenhouse.io"],
    "lever": ["jobs.lever.co", "api.lever.co"],
    "workday": ["myworkdayjobs.com"],
}


def probe(state: dict) -> dict:
    """결정적 탐침(LLM 0개). probe_site 로 entry_url 을 찔러 단서 수집.
    테스트는 state['probe'] 또는 state['_client'] 주입으로 네트워크 우회."""
    if "probe" in state:
        return {}
    return {"probe": probe_site(state.get("company", ""), state["entry_url"],
                                client=state.get("_client"))}


def classify(state: dict) -> dict:
    """probe 결과로 두 축 판정. 결정 트리: ATS → declarative → playwright → custom.
    (이 로직은 진짜다. LLM 불필요 — 시그니처/콘텐츠 타입 규칙으로 충분.)"""
    p = state.get("probe", {})

    # ATS 는 '공개 API 가 실제 200 으로 확인된 경우'에만 (probe 가 ats 채움).
    # 시그니처만 있고 공개 API 가 없으면(토스) → 자체 declarative 로.
    if p.get("ats"):
        return {"crawler": "declarative", "source_type": "json", "ats": p["ats"],
                "ats_board": p.get("ats_board"),
                "classify_reason": f"{p['ats']} 공개 API 확인 (board={p.get('ats_board')})"}

    if p.get("blocked"):
        return {"crawler": "custom", "source_type": "json", "ats": None,
                "classify_reason": "랜딩 차단(예: Cloudflare) + ATS 아님 → playwright/custom 필요"}

    if p.get("spa_no_api"):
        markers = p.get("markers") or []
        # __NEXT_DATA__ / Gatsby 가 있으면 embedded_json 경로를 알 수 있을 수도 있지만,
        # 경로(path)를 사람이 확인해야 하므로 needs_input 은 유지.
        if "__NEXT_DATA__" in markers or "___gatsby" in markers:
            return {"crawler": "declarative", "source_type": "embedded_json", "ats": None,
                    "needs_input": True,
                    "classify_reason": "SPA + __NEXT_DATA__ 감지 + API 자동발견 실패 → embedded_json 경로를 사람이 확인 필요(Mode B)"}
        return {"crawler": "declarative", "source_type": "json", "ats": None,
                "needs_input": True,
                "classify_reason": "SPA + API 자동발견 실패 → 사람이 목록 API URL 제공(Mode B)"}

    ctype = p.get("content_type", "")
    has_api = bool(p.get("endpoints")) or bool(p.get("api_sample"))
    markers = p.get("markers") or []

    # probe 가 /_next/data/<hash>/ 패턴을 감지하고 __NEXT_DATA__ 를 직접 추출한 경우.
    # 해시 URL 대신 안정적인 page URL + script_id 방식으로 embedded_json 처리.
    if p.get("is_next_data"):
        return {"crawler": "declarative", "source_type": "embedded_json", "ats": None,
                "classify_reason": "Next.js __NEXT_DATA__ 임베드 (해시 URL 자동 감지 → embedded_json)"}

    # __NEXT_DATA__ 가 있고 API 엔드포인트도 발견됐으면 JSON API 우선.
    # API 없이 __NEXT_DATA__ 만 있으면 embedded_json (경로는 map_spec 이 처리).
    if "__NEXT_DATA__" in markers and not has_api:
        return {"crawler": "declarative", "source_type": "embedded_json", "ats": None,
                "classify_reason": "__NEXT_DATA__ 임베드 JSON (별도 API 없음)"}

    if "json" in ctype or has_api:
        return {"crawler": "declarative", "source_type": "json", "ats": None,
                "classify_reason": "자체 JSON 엔드포인트"}
    if "html" in ctype and p.get("status") == 200:
        return {"crawler": "declarative", "source_type": "html", "ats": None,
                "classify_reason": "SSR HTML"}
    return {"crawler": "custom", "source_type": "json", "ats": None,
            "classify_reason": "plain HTTP로 데이터 도달 실패 → custom"}


def ats_template(state: dict) -> dict:
    """ATS면 템플릿에 토큰만 채워 spec 완성. (스켈레톤: 실제론 토큰을 probe에서 추출.)
    LLM 거의 불필요 — 가장 싼 경로."""
    ats = state.get("ats")
    token = state.get("ats_board") or state.get("company")
    if ats == "greeting":
        gurl = state.get("probe", {}).get("greeting_url") or f"https://{token}.career.greetinghr.com/ko/main"
        spec = {
            "company": state["company"], "source_type": "embedded_json", "crawler": "declarative",
            "list": {"url": gurl,
                     "script_id": "__NEXT_DATA__",
                     "select": {"array": "props.pageProps.dehydratedState.queries",
                                "match_field": "queryKey", "match_value": ["openings"],
                                "take": "state.data"}},
            "pagination": {"type": "none"},
            "required": ["job_id", "title"],
            "fields": {"job_id": "openingId", "title": "title",
                       "posted_at": "openDate", "deadline": "dueDate",
                       "job_category": "openingJobPosition.openingJobPositions[].workspaceOccupation.occupation",
                       "location": "openingJobPosition.openingJobPositions[].workspacePlace.location"},
            "apply_url": {"template": f"https://{token}.career.greetinghr.com/ko/o/{{job_id}}"},
            "extra": {"d_day": "deadlineDDay"},
            "filter": {"field": "title",
                       "exclude_contains": ["인재풀", "Talent Pool", "talent pool"]},
            "detail": {
                "enabled": True,
                "source_type": "embedded_json",
                "url_template": f"https://{token}.career.greetinghr.com/ko/o/{{job_id}}",
                "script_id": "__NEXT_DATA__",
                "select": {
                    "array": "props.pageProps.dehydratedState.queries",
                    "match_field": "queryKey",
                    "match_prefix": ["career", "getOpeningById"],
                    "take": "state.data.data",
                },
                "fields": {"description": "openingsInfo.detail"},
            },
        }
        return {"spec": spec, "attempts": state.get("attempts", 0) + 1}
    if ats == "lever":
        spec = {
            "company": state["company"], "source_type": "json", "crawler": "declarative",
            "list": {"url": f"https://api.lever.co/v0/postings/{token}",
                     "params": {"mode": "json"}, "response_path": ""},   # 최상위가 배열
            "pagination": {"type": "none"},
            "required": ["job_id", "title", "updated_at"],
            "fields": {"job_id": "id", "title": "text", "updated_at": "createdAt",
                       "location": "categories.location", "job_category": "categories.team",
                       "employee_type": "categories.commitment", "apply_url": "applyUrl",
                       # 본문이 여러 조각 → 합쳐서 하나로
                       "description": ["openingPlain", "descriptionPlain", "additionalPlain"]},
            "extra": {"department": "categories.department", "country": "country",
                      "workplace_type": "workplaceType", "hosted_url": "hostedUrl"},
        }
        return {"spec": spec, "attempts": state.get("attempts", 0) + 1}
    if ats == "greenhouse":
        spec = {
            "company": state["company"], "source_type": "json", "crawler": "declarative",
            "list": {"url": f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                     "params": {"content": "true"}, "response_path": "jobs"},
            "pagination": {"type": "none"},
            "required": ["job_id", "title", "updated_at"],
            "fields": {"job_id": "id", "title": "title", "updated_at": "updated_at",
                       "location": "location.name", "apply_url": "absolute_url",
                       "description": "content"},
        }
        return {"spec": spec, "attempts": state.get("attempts", 0) + 1}
    return {"handoff_reason": f"ATS '{state.get('ats')}' 템플릿 없음", "crawler": "custom"}


def map_spec(state: dict) -> dict:
    """LLM이 probe의 api_sample을 보고 필드 매핑을 판단 → spec 조립 (1-A/2-A/3-A).
    - 테스트: state['_mock_llm'](text) 또는 state['_mock_spec'](완성 spec) 주입으로 키 없이.
    - 실제: ChatAnthropic 호출. 키 없으면 needs_input 으로 정직하게 멈춤.
    재시도 시 직전 issues 를 피드백으로 덧붙임."""
    from agent import mapper
    attempts = state.get("attempts", 0) + 1

    # 테스트 주입 1: 완성 spec 직접
    if "_mock_spec" in state:
        return {"spec": state["_mock_spec"], "attempts": attempts}

    if not state.get("probe", {}).get("api_sample"):
        return {"spec": {}, "attempts": attempts,
                "needs_input": True,
                "classify_reason": "API 응답 샘플 없음 → 사람이 목록 API URL 제공(Mode B)"}

    # 페이지네이션 재시도 보강: verify 가 pagination_incomplete 를 보고한 경우
    if state.get("pagination_incomplete"):
        probe = dict(state.get("probe") or {})   # 원본 state 를 변이하지 않도록 얕은 복사
        from agent.probe import detect_pagination, _extract_params
        api_url = probe.get("api_url", "")
        post_body = probe.get("api_post_body")
        params = _extract_params(api_url, post_body)
        pg = detect_pagination(probe.get("api_sample") or {}, params)
        if pg.get("type") not in ("none", "unknown_needed"):
            probe["pagination_hint"] = pg
            state = {**state, "probe": probe}    # 복사본으로 교체
        else:
            # 자동 탐지 실패 → LLM 프롬프트에 페이지네이션 컨텍스트 추가
            state = {**state, "_pagination_retry": True}

    messages = mapper.build_messages(state)
    if state.get("issues"):     # 재시도: 직전 실패를 알려 더 낫게
        messages[0]["content"] += f"\n\n[직전 시도 문제] {state['issues']} — 매핑을 고쳐라."

    # 테스트 주입 2: 가짜 LLM 텍스트
    this_cost = None
    if "_mock_llm" in state:
        text = state["_mock_llm"]
    else:
        try:
            _load_env()
            from langchain_anthropic import ChatAnthropic
            from agent.cost import usage_from_response, cost_of
            model = "claude-haiku-4-5"
            llm = ChatAnthropic(model=model, temperature=0, max_tokens=1024)
            resp = llm.invoke([{"role": "system", "content": mapper.SYSTEM}] + messages)
            text = resp.content
            this_cost = cost_of(usage_from_response(resp), model)
        except Exception as e:
            return {"spec": {}, "attempts": attempts,
                    "handoff_reason": f"LLM 호출 실패(키/패키지 확인): {e}"}

    try:
        parsed = mapper.parse_response(text)
        spec = mapper.assemble_spec(state, parsed)
    except Exception as e:
        return {"spec": {}, "attempts": attempts, "issues": [f"LLM 출력 파싱 실패: {e}"],
                "verified": False}
    out = {"spec": spec, "attempts": attempts, "map_reason": parsed.get("reason")}
    if this_cost:
        prev = state.get("llm_cost") or {"input_tokens": 0, "output_tokens": 0, "usd": 0.0, "krw": 0.0}
        out["llm_cost"] = {
            "model": this_cost["model"],
            "input_tokens": prev["input_tokens"] + this_cost["input_tokens"],
            "output_tokens": prev["output_tokens"] + this_cost["output_tokens"],
            "usd": round(prev["usd"] + this_cost["usd"], 6),
            "krw": round(prev["krw"] + this_cost["krw"], 2),
            "calls": (prev.get("calls", 0) + 1),
        }
    return out


def _check_pagination_completeness(spec, records, state):
    """pagination=none 인데 응답에 totalCount 류가 수집 건수보다 크면 issue 반환."""
    pg = (spec.get("pagination") or {}).get("type", "none")
    if pg != "none":
        return []
    # probe 의 원본 응답에서 total 정보 확인
    probe = state.get("probe") or {}
    sample = probe.get("api_sample")
    if not isinstance(sample, dict):
        return []
    from agent.probe import _TOTAL_KEYS
    for k, v in sample.items():
        if k.lower().replace("_", "").replace("-", "") in _TOTAL_KEYS and isinstance(v, (int, float)):
            total = int(v)
            if total > len(records):
                return [f"pagination=none 이지만 응답에 {k}={total}, 수집={len(records)}건 → 페이지네이션 필요 가능성"]
    return []


def verify(state: dict) -> dict:
    """엔진으로 실제 수집 → 1층(형식) 점검 → 2층(LLM 의미) 점검.
    2층은 명백한 오매핑만 잡고 애매하면 통과(무한 재시도 방지).
    테스트: client 주입(네트워크 없이) + _mock_verify(2층 LLM 대체)."""
    spec = state.get("spec") or {}
    client = state.get("_client")
    # 검증 시 상세 페이지는 최대 2건만 (실 수집은 스프링이 전체 처리)
    verify_spec = {**spec, "_detail_max": 2} if (spec.get("detail") or {}).get("enabled") else spec
    try:
        records = collect(verify_spec, client=client)
    except Exception as e:
        # 수집 예외(네트워크 4xx/5xx 등)는 '매핑 오류'가 아니라 'URL 호출 자체가 막힘'.
        # 재시도해도 무의미 → fetch_blocked 표시로 즉시 핸드오프(LLM 재시도 낭비 방지).
        return {"verified": False, "issues": [f"수집 예외: {e}"], "record_count": 0,
                "records_sample": [], "fetch_blocked": True}
    issues = check(records, spec)               # 1층: 형식
    if issues or not records:
        return {"verified": False, "issues": issues,
                "record_count": len(records), "records_sample": records[:2]}

    # 2층: LLM 의미 점검 (ATS 템플릿 경로는 검증된 매핑이라 생략 — 비용 절약)
    sem = _verify_semantic(state, records[0])
    if sem and not sem.get("ok", True) and sem.get("wrong"):
        return {"verified": False, "issues": [f"의미점검: {w}" for w in sem["wrong"]],
                "record_count": len(records), "records_sample": records[:2]}

    # 3층: 페이지네이션 완전성 검사 (safety net)
    pg_issues = _check_pagination_completeness(spec, records, state)
    if pg_issues:
        return {"verified": False, "issues": pg_issues,
                "record_count": len(records), "records_sample": records[:2],
                "pagination_incomplete": True}

    return {"verified": True, "issues": [],
            "record_count": len(records), "records_sample": records[:2]}


def _verify_semantic(state, sample):
    """수집 샘플 1건을 LLM 에 보여 의미 점검. ATS 경로/미설정 시 None(생략)."""
    if state.get("ats"):                # ATS 템플릿은 매핑이 이미 검증됨 → 2층 생략
        return None
    if "_mock_verify" in state:         # 테스트 주입
        return state["_mock_verify"]
    from agent import mapper
    msgs = mapper.build_verify_messages(sample)
    try:
        _load_env()
        from langchain_anthropic import ChatAnthropic
        from agent.cost import usage_from_response, cost_of
        llm = ChatAnthropic(model="claude-haiku-4-5", temperature=0, max_tokens=512)
        resp = llm.invoke([{"role": "system", "content": mapper.SYSTEM_VERIFY}] + msgs)
        return mapper.parse_response(resp.content)
    except Exception:
        return None                     # 키/패키지 없으면 2층 생략 (1층만으로 진행)


def discover_detail(state: dict) -> dict:
    """description 이 목록 API 에 없을 때 상세 페이지 수집 설정을 추가한다.
    셀렉터 탐지 없이 'body' 를 사용 — 상세 본문 전체를 가져오고,
    직무/요건 추출은 수집 이후 LLM 이 담당."""
    spec = state.get("spec") or {}

    # 이미 description 이 있거나 detail 이 설정돼 있으면 스킵
    if "description" in (spec.get("fields") or {}):
        return {}
    if (spec.get("detail") or {}).get("enabled"):
        return {}

    # apply_url 이 있어야 상세 페이지를 수집할 수 있음
    has_apply_url = (
        "apply_url" in (spec.get("fields") or {})
        or (spec.get("apply_url") or {}).get("template")
    )
    if not has_apply_url:
        return {}

    new_spec = dict(spec)
    new_spec["detail"] = {
        "enabled": True,
        "source_type": "html",
        "url_from": "apply_url",
        "body_selector": "body",
    }
    return {"spec": new_spec, "detail_selector": "body"}



def finalize(state: dict) -> dict:
    """완성된 spec 을 확정. save_dir 이 있으면 {save_dir}/{회사}.yaml 로 저장.
    저장된 YAML 은 이후 `python -m collector.run {파일}` 으로 0원 수집에 재사용."""
    spec = state.get("spec")
    out = {"status": "done", "final_spec": spec}
    save_dir = state.get("save_dir")
    if save_dir and spec:
        import os, yaml
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(save_dir, f"{state.get('company')}.yaml")
        header = (f"# 자동 생성됨 (분석 에이전트). 회사={state.get('company')}\n"
                  f"# 분류: {state.get('classify_reason')}\n")
        if state.get("ats"):
            header += f"# ATS: {state.get('ats')} (board={state.get('ats_board')})\n"
        if state.get("map_reason"):
            header += f"# LLM 매핑 근거: {state.get('map_reason')}\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(header)
            yaml.safe_dump(spec, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        out["saved_path"] = path
    return out


def to_human(state: dict) -> dict:
    if state.get("needs_input"):
        return {"status": "needs_input",
                "handoff_reason": "목록 API URL을 사람이 제공해야 함 (Mode B). "
                                  "예: entry_url 대신 실제 목록 API 주소를 주면 재시도 가능."}
    return {"status": "needs_human",
            "handoff_reason": state.get("handoff_reason")
            or f"검증 {state.get('attempts')}회 실패: {state.get('issues')}"}


# ----- 조건부 엣지 (라우팅 함수): 현재 state로 다음 노드 이름을 고른다 -----
def route_after_classify(state: dict) -> str:
    if state.get("crawler") == "custom" or state.get("needs_input"):
        return "to_human"
    if state.get("ats"):
        return "ats_template"
    return "map_spec"


def route_after_verify(state: dict) -> str:
    if state.get("verified"):
        return "finalize"
    if state.get("fetch_blocked"):
        return "to_human"        # URL 호출 자체가 막힘(4xx/5xx) → 매핑 재시도 무의미
    if state.get("attempts", 0) >= state.get("max_attempts", 3):
        return "to_human"        # 재시도 상한 → 비용 폭주 방지
    return "map_spec"            # 고쳐서 재시도
