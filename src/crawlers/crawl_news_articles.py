"""
연합뉴스 / 한겨레 / 미디어오늘 / 네이버뉴스 1년치 통합 크롤링 스크립트.

사용법:
    # 전체 1년치 (기본)
    python -m src.crawlers.crawl_news_articles

    # 스모크 테스트 (소량)
    SMOKE=1 python -m src.crawlers.crawl_news_articles

    # 환경변수로 범위/한도 조정
    DATE_START=2025-01-01 DATE_END=2026-01-01 PER_SOURCE_LIMIT=500 python -m ...

출력: data/results_{YYYYMMDD}-{YYYYMMDD}.jsonl  (JSON Lines, 1줄 = 1 기사)
"""

import json
import logging
import os
import sys
import threading
import time
from collections import Counter
from datetime import date, datetime

from src.crawlers.base_crawler import BaseCrawler
from src.crawlers.yonhap_crawler import YonhapCrawler
from src.crawlers.hankyoreh_crawler import HankyorehCrawler
from src.crawlers.mediatoday_crawler import MediatodayCrawler
from src.crawlers.naver_news_crawler import NaverNewsCrawler
from src.crawlers.kcc_crawler import KccCrawler
from src.crawlers.mbc_crawler import MbcCrawler
from src.crawlers.nodong_crawler import NodongCrawler
from src.crawlers.nsp_crawler import NspCrawler
from src.utils.logger import get_logger
from src.utils.progress import ProgressBar


logger = get_logger("NewsArticles")


def _parse_date(s, default):
    if not s:
        return default
    return datetime.strptime(s, "%Y-%m-%d").date()


SMOKE = os.environ.get("SMOKE", "").lower() in {"1", "true", "yes"}

if SMOKE:
    # 스모크 테스트: 최근 3일, 소스당 5건
    DATE_END = date(2026, 5, 28)
    DATE_START = date(2026, 5, 25)
    PER_SOURCE_LIMIT = int(os.environ.get("PER_SOURCE_LIMIT", 5))
else:
    # 기본: 1년치 (2025-05-28 ~ 2026-05-28)
    DATE_END = _parse_date(os.environ.get("DATE_END"), date(2026, 5, 28))
    DATE_START = _parse_date(os.environ.get("DATE_START"), date(2025, 5, 28))
    # 사실상 무제한; 날짜 범위로 자연 종료되도록
    PER_SOURCE_LIMIT = int(os.environ.get("PER_SOURCE_LIMIT", 100000))

OUTPUT_DIR = "data"


def output_path():
    fname = f"results_{DATE_START.strftime('%Y%m%d')}-{DATE_END.strftime('%Y%m%d')}.jsonl"
    return os.path.join(OUTPUT_DIR, fname)


def build_crawlers():
    return [
        YonhapCrawler(),
        HankyorehCrawler(),
        MediatodayCrawler(),
        NaverNewsCrawler(),
        # 보도자료/방송/노조 사이트들. crawl(limit=N) 단순 인터페이스.
        # 페이지네이션 없이 최신 N건만 가져오는 구조라 1년 백필은 제한적.
        KccCrawler(),
        MbcCrawler(),
        NodongCrawler(),
        NspCrawler(),
    ]


def make_dedupe_key(item):
    if item.get("doc_id"):
        return ("doc", item["doc_id"])
    if item.get("detail_url"):
        return ("url", item["detail_url"].strip())
    return (
        "title",
        item.get("source") or "",
        item.get("date") or "",
        (item.get("title") or "").strip(),
    )


def load_existing(path):
    if not os.path.exists(path):
        return []
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return items


def write_jsonl(path, items):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def dedupe(items):
    seen = set()
    out = []
    for it in items:
        k = make_dedupe_key(it)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def save_partial(results, path):
    """소스 하나가 끝날 때마다 누적 저장 (중복 제거된 최종본 작성)."""
    existing = load_existing(path)
    merged = dedupe(existing + results)
    write_jsonl(path, merged)
    return merged


