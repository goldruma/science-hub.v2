# -*- coding: utf-8 -*-
"""
과학/공학 활동 허브 - 스크레이퍼 오케스트레이터

사용법
------
전체 소스 수집 후 data/notices.json 갱신:
    python main.py

클라우드(GitHub 호스팅 러너)에서 접속 가능한 소스만 수집:
    python main.py --group cloud

집 PC 등 self-hosted 러너 전용 소스만 수집 (해외 IP가 막힌 곳들):
    python main.py --group self_hosted

특정 소스 하나만 테스트(파일 저장 안 함, 콘솔에 결과만 출력):
    python main.py --test kaist_ug

소스 목록 보기:
    python main.py --list
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from html_scraper import scrape_source
from rss_scraper import scrape_rss
from utils import guess_tags, make_uid, normalize_date, dedupe, is_relevant

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "sources.json"
DATA_PATH = ROOT / "data" / "notices.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def load_sources():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["sources"]


def run_one(source: dict) -> list:
    if source.get("type") == "rss":
        raw_items = scrape_rss(source)
    else:
        raw_items = scrape_source(source)

    strict = source.get("strict_filter", False)
    results = []
    for it in raw_items:
        if not is_relevant(it["title"], strict=strict):
            continue
        tags = guess_tags(it["title"], source["default_tags"])
        results.append({
            "uid": make_uid(source["id"], it["title"], it["link"]),
            "source_id": source["id"],
            "org": source["org"],
            "org_type": source["org_type"],
            "title": it["title"],
            "link": it["link"],
            "date": normalize_date(it.get("date_raw", "")),
            "grade": tags["grade"],
            "field": tags["field"],
            "category": tags["category"],
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    return results


def run_all(group: str = "all"):
    sources = load_sources()
    if group != "all":
        sources = [s for s in sources if s.get("run_on", "cloud") == group]
        logger.info("group=%s : 대상 소스 %d개", group, len(sources))
    all_items = []
    ok, failed = 0, []
    sources_status = []

    for source in sources:
        logger.info("수집 중: [%s] %s", source["org"], source["name"])
        try:
            items = run_one(source)
        except Exception as e:  # 한 소스가 죽어도 전체는 계속 진행
            logger.error("실패: %s (%s)", source["id"], e)
            failed.append(source["id"])
            sources_status.append({
                "id": source["id"], "org": source["org"], "org_type": source["org_type"],
                "url": source["notice_url"], "ok": False, "item_count": 0,
            })
            continue
        if not items:
            # 0건인 이유는 (a) 실제로 접속이 막힘(해외 IP 차단 등) (b) selector 불일치
            # (c) 관련성 필터로 전부 걸러짐 중 하나. 원인 구분은 --test 로 확인.
            logger.warning("  -> 0건 (접속 차단/selector 점검 필요할 수 있음: --test %s)", source["id"])
            failed.append(source["id"])
        else:
            logger.info("  -> %d건", len(items))
            ok += 1
        sources_status.append({
            "id": source["id"], "org": source["org"], "org_type": source["org_type"],
            "url": source["notice_url"], "ok": len(items) > 0, "item_count": len(items),
        })
        all_items.extend(items)

    # 기존 데이터와 합쳐서 중복 제거(사이트가 지난 공지를 리스트에서 내려도
    # 최근 며칠간 모은 데이터는 남도록 seed/이전 회차 데이터를 보존)
    existing = []
    existing_status = []
    if DATA_PATH.exists():
        try:
            existing_data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            existing = existing_data.get("items", [])
            existing_status = existing_data.get("sources_status", [])
        except json.JSONDecodeError:
            pass

    merged = dedupe(all_items + existing)
    merged.sort(key=lambda x: (x.get("date") or "0000-00-00"), reverse=True)
    # 무한정 커지지 않도록 최근 400건만 보관
    merged = merged[:400]

    # sources_status도 병합: 이번 회차(group)에 안 돌린 소스는 이전 상태를 유지
    # (예: --group cloud 실행은 self_hosted 소스의 마지막 상태를 지우지 않음)
    status_by_id = {s["id"]: s for s in existing_status}
    for s in sources_status:
        status_by_id[s["id"]] = s
    merged_status = list(status_by_id.values())

    all_sources = load_sources()
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_count": len(all_sources),
        "ok_count": sum(1 for s in merged_status if s.get("ok")),
        "failed_sources": [s["id"] for s in merged_status if not s.get("ok")],
        "sources_status": merged_status,
        "item_count": len(merged),
        "items": merged,
    }
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("완료: %d건 저장 (%s), 이번 회차 실패/0건 소스: %s", len(merged), DATA_PATH, failed or "없음")


def test_one(source_id: str):
    sources = {s["id"]: s for s in load_sources()}
    if source_id not in sources:
        print(f"'{source_id}' 를 찾을 수 없습니다. --list 로 확인하세요.")
        sys.exit(1)
    items = run_one(sources[source_id])
    print(json.dumps(items, ensure_ascii=False, indent=2))
    print(f"\n총 {len(items)}건")


def list_sources():
    for s in load_sources():
        print(f"{s['id']:16s} [{s['org_type']:14s}] {s['org']} - {s['name']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="과학/공학 활동 허브 스크레이퍼")
    parser.add_argument("--test", metavar="SOURCE_ID", help="소스 하나만 테스트 실행")
    parser.add_argument("--list", action="store_true", help="등록된 소스 목록 출력")
    parser.add_argument("--group", choices=["all", "cloud", "self_hosted"], default="all",
                         help="cloud=해외 러너에서도 잘 되는 소스만 / self_hosted=차단 위험 소스만 / all=전체(기본값)")
    args = parser.parse_args()

    if args.list:
        list_sources()
    elif args.test:
        test_one(args.test)
    else:
        run_all(group=args.group)
