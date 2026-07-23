"""map_spec 의 LLM 부분: probe 의 api_sample 을 보고 '필드 매핑'을 판단(3-A).
설계 원칙:
  입력  = probe 가 확보한 실제 API 응답 샘플 + 후보 경로 (추측 금지, 실데이터 기반: 1-A)
  출력  = 엄격한 JSON (자유 YAML 아님: 2-A). 우리가 그걸로 YAML(spec dict) 조립.
  범위  = fields 매핑 + record 배열 경로만. URL/pagination/source_type 은 규칙·probe 가 결정(3-A).
LLM 호출은 build_messages()/parse_response() 로 분리 → 키 없이 프롬프트·파싱·조립을 테스트.
"""
from __future__ import annotations
import json
import re

# LLM 이 채울 표준 컬럼 (우리 스키마가 기대하는 목적지)
TARGET_FIELDS = [
    ("job_id", "공고 고유 ID (필수)"),
    ("title", "공고 제목 (필수)"),
    ("updated_at", "수정/등록 일시 (있으면)"),
    ("deadline", "마감 일시 (있으면)"),
    ("location", "근무지 (있으면)"),
    ("apply_url", "지원/상세 링크 - API 응답에 http로 시작하는 완전한 URL이 있을 때만 키 경로 기입. ID만 있으면 비워 두고 apply_url_template 을 사용."),
    ("description", "본문 (목록 응답에 있으면; 여러 조각이면 배열로)"),
    ("is_closed", "마감 여부 bool (신뢰할 수 있는 필드가 있을 때만)"),
]

SYSTEM = (
    "너는 채용 API 응답을 보고 필드 매핑을 정하는 도구다. "
    "주어진 JSON 샘플의 '실제 키'만 사용한다. 없는 필드는 비워 둔다(추측 금지). "
    "점 표기법(a.b)과 배열맵(arr[].field)을 쓸 수 있다. "
    "반드시 지정된 JSON 형식으로만 답한다."
)

# 기대 출력 스키마 (LLM 에게 보여주는 형태)
OUTPUT_SCHEMA = {
    "record_path": "응답에서 공고 '배열'이 있는 경로 (예: jobList). 최상위가 배열이면 빈 문자열.",
    "fields": {"job_id": "<샘플 속 실제 키 경로>", "title": "<...>", "...": "필요한 것만"},
    "apply_url_template": "공고 상세 URL 패턴. API가 완전한 URL 직접 반환이면 빈 문자열. job_id 등으로 조합 필요하면 API URL 도메인 기준으로 추론 (예: 'https://careers.kakao.com/jobs/{job_id}'). 알 수 없으면 빈 문자열.",
    "reason": "각 매핑을 그 키로 고른 1~2문장 근거",
}


def build_messages(state: dict) -> list:
    """probe 의 api_sample 을 넣어 LLM 메시지 구성."""
    probe = state.get("probe", {})
    sample = probe.get("api_sample")
    api_url = probe.get("api_url", "")
    target = "\n".join(f"  - {k}: {desc}" for k, desc in TARGET_FIELDS)
    user = (
        f"회사: {state.get('company')}\n"
        f"API URL: {api_url}\n\n"
        f"[API 응답 샘플 (배열은 앞 2건만)]\n```json\n"
        f"{json.dumps(sample, ensure_ascii=False, indent=2)}\n```\n\n"
        f"[채울 표준 컬럼] (실제 키가 있는 것만 매핑)\n{target}\n\n"
        f"[apply_url_template 작성 방법]\n"
        f"  - 샘플 레코드에 'http'로 시작하는 완전한 URL 필드가 있으면 fields.apply_url 에 그 키 경로를 쓰고, apply_url_template 은 빈 문자열('').\n"
        f"  - URL이 없고 ID만 있으면: API URL({api_url!r})의 도메인을 기준으로 상세 경로를 추론해 템플릿 구성.\n"
        f"    예) API URL이 'https://careers.example.com/api/jobs' 이고 id 키가 있으면 → 'https://careers.example.com/jobs/{{job_id}}'\n"
        f"  - 패턴을 전혀 알 수 없으면 빈 문자열('').\n\n"
        f"[출력 형식] 아래 JSON 만 출력 (설명·마크다운 금지)\n"
        f"```json\n{json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2)}\n```"
    )
    # 페이지네이션 보강 컨텍스트: 재시도 시 pagination issue 가 있으면 힌트 추가
    if state.get("_pagination_retry"):
        pg_ctx_parts = []
        if probe.get("api_post_body"):
            pg_ctx_parts.append(f"POST body: {json.dumps(probe['api_post_body'], ensure_ascii=False)}")
        if probe.get("api_url"):
            pg_ctx_parts.append(f"URL: {probe['api_url']}")
        if pg_ctx_parts:
            user += (
                "\n\n[페이지네이션 분석 필요]\n"
                "이 API는 페이지네이션이 필요하지만 자동 탐지에 실패했다. "
                "요청 파라미터에서 offset/page/start 등의 페이지네이션 파라미터를 찾아 "
                "record_path 외에 pagination 정보도 함께 알려달라.\n" +
                "\n".join(pg_ctx_parts)
            )
    return [{"role": "user", "content": user}]


