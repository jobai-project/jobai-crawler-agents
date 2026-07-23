"""CLI.
  실제 수집:    python -m collector.run examples\\kakao.yaml
  오프라인 검증: python -m collector.run --selftest   (네트워크/pyyaml 불필요)
"""
import sys
import json

from collector.engine import collect, check, resolve, map_record_json


def run_yaml(path, out_path=None):
    import yaml
    with open(path, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    print(f"[수집] {spec.get('company')} ({spec.get('company_name_ko')}) "
          f"crawler={spec.get('crawler')} source={spec.get('source_type')}")
    records = collect(spec)
    print(f"[결과] {len(records)}건 수집")
    issues = check(records, spec)
    if issues:
        print("[점검 실패]")
        for i in issues:
            print("  -", i)
    else:
        print("[점검 통과] 1층 결정적 체크 OK")
    if records:
        print("[전체 요약]")
        print(f"  {'job_id':<10} {'closed':<6} title")
        for r in records:
            jid = str(r.get("job_id"))[:10]
            closed = str(r.get("is_closed"))
            title = str(r.get("title"))[:46]
            print(f"  {jid:<10} {closed:<6} {title}")
        print("[샘플 1건]")
        print(json.dumps(records[0], ensure_ascii=False, indent=2))
    if out_path:
        import os
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"company": spec.get("company"), "count": len(records),
                       "records": records}, f, ensure_ascii=False, indent=2)
        with_desc = sum(1 for r in records if r.get("description"))
        print(f"[저장] {out_path} ({len(records)}건, 본문 있는 공고 {with_desc}건)")


def selftest():
    """가짜 카카오 응답으로 JSON 경로/[].필드/매핑/점검 로직 검증."""
    fake = {
        "totalPage": 1,
        "jobList": [
            {"realId": "P-1", "jobOfferTitle": "백엔드 개발자", "uptDate": "2026-05-01",
             "workContentDesc": "서버 개발", "qualification": "Java", "closeFlag": False,
             "locationName": "판교",
             "skillSetList": [{"skillSetName": "Server"}, {"skillSetName": "Kotlin"}]},
            {"realId": "P-2", "jobOfferTitle": "프론트 개발자", "uptDate": "2026-05-02",
             "workContentDesc": "웹 개발", "qualification": "TS", "closeFlag": True,
             "locationName": "판교", "skillSetList": [{"skillSetName": "Web"}]},
        ],
    }
    spec = {
        "fields": {"job_id": "realId", "title": "jobOfferTitle", "updated_at": "uptDate",
                   "description": "workContentDesc", "is_closed": "closeFlag",
                   "location": "locationName"},
        "apply_url": {"template": "https://careers.kakao.com/jobs/{job_id}"},
        "extra": {"skill_sets": "skillSetList[].skillSetName"},
    }
    recs = [map_record_json(r, spec) for r in resolve(fake, "jobList")]
    assert recs[0]["job_id"] == "P-1"
    assert recs[0]["apply_url"] == "https://careers.kakao.com/jobs/P-1"
    assert recs[0]["extra"]["skill_sets"] == ["Server", "Kotlin"]
    assert recs[1]["is_closed"] is True
    assert check(recs, spec) == []
    print("selftest OK — JSON 경로 / [].필드 / 매핑 / 점검 정상")
    print(json.dumps(recs[0], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        selftest()
    elif len(sys.argv) >= 2:
        # --out output\회사.json 으로 수집 결과(상세 포함) 저장
        out_path = None
        if "--out" in sys.argv:
            i = sys.argv.index("--out")
            out_path = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        run_yaml(sys.argv[1], out_path)
    else:
        print("사용법: python -m collector.run <yaml경로> [--out 결과.json]  |  python -m collector.run --selftest")
