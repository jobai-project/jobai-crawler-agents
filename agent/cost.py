"""LLM 토큰 사용량 → 요금 계산 (map_spec 가 유일한 LLM 사용처).
단가: Claude Haiku 4.5 = 입력 $1 / 출력 $5 per 1M tokens (2026-06 기준, 공식가).
요금이 바뀌면 PRICES 만 고치면 됨.
"""
from __future__ import annotations

# 모델별 (입력단가, 출력단가) per 1,000,000 tokens, USD
PRICES = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (5.0, 25.0),
}
USD_TO_KRW = 1400   # 대략치(표시용). 정확한 환율은 그때그때 다름.


def usage_from_response(resp) -> dict:
    """LangChain AIMessage 에서 토큰 사용량 추출. 형태가 달라도 최대한 견고하게."""
    um = getattr(resp, "usage_metadata", None) or {}
    if um:
        return {"input": um.get("input_tokens", 0), "output": um.get("output_tokens", 0)}
    # 폴백: response_metadata.usage (anthropic 원형)
    meta = getattr(resp, "response_metadata", {}) or {}
    u = meta.get("usage", {})
    return {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0)}


def cost_of(usage: dict, model: str = "claude-haiku-4-5") -> dict:
    pin, pout = PRICES.get(model, PRICES["claude-haiku-4-5"])
    inp, out = usage.get("input", 0), usage.get("output", 0)
    usd = inp / 1_000_000 * pin + out / 1_000_000 * pout
    return {"model": model, "input_tokens": inp, "output_tokens": out,
            "usd": round(usd, 6), "krw": round(usd * USD_TO_KRW, 2)}


def format_cost(c: dict) -> str:
    return (f"[LLM 비용] {c['model']} | 입력 {c['input_tokens']} + 출력 {c['output_tokens']} 토큰 "
            f"| ${c['usd']:.6f} (≈ {c['krw']:.1f}원)")
