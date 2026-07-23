"""JSON API 응답에서 각 필드가 몇 개 고유한지 + 본문 후보를 찾는 진단 도구.
  python inspect_fields.py "<API URL>" [list경로]
job_id 로 쓸 '고유' 필드, 본문(긴 텍스트) 필드를 눈으로 확인.
"""
import sys, json, httpx


def resolve(obj, path):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def main(url, list_path="data.list"):
    r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0",
                                "Referer": url.split("/api/")[0]}, timeout=20,
                  follow_redirects=True)
    lst = resolve(r.json(), list_path)
    if not isinstance(lst, list) or not lst:
        print("list 못 찾음. 경로 확인:", list_path); return
    n = len(lst)
    print(f"status {r.status_code} | {n}건\n")

    # 고유도 100%인 필드 = job_id 후보
    print("=== 고유 필드 (job_id 후보) ===")
    keys = lst[0].keys()
    for k in keys:
        try:
            vals = [json.dumps(x.get(k), ensure_ascii=False) for x in lst]
        except Exception:
            continue
        uniq = len(set(vals))
        if uniq == n and n > 1:
            print(f"  {k}: 고유 {uniq}/{n}  (샘플 {vals[0][:30]})")

    # 긴 텍스트 필드 = 본문 후보
    print("\n=== 긴 텍스트 필드 (본문 후보) ===")
    for k in keys:
        v = lst[0].get(k)
        if isinstance(v, str) and len(v) > 100:
            print(f"  {k}: {len(v)}자  ({v[:60]}...)")

    # 제목 후보
    print("\n=== 제목 후보 (Nm/title/name 류) ===")
    for k in keys:
        if any(w in k.lower() for w in ("nm", "title", "name", "subject")):
            v = lst[0].get(k)
            if isinstance(v, str) and v:
                print(f"  {k}: {v[:50]}")


if __name__ == "__main__":
    url = sys.argv[1]
    lp = sys.argv[2] if len(sys.argv) > 2 else "data.list"
    main(url, lp)