def install_streaming_writer(path):
    """make_unified_data 결과를 매번 JSONL에 즉시 append.

    병렬 실행에 대비해 쓰기에 락을 건다.
    수십시간 단위 크롤에서 진행 가시성 + 중단 대비 + 디스크 영속성을 확보.
    중복은 마지막에 dedupe 패스로 정리.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fp = open(path, "a", encoding="utf-8")
    write_lock = threading.Lock()

    original = BaseCrawler.make_unified_data

    def _in_range(date_str):
        if not date_str:
            return True  # 날짜 모르면 보수적으로 통과 (post-hoc 필터링 가능)
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return True
        if BaseCrawler.date_range_start and d < BaseCrawler.date_range_start:
            return False
        if BaseCrawler.date_range_end and d > BaseCrawler.date_range_end:
            return False
        return True

    def patched(self, *args, **kwargs):
        item = original(self, *args, **kwargs)
        if not _in_range(item.get("date")):
            return item  # 범위 외 → 파일에 쓰지 않음
        try:
            with write_lock:
                fp.write(json.dumps(item, ensure_ascii=False) + "\n")
                fp.flush()
        except Exception as e:
            logger.warning(f"streaming write failed: {e}")
        return item

    BaseCrawler.make_unified_data = patched
    return fp


def _run_one(crawler, results_box, status_box):
    """단일 크롤러 실행 워커. results_box[source] = [...], status_box[source] = 'ok'|'failed'|'running'."""
    src = crawler.source_name
    status_box[src] = "running"
    try:
        items = crawler.crawl(limit=PER_SOURCE_LIMIT)
        results_box[src] = items
        status_box[src] = "ok"
        logger.info(f"[{src}] done — collected {len(items)}")
    except Exception as e:
        results_box[src] = []
        status_box[src] = "failed"
        logger.error(f"[{src}] failed: {e}", exc_info=True)


def run_all():
    BaseCrawler.date_range_start = DATE_START
    BaseCrawler.date_range_end = DATE_END

    out_path = output_path()
    logger.info(
        f"Date range: {DATE_START} ~ {DATE_END} | "
        f"per-source limit: {PER_SOURCE_LIMIT} | output: {out_path}"
    )

    # 매 기사 즉시 JSONL append (락 적용)
    stream_fp = install_streaming_writer(out_path)

    crawlers = build_crawlers()
    results_box = {}
    status_box = {c.source_name: "pending" for c in crawlers}

    threads = []
    for c in crawlers:
        t = threading.Thread(
            target=_run_one,
            args=(c, results_box, status_box),
            name=f"crawler-{c.source_name}",
            daemon=True,
        )
        t.start()
        threads.append(t)
        logger.info(f"[{c.source_name}] started")

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        logger.warning("Interrupted by user; running final dedupe.")
        stream_fp.flush()
        save_partial([], out_path)
        raise

    # 최종 중복 제거 패스 (스트리밍 append 후 정리)
    stream_fp.flush()
    stream_fp.close()
    save_partial([], out_path)

    all_new = []
    for src, items in results_box.items():
        logger.info(f"  {src}: {len(items)} ({status_box.get(src)})")
        all_new.extend(items)
    return all_new, out_path


def summarize(items, path):
    dates = [it.get("date") for it in items if it.get("date")]
    sources = Counter(it.get("source") or "Unknown" for it in items)
    companies = Counter(it.get("company") or "Unknown" for it in items)

    print("\n--- NEWS ARTICLES SUMMARY ---")
    print(f"Output file: {os.path.abspath(path)}")
    print(f"Total unique articles: {len(items)}")
    print(
        f"Date range: {min(dates) if dates else 'N/A'} ~ "
        f"{max(dates) if dates else 'N/A'}"
    )
    print("Sources:")
    for s, n in sources.most_common():
        print(f"- {s}: {n}")
    print("Top publishers (company):")
    for c, n in companies.most_common(10):
        print(f"- {c}: {n}")


if __name__ == "__main__":
    logger.info(
        f"Starting crawl ({'SMOKE' if SMOKE else 'FULL'}): "
        f"{DATE_START} ~ {DATE_END}"
    )
    results, path = run_all()
    final = load_existing(path)
    summarize(final, path)