def parse_response(text: str) -> dict:
    """LLM 텍스트에서 JSON 추출. ```json 펜스·일반 펜스·순수 JSON 모두 대응."""
    s = text.strip()
    # 1순위: ```json ... ``` 또는 ``` ... ``` 블록 안의 JSON 객체
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    # 2순위: 가장 바깥쪽 { } 쌍 (앞뒤에 설명 텍스트가 붙어도 처리)
    start = s.find("{")
    end = s.rfind("}") + 1
    if start != -1 and end > start:
        return json.loads(s[start:end])
    return json.loads(s)


def assemble_spec(state: dict, parsed: dict) -> dict:
    """LLM 의 fields/record_path + probe·규칙이 정한 나머지 → 완전한 spec(dict) 조립.
    URL/source_type/pagination/method 는 LLM 이 아니라 여기서 결정(3-A)."""
    probe = state.get("probe", {})
    fields = {k: v for k, v in (parsed.get("fields") or {}).items() if v}
    body = probe.get("api_post_body")            # playwright 가 POST API 를 발견한 경우

    is_next_data = probe.get("is_next_data", False)
    listspec: dict = {
        "url": probe.get("api_url"),
        "response_path": parsed.get("record_path", ""),
    }
    if is_next_data:
        # embedded_json: __NEXT_DATA__ 스크립트에서 직접 파싱 (HTTP method 불필요)
        listspec["script_id"] = "__NEXT_DATA__"
    else:
        listspec["method"] = "POST" if body is not None else "GET"
    if body is not None:
        listspec["body"] = body
        listspec["headers"] = {"Content-Type": "application/json"}

    spec: dict = {
        "company": state.get("company"),
        "source_type": state.get("source_type", "json"),
        "crawler": "declarative",
        "list": listspec,
        "pagination": probe.get("pagination_hint") or {"type": "none"},
        "required": [c for c in ("job_id", "title") if c in fields],
        "fields": fields,
    }

    # apply_url 처리:
    # - LLM 이 apply_url_template 을 제공 → spec.apply_url.template 로 설정, fields 에서 중복 제거
    # - LLM 이 fields.apply_url 에 직접 URL 키 경로를 매핑 → 그대로 유지 (엔진이 직접 URL 로 처리)
    tpl = (parsed.get("apply_url_template") or "").strip()
    if tpl:
        fields.pop("apply_url", None)
        spec["apply_url"] = {"template": tpl}

    return spec


# ===== discover_detail: 상세 페이지 본문 셀렉터 LLM 탐지 =====
SYSTEM_DETAIL = (
    "너는 채용공고 상세 HTML에서 '공고 본문(업무 소개·자격요건·우대사항 등)' 영역의 CSS 셀렉터를 찾는 도구다. "
    "주어진 후보 요소 목록에서 실제 공고 내용이 담긴 요소 하나를 고른다. "
    "반드시 지정된 JSON 형식으로만 답한다."
)


def build_selector_messages(candidates: list) -> list:
    """상세 페이지 후보 요소 목록을 LLM에 보내 본문 셀렉터를 선택하게 한다."""
    items = "\n".join(
        f"{i+1}. 셀렉터: {c['selector']}\n   미리보기: {c['preview'][:200]}"
        for i, c in enumerate(candidates)
    )
    user = (
        f"[후보 HTML 요소 목록]\n{items}\n\n"
        f"[출력 형식] JSON만 출력:\n"
        f'{{"selector": "<위 후보 중 공고 본문에 해당하는 셀렉터>", "reason": "선택 이유"}}'
    )
    return [{"role": "user", "content": user}]


# ===== verify 2층 (LLM 의미 판정) =====
SYSTEM_VERIFY = (
    "너는 수집된 채용공고 레코드의 필드 매핑이 '의미상' 맞는지 점검하는 검수자다. "
    "명백히 틀린 것만 지적한다(애매하면 통과). 반드시 지정된 JSON 으로만 답한다."
)

def build_verify_messages(sample: dict) -> list:
    import json as _json
    checklist = (
        "- title: 실제 '공고 제목'인가 (회사명/직무분류/지역이 아니라)\n"
        "- location: '근무지/지역'인가 (제목이나 다른 값이 잘못 들어오지 않았나)\n"
        "- deadline: 마감 '일시'인가\n"
        "- job_id: 고유 식별자처럼 보이나\n"
        "- description: 비어있지 않다면 본문처럼 보이나"
    )
    user = (
        f"[수집된 레코드 1건]\n```json\n{_json.dumps(sample, ensure_ascii=False, indent=2)[:1500]}\n```\n\n"
        f"[점검 항목] 명백히 틀린 필드만 골라라\n{checklist}\n\n"
        f'[출력 형식] JSON 만: {{"ok": true/false, "wrong": ["필드명: 왜 틀렸는지"], "reason": "요약"}}'
    )
    return [{"role": "user", "content": user}]
