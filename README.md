# crawler-agents

채용공고 수집을 위한 **선언적 엔진 + AI 분석 에이전트**.

회사 URL 하나를 주면 에이전트가 사이트를 자동 분석해 수집 명세(YAML)를 만들고, 엔진이 그 명세로 공고를 수집한다.

---

## 아키텍처

| 구성 | 디렉토리 | LLM | 용도 |
|------|----------|-----|------|
| **에이전트** | `agent/` | Claude Haiku 4.5 | URL → YAML 명세 자동 생성 |
| **검증 엔진** | `collector/` | 없음 | YAML 명세가 정상 동작하는지 사전 검증 |

> **실제 수집은 Spring 서비스에서 수행한다.**
> `collector/`는 스프링 엔진에 YAML을 넘기기 전, 명세가 올바른지 미리 확인하기 위한 Python 프로토타입이다.
> 에이전트의 `verify` 노드도 이 검증 엔진을 내부적으로 호출해 명세를 점검한다.

---

## 구조

```
crawler-agents/
├── agent/               # AI 분석 에이전트 (LangGraph + Claude)
│   ├── graph.py         # LangGraph 파이프라인 조립
│   ├── nodes.py         # 각 노드 구현 (probe / classify / map_spec / ...)
│   ├── probe.py         # 사이트 HTTP 탐침 (LLM 0개, 4단계 전략)
│   ├── mapper.py        # LLM 필드 매핑 + 프롬프트
│   ├── state.py         # AgentState 타입
│   ├── cost.py          # LLM 비용 계산
│   ├── browser_probe.py # Playwright 브라우저 탐침 (SPA 최후 수단)
│   └── verify_agent.py  # CLI 진입점
├── collector/           # 검증용 수집 엔진 (LLM 0개, 스프링 투입 전 사전 검증)
│   ├── engine.py        # JSON / HTML / embedded_json 수집·매핑
│   ├── run.py           # CLI 진입점
│   └── verify_run.py    # 명세 단독 검증
├── examples/            # 회사별 수집 명세 YAML (자동 생성)
│   ├── kakao.yaml
│   ├── naver.yaml
│   └── ...              # 22개 회사 + 1개 템플릿(greeting_template)
├── inspect_apis.py      # API 분석 유틸리티
├── inspect_fields.py    # 필드 분석 유틸리티
├── .env.example
└── requirements.txt
```

---

## 파이프라인

에이전트는 LangGraph StateGraph로 구성되며 아래 순서로 실행된다.

<img width="791" height="271" alt="그림1" src="https://github.com/user-attachments/assets/806cb3b6-adb1-4907-a917-63959fa059d3" />

### 조건 분기

- **classify 이후** (`route_after_classify`):
  - `custom` 또는 `needs_input` → `to_human`
  - ATS 감지 → `ats_template`
  - 그 외 → `map_spec`

- **verify 이후** (`route_after_verify`):
  - `verified=True` → `finalize`
  - `fetch_blocked=True` 또는 `attempts >= max_attempts` → `to_human`
  - 그 외 → `map_spec` (재시도, 직전 issues를 프롬프트에 피드백)

### 노드 상세

| 노드 | 역할 | LLM |
|------|------|-----|
| `probe` | 4단계 HTTP 탐침: 직접 탐색 → ATS API 확인 → Greeting 판정 → SPA 숨은 API 발견 | 없음 |
| `classify` | 규칙 기반 결정 트리로 JSON/HTML/embedded_json/ATS 분류 | 없음 |
| `ats_template` | Lever·Greenhouse·Greeting 등 ATS 템플릿에 보드 토큰 삽입하여 즉시 생성 | 없음 |
| `map_spec` | API 응답 샘플로 필드 매핑 결정 (재시도 시 직전 issues 피드백) | Claude Haiku 4.5 |
| `discover_detail` | description이 목록 API에 없으면 상세 페이지 수집 설정 자동 추가 | 없음 |
| `verify` | 실제 수집 후 3층 점검 (아래 참조) | Claude Haiku 4.5 (2층만) |
| `finalize` | YAML 저장 | 없음 |
| `to_human` | 자동화 불가 → 사람에게 핸드오프 | 없음 |

