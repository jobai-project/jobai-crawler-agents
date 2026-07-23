"""LangGraph 그래프 조립 + 실행 래퍼.

그래프 구조:
  probe → classify ─┬→ ats_template ─┐
                    ├→ map_spec      ├→ discover_detail → verify ─┬→ finalize → END
                    └→ to_human ────┘                             ├→ map_spec  (재시도)
                                                                  └→ to_human → END

조건부 엣지:
  classify       → route_after_classify → {ats_template | map_spec | to_human}
  verify         → route_after_verify   → {finalize | map_spec | to_human}

discover_detail: description 이 없으면 상세 페이지 1건을 가져와 본문 셀렉터를 탐지.
                 이미 있거나 탐지 실패 시 spec 변경 없이 그냥 통과.
"""
from __future__ import annotations
from langgraph.graph import StateGraph, END
from agent.state import AgentState
from agent import nodes


def _build() -> StateGraph:
    b = StateGraph(AgentState)

    # 노드 등록
    for name, fn in [
        ("probe",           nodes.probe),
        ("classify",        nodes.classify),
        ("ats_template",    nodes.ats_template),
        ("map_spec",        nodes.map_spec),
        ("discover_detail", nodes.discover_detail),
        ("verify",          nodes.verify),
        ("finalize",        nodes.finalize),
        ("to_human",        nodes.to_human),
    ]:
        b.add_node(name, fn)

    # 진입점
    b.set_entry_point("probe")

    # 고정 엣지
    b.add_edge("probe",           "classify")
    b.add_edge("ats_template",    "discover_detail")
    b.add_edge("map_spec",        "discover_detail")
    b.add_edge("discover_detail", "verify")
    b.add_edge("finalize",        END)
    b.add_edge("to_human",        END)

    # 조건부 엣지
    b.add_conditional_edges(
        "classify",
        nodes.route_after_classify,
        {"ats_template": "ats_template", "map_spec": "map_spec", "to_human": "to_human"},
    )
    b.add_conditional_edges(
        "verify",
        nodes.route_after_verify,
        {"finalize": "finalize", "map_spec": "map_spec", "to_human": "to_human"},
    )

    return b


app = _build().compile()


def run(initial: dict, trace: bool = False) -> dict:
    """그래프를 실행하고 최종 state 를 반환. verify_agent.py 호환 인터페이스.

    trace=True 이면 실행된 노드 순서를 state['_path'] 에 기록.
    stream_mode='updates' 로 청크를 받아 경로를 수집하면서 state 를 누적한다.
    """
    init = dict(initial)
    init.setdefault("attempts", 0)
    init.setdefault("max_attempts", 3)

    if not trace:
        return dict(app.invoke(init))

    path: list[str] = []
    state: dict = dict(init)
    for chunk in app.stream(init, stream_mode="updates"):
        # chunk = {node_name: partial_state_update}
        for node_name, partial in chunk.items():
            path.append(node_name)
            if isinstance(partial, dict):
                state.update(partial)
    state["_path"] = path
    return state
