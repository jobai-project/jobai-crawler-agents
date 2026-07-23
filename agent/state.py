"""에이전트 상태 정의.
그래프 전체를 흐르는 단일 딕셔너리. 각 노드가 일부를 읽고 채운다.
'무엇이 채워졌나'로 다음 노드가 정해진다 (ReAct의 자유 판단과 대비).
"""
from __future__ import annotations
from typing import TypedDict, Optional, Literal


class AgentState(TypedDict, total=False):
    # --- 입력 (사람이 줌) ---
    company: str                  # 소문자 ASCII 식별자
    company_name_ko: str
    entry_url: str

    # --- probe 노드가 채움 (결정적 탐침) ---
    probe: dict                   # {status, content_type, body_sample, signatures, endpoints, api_url, api_sample...}

    # --- classify 노드가 채움 ---
    crawler: Literal["declarative", "playwright", "custom"]
    source_type: Literal["json", "html"]
    ats: Optional[str]            # "greenhouse" 등. 아니면 None
    ats_board: Optional[str]      # ATS 보드 토큰 (greenhouse 등)
    # (probe 가 greeting_url 등 부가 정보를 probe dict 에 담음)
    classify_reason: str
    map_reason: Optional[str]      # LLM이 필드 매핑을 그렇게 고른 근거
    llm_cost: dict                # 누적 LLM 토큰/요금 {input_tokens,output_tokens,usd,krw,calls}

    # --- map 노드가 채움 (LLM 또는 ATS 템플릿) ---
    spec: dict                    # 작성된 YAML 명세 (dict 형태)

    # --- verify 노드가 채움 (엔진 실행) ---
    records_sample: list          # 수집된 레코드 일부
    record_count: int
    issues: list                  # check() 결과 (빈 리스트면 통과)
    verified: bool

    # --- 루프 제어 ---
    attempts: int                 # map→verify 반복 횟수
    max_attempts: int             # 상한 (기본 3)

    # --- 종료 ---
    needs_input: bool             # SPA API 자동발견 실패 → 사람이 API URL 제공(Mode B)
    status: Literal["done", "needs_human", "needs_input"]
    save_dir: Optional[str]        # 설정 시 finalize 가 {save_dir}/{회사}.yaml 저장
    saved_path: Optional[str]     # 저장된 YAML 경로
    final_spec: Optional[dict]
    handoff_reason: Optional[str] # custom/needs_human 사유
