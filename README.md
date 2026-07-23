# crawler-agents

채용공고 수집을 위한 **선언적 엔진 + AI 분석 에이전트**.

회사 URL 하나를 주면 에이전트가 사이트를 자동 분석해 수집 명세(YAML)를 만들고, 엔진이 그 명세로 공고를 수집한다.

---

## 구조

```
crawler-agents/
├── agent/               # AI 분석 에이전트 (LangGraph + Claude)
│   ├── graph.py         # LangGraph 파이프라인 조립
│   ├── nodes.py         # 각 노드 구현 (probe / classify / map_spec / ...)
│   ├── probe.py         # 사이트 HTTP 탐침 (LLM 0개)
│   ├── mapper.py        # LLM 필드 매핑 + 프롬프트
│   ├── state.py         # AgentState 타입
│   ├── cost.py          # LLM 비용 계산
│   ├── browser_probe.py # Playwright 브라우저 탐침 (선택)
│   └── verify_agent.py  # CLI 진입점
├── collector/           # 선언적 수집 엔진 (LLM 0개)
│   ├── engine.py        # JSON / HTML / embedded_json 수집·매핑
│   ├── run.py           # CLI 진입점
│   └── verify_run.py    # 엔진 단독 검증
├── examples/            # 회사별 수집 명세 YAML (자동 생성)
│   ├── kakao.yaml
│   ├── naver.yaml
│   └── ...              # 23개 회사
├── .env.example
└── requirements.txt
```

---

## 파이프라인

에이전트는 LangGraph StateGraph로 구성되며 아래 순서로 실행된다.

```
probe → classify ─┬→ ats_template ─┐
                  ├→ map_spec      ├→ discover_detail → verify ─┬→ finalize → END
                  └→ to_human ────┘                             ├→ map_spec  (재시도, 최대 3회)
                                                                └→ to_human → END
```

| 노드 | 역할 | LLM |
|------|------|-----|
| `probe` | URL을 HTTP로 탐침, ATS/API/마커 탐지 | 없음 |
| `classify` | JSON/HTML/embedded_json/ATS 분류 | 없음 |
| `ats_template` | Lever·Greenhouse·Greeting 등 ATS 템플릿 즉시 생성 | 없음 |
| `map_spec` | API 응답 샘플로 필드 매핑 결정 | Claude Haiku |
| `discover_detail` | 상세 페이지 수집 설정 자동 추가 | 없음 |
| `verify` | 실제 수집 후 필드·중복·의미 점검 | Claude Haiku (의미 점검) |
| `finalize` | YAML 저장 | 없음 |
| `to_human` | 자동화 불가 → 사람에게 핸드오프 | 없음 |

### 지원하는 사이트 유형

| 유형 | 설명 | 예시 |
|------|------|------|
| `json` | REST JSON API | 카카오, 쿠팡, 네이버 |
| `embedded_json` | Next.js `__NEXT_DATA__` SSG | 에이블리, 당근 |
| `html` | CSS 셀렉터 기반 HTML 파싱 | - |
| `ats:lever` | Lever 공개 API | - |
| `ats:greenhouse` | Greenhouse 공개 API | - |
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

> 엔진(`collector/`)만 쓸 때는 API 키 불필요.

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

### 엔진: YAML로 직접 수집

```bash
# YAML 명세로 수집
python -m collector.run examples/kakao.yaml

# 수집 결과 검증
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
  is_closed: status        # 값 변환은 스프링 서비스 레이어에서 처리

# apply_url 이 없고 ID만 있는 경우
apply_url:
  template: "https://example.com/jobs/{job_id}"

# 공고 상세 본문 수집 (선택)
detail:
  enabled: true
  source_type: html
  url_from: apply_url
  body_selector: body      # CSS 셀렉터
```

---

## 비용

에이전트 1회 실행 기준 (Claude Haiku 사용):

| 경로 | LLM 호출 | 비용 (약) |
|------|----------|-----------|
| ATS 템플릿 (Lever/Greenhouse/Greeting) | 0회 | 0원 |
| 자체 JSON API (첫 시도 성공) | 1~2회 | 1~3원 |
| 재시도 포함 최대 | 최대 6회 | ~10원 |