### probe 4단계 탐침 전략

| 단계 | 동작 | 비고 |
|------|------|------|
| 1 | entry_url 직접 HTTP 요청 | JSON 응답이면 즉시 api_sample 확보, HTML이면 ATS 시그니처·마커 탐지 |
| 2 | ATS 공개 API 확인 | Greenhouse(`boards-api.greenhouse.io/{token}/jobs`), Lever(`api.lever.co/v0/postings/{token}`) 보드 토큰 추출 |
| 3 | Greeting 특수 판정 | 도메인 또는 `__NEXT_DATA__`에 "openings" → `ats=greeting`, 경로 후보 시도 |
| 4 | SPA 숨겨진 API 발견 | 추정 경로 시도 → `__NEXT_DATA__` 임베드 추출 → Playwright 브라우저 탐침 (최후 수단) |

### verify 3층 점검

| 층 | 점검 내용 | LLM |
|----|----------|-----|
| **1층 (형식)** | 필수 필드 비어있는지, job_id 중복, 0건 수집 | 없음 |
| **2층 (의미)** | title이 실제 직무명인지, location이 지역인지, deadline이 날짜인지, job_id가 고유한지 | Claude Haiku 4.5 |
| **3층 (페이지네이션)** | total 대비 수집 건수 완전성 체크 | 없음 |

> ATS 템플릿 경로는 이미 검증된 스키마이므로 2층(의미 점검)을 건너뛴다.

### classify 결정 트리

LLM 없이 규칙 기반으로 분류한다:

1. ATS가 probe에서 확인 → `declarative` + `json` + ATS 템플릿
2. blocked (4xx/5xx & 단어 0개) → `custom`
3. SPA & API 자동 발견 실패 → `json` + `needs_input` (Mode B)
4. `__NEXT_DATA__` 직접 추출 성공 → `embedded_json`
5. `__NEXT_DATA__` 있고 API 없음 → `embedded_json`
6. JSON content-type 또는 endpoint 발견 → `declarative` + `json`
7. HTML 200 → `declarative` + `html`
8. 그 외 → `custom`

### 지원하는 사이트 유형

| 유형 | 설명 | 예시 |
|------|------|------|
| `json` | REST JSON API | 카카오, 쿠팡, 네이버 |
| `embedded_json` | Next.js `__NEXT_DATA__` SSG | 에이블리, 당근 |
| `html` | CSS 셀렉터 기반 HTML 파싱 | - |
| `ats:lever` | Lever 공개 API | - |
| `ats:greenhouse` | Greenhouse 공개 API | 쿠팡 |
| `ats:greeting` | Greeting HR | 카카오페이, 버킷플레이스 |

---

## 세팅

### 1. Python 환경

```bash
python -m venv .venv

# Windows
.\.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. 환경변수

```bash
cp .env.example .env
# .env 파일에 ANTHROPIC_API_KEY 입력
```

```
ANTHROPIC_API_KEY=sk-ant-...
```

> 검증 엔진(`collector/`)만 쓸 때는 API 키 불필요.

---

## 사용법

### 에이전트: URL → YAML 자동 생성

```bash
# 분석 + examples/ 에 YAML 저장
python -m agent.verify_agent <회사명> <URL> --save

# 예시
python -m agent.verify_agent kakao    https://careers.kakao.com/jobs
python -m agent.verify_agent ably     https://ably.team/recruit
python -m agent.verify_agent bucketplace https://www.bucketplace.com/careers/
```

출력 예시:
```
[ably] https://ably.team/recruit
  경로 : probe → classify → map_spec → discover_detail → verify → finalize
  분류 : Next.js __NEXT_DATA__ 임베드 (해시 URL 자동 감지)
  ATS  : None | crawler: declarative | source: embedded_json
  상태 : done
  비용 : LLM 1회 | 입력 1823 + 출력 312 토큰 | $0.000831 (≈ 1.2원)
  저장 : examples/ably.yaml
  수집 : 53건 | 점검: 통과
