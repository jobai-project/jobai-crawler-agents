"""목록→상세까지 한 회사를 끝까지 돌려보고 상태를 점검하는 검증 러너.
  python -m collector.verify_run examples\\naver.yaml
  python -m collector.verify_run examples\\naver.yaml 3      # 상세는 앞 3건만(빠르게)
"""
import sys, json, yaml
from collector.engine import collect, check, collect_json, collect_html, collect_embedded_json
from collector.engine import _apply_filter


def main(path, detail_limit=None):
    spec = yaml.safe_load(open(path, encoding="utf-8"))
    has_detail = bool((spec.get("detail") or {}).get("enabled"))
    print(f"[{spec.get('company')}] source={spec.get('source_type')} "
          f"detail={'ON' if has_detail else 'off'}")

    # 1) 목록만 먼저 (상세 끄고) — 목록 단계 단독 점검
    import httpx
    client = httpx.Client(headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True)
    st = spec.get("source_type")
    if st == "json":
        listing = collect_json(spec, client)
    elif st == "html":
        listing = collect_html(spec, client)
    else:
        listing = collect_embedded_json(spec, client)
    listing = _apply_filter(listing, spec)
    print(f"\n[목록] {len(listing)}건 수집")
    issues = check(listing, spec)
    print("[목록 점검]", "통과" if not issues else issues)
    # 목록 단계 본문 유무
    with_desc = sum(1 for r in listing if r.get("description"))
    print(f"[목록 본문] description 있는 공고: {with_desc}/{len(listing)}")

    if not has_detail:
        print("\n(이 회사는 목록에 본문 포함 → 상세 단계 없음)")
        client.close()
        _sample(listing)
        return

    # 2) 상세까지 (detail_limit 만큼만)
    targets = listing if detail_limit is None else listing[:detail_limit]
    print(f"\n[상세] {len(targets)}건에 대해 상세 호출...")
    from collector.engine import fetch_detail
    ok = empty = err = 0
    for r in targets:
        before = r.get("description")
        try:
            fetch_detail(r, spec, client)
        except Exception as e:
            r["detail_error"] = str(e)
        if r.get("detail_error"):
            err += 1
        elif r.get("description"):
            ok += 1
        else:
            empty += 1
    client.close()
    print(f"[상세 결과] 본문 채움 {ok} / 비어있음 {empty} / 에러 {err}")
    secs = [len(r.get("sections") or {}) for r in targets if r.get("sections")]
    if secs:
        print(f"[상세 섹션] 섹션 추출된 공고 {len(secs)}건, 평균 섹션 수 {sum(secs)//len(secs)}")
    _sample(targets)


def _sample(records):
    if not records:
        print("\n(샘플 없음)")
        return
    r = dict(records[0])
    if r.get("description"):
        r["description"] = r["description"][:160] + " ...(생략)"
    if r.get("sections"):
        r["sections"] = {k: (v[:60] + "...") for k, v in list(r["sections"].items())[:4]}
    print("\n[샘플 1건]")
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python -m collector.verify_run <yaml> [상세건수제한]")
    else:
        lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
        main(sys.argv[1], lim)
