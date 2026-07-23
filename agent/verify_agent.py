"""에이전트를 URL로 끝까지 돌려보고 결과를 요약하는 점검 도구.
  python -m agent.verify_agent <company> <entry_url>
예:
  python -m agent.verify_agent coupang  https://www.coupang.jobs/kr/jobs/
  python -m agent.verify_agent leverdemo https://jobs.lever.co/leverdemo
  python -m agent.verify_agent kakaopay https://kakaopay.career.greetinghr.com/ko/main
"""
import sys, json
from agent.graph import run


def main(company, url, save_dir=None):
    init = {"company": company, "entry_url": url}
    if save_dir:
        init["save_dir"] = save_dir
    s = run(init, trace=True)
    print(f"[{company}] {url}")
    print("  경로 :", " → ".join(s.get("_path", [])))
    print("  분류 :", s.get("classify_reason"))
    print("  ATS  :", s.get("ats"), "| board:", s.get("ats_board"),
          "| crawler:", s.get("crawler"), "| source:", s.get("source_type"))
    print("  상태 :", s.get("status"))
    c = s.get("llm_cost")
    if c:
        print(f"  비용 : LLM {c.get('calls',1)}회 | 입력 {c['input_tokens']} + 출력 {c['output_tokens']} 토큰 "
              f"| ${c['usd']:.6f} (≈ {c['krw']:.1f}원)")
    else:
        print("  비용 : LLM 미사용 (0원) — ATS 템플릿/캐시 경로")
    if s.get("saved_path"):
        print("  저장 :", s["saved_path"], "→ 이후 `python -m collector.run", s["saved_path"], "`")
    if s.get("status") == "done":
        print("  수집 :", s.get("record_count"), "건 | 점검:",
              "통과" if s.get("verified") else s.get("issues"))
        sample = (s.get("records_sample") or [None])[0]
        if sample:
            r = dict(sample)
            if r.get("description"):
                r["description"] = str(r["description"])[:140] + " ...(생략)"
            print("  샘플 :", json.dumps(r, ensure_ascii=False)[:500])
    else:
        print("  사유 :", s.get("handoff_reason"))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("사용법: python -m agent.verify_agent <company> <entry_url>")
    else:
        save = "examples" if "--save" in sys.argv else None
        main(sys.argv[1], sys.argv[2], save_dir=save)