```

### 검증 엔진: YAML 명세 사전 검증

스프링에 넘기기 전에 명세가 정상 동작하는지 확인한다.

```bash
# YAML 명세로 수집 테스트
python -m collector.run examples/kakao.yaml

# JSON 파일로 출력 저장
python -m collector.run examples/kakao.yaml --out output/kakao.json

# 오프라인 자체 테스트 (네트워크 불필요)
python -m collector.run --selftest

# 수집 결과 검증 (필수 필드·중복·상세 페이지 점검)
python -m collector.verify_run examples/kakao.yaml
```

---

## YAML 명세 스키마

에이전트가 자동 생성하거나 수동으로 작성할 수 있다.

```yaml
company: example
source_type: json          # json | html | embedded_json
crawler: declarative

list:
  url: https://example.com/api/jobs
  method: GET              # GET | POST
  params:                  # GET 파라미터 (선택)
    pageNum: 1
  body:                    # POST 요청 body (선택)
    keyword: ""
  headers:                 # 커스텀 헤더 (선택)
    X-Custom: value
  response_path: data.jobs # 응답에서 공고 배열 위치

pagination:
  type: none               # none | page_number | offset
  # type: page_number
  # param: page
  # start: 1
  # total_pages_path: totalPages

required:
  - job_id
  - title

fields:
  job_id: id
  title: title
  updated_at: createdAt
  deadline: closedAt
  location: workPlace
  apply_url: applyUrl
  description:             # 복수 필드 결합 가능
    - intro
    - desc
  is_closed: status        # 값 변환은 스프링 서비스 레이어에서 처리

# 필수가 아닌 추가 필드 매핑 (선택)
extra:
  skills: skillList[].name # 배열 맵 지원

# apply_url 이 없고 ID만 있는 경우
apply_url:
  template: "https://example.com/jobs/{job_id}"

# 노이즈 제거 (선택)
filter:
  field: title
  exclude_contains:        # 해당 문자열 포함 시 제외
    - "인턴"
    - "Talent Pool"
  in:                      # 특정 값만 포함
    - "개발"

# 특정 문자열을 null로 변환 (선택)
null_values:
  deadline: "2999"         # deadline에 "2999" 포함 시 null 처리

# 공고 상세 본문 수집 (선택)
detail:
  enabled: true
  source_type: html        # html | json | embedded_json
  url_from: apply_url      # 또는 url_template 사용
  # url_template: "https://example.com/jobs/{job_id}"
  body_selector: body      # CSS 셀렉터
  section_box: div.section # 섹션 분리 (선택)
  section_title: h2        # 섹션 제목 셀렉터 (선택)
  fields:                  # 상세 페이지 필드 매핑 (선택)
    description: content
```

---

## 비용

에이전트 1회 실행 기준 (Claude Haiku 4.5 사용):

| 경로 | LLM 호출 | 비용 (약) |
|------|----------|-----------|
| ATS 템플릿 (Lever/Greenhouse/Greeting) | 0회 | 0원 |
| 자체 JSON API (첫 시도 성공) | 1~2회 | 1~3원 |
| 재시도 포함 최대 | 최대 6회 | ~10원 |

### 모델 단가

`cost.py`에 정의된 토큰 단가 (per 1M tokens):

| 모델 | 입력 | 출력 |
|------|------|------|
| `claude-haiku-4-5` (사용 중) | $1.0 | $5.0 |
| `claude-sonnet-4-6` | $3.0 | $15.0 |
| `claude-opus-4-7` | $5.0 | $25.0 |

> 환율 기준: $1 = ₩1,400 (표시용)

---

## 테스트

`AgentState`에 테스트 주입 필드가 있어 LLM 호출 없이 파이프라인을 테스트할 수 있다:

| 필드 | 용도 |
|------|------|
| `_client` | mock HTTP 클라이언트 주입 |
| `_mock_llm` | LLM 응답 텍스트 주입 (map_spec) |
| `_mock_spec` | 완성 spec dict 주입 (map_spec 건너뜀) |
| `_mock_verify` | 검증 결과 주입 (verify LLM 건너뜀) |

```bash
# 엔진 오프라인 자체 테스트
python -m collector.run --selftest
```
