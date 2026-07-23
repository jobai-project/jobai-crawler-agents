"""SPA 가 부르는 모든 JSON API 를 나열 (playwright 진단용).
  python inspect_apis.py <URL>
어떤 응답이 '진짜 공고 목록'인지 눈으로 확인하는 도구.
"""
import sys, json
from playwright.sync_api import sync_playwright


def main(url):
    seen = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(user_agent="Mozilla/5.0")
        def on(r):
            ct = (r.headers or {}).get("content-type", "")
            if "json" in ct.lower():
                try:
                    d = r.json()
                except Exception:
                    return
                # 배열 길이 추정
                n = ""
                if isinstance(d, list):
                    n = f"[배열 {len(d)}건]"
                elif isinstance(d, dict):
                    for k, v in d.items():
                        if isinstance(v, list):
                            n = f"{k}=[{len(v)}건]"; break
                        if isinstance(v, dict):
                            for k2, v2 in v.items():
                                if isinstance(v2, list):
                                    n = f"{k}.{k2}=[{len(v2)}건]"; break
                            if n: break
                seen.append((r.request.method, r.url, n, json.dumps(d, ensure_ascii=False)[:150]))
        pg.on("response", on)
        try:
            pg.goto(url, wait_until="networkidle", timeout=25000)
        except Exception as e:
            print("goto 경고:", e)
        b.close()
    print(f"\n=== JSON 응답 {len(seen)}개 ===")
    for i, (m, u, n, s) in enumerate(seen):
        print(f"\n[{i}] {m} {u[:90]}")
        print(f"    {n}")
        print(f"    {s}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "https://careers.nhn.com/recruits")
