"""命令列進入點。"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import config, db, ingest


def _force_utf8() -> None:
    """訊息都是中文，落到 cp950 之類的終端會直接拋 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _stats() -> None:
    with db.session() as conn:
        rows = {
            "物種": conn.execute("SELECT COUNT(*) c FROM taxon").fetchone()["c"],
            "保育類": conn.execute("SELECT COUNT(*) c FROM taxon WHERE is_protected = 1").fetchone()["c"],
            "已對到 iNat id": conn.execute(
                "SELECT COUNT(*) c FROM taxon WHERE is_protected = 1 AND inat_taxon_id IS NOT NULL"
            ).fetchone()["c"],
            "觀測": conn.execute("SELECT COUNT(*) c FROM observation").fetchone()["c"],
            "警示": conn.execute("SELECT COUNT(*) c FROM alert").fetchone()["c"],
        }
        span = conn.execute(
            "SELECT MIN(observed_on) a, MAX(observed_on) b FROM observation"
        ).fetchone()

    for k, v in rows.items():
        print(f"{k:<16} {v:>10,}")
    if span and span["a"]:
        print(f"{'觀測日期範圍':<16} {span['a']} ~ {span['b']}")
    print(f"{'資料庫':<16} {config.DB_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="wildmap", description="台灣野生物即時觀測地圖")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="建立資料庫結構")
    sub.add_parser("tbn", help="同步 TBN 物種字典（保育等級與中文名）")
    sub.add_parser("resolve", help="把保育類學名對到 iNaturalist taxon id")
    sub.add_parser("sync", help="跑一次 iNaturalist 增量同步")

    p_ebird = sub.add_parser("ebird", help="跑一次 eBird 同步（需 EBIRD_API_KEY）")
    p_ebird.add_argument("--back", type=int, default=14, help="回溯天數，上限 30")
    sub.add_parser("merge", help="把同物異名的舊鍵值資料併回正規鍵值")
    sub.add_parser("reclassify", help="用留存的原始學名重算觀測歸屬，並清掉孤兒物種列")

    p_alerts = sub.add_parser("alerts", help="對最近幾天已入庫的觀測補跑警示判定")
    p_alerts.add_argument("--days", type=int, default=7)
    sub.add_parser("stats", help="顯示資料庫概況")
    sub.add_parser("run", help="常駐執行，依設定的間隔持續同步")

    p_backfill = sub.add_parser("backfill", help="回填歷史觀測")
    p_backfill.add_argument("--days", type=int, default=config.BACKFILL_DAYS)
    p_backfill.add_argument("--only", choices=["all", "birds", "protected"], default="all")

    p_serve = sub.add_parser("serve", help="啟動網頁服務")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--with-ingest", action="store_true", help="同時在背景跑同步")

    args = parser.parse_args()
    _force_utf8()
    _setup_logging(args.verbose)

    if args.command == "init":
        db.init_db()
        print(f"資料庫已建立：{config.DB_PATH}")
    elif args.command == "tbn":
        db.init_db()
        asyncio.run(ingest.sync_tbn())
    elif args.command == "resolve":
        asyncio.run(ingest.resolve_inat_ids())
    elif args.command == "backfill":
        asyncio.run(ingest.backfill_inat(args.days, args.only))
    elif args.command == "sync":
        asyncio.run(ingest.sync_inat())
    elif args.command == "ebird":
        asyncio.run(ingest.sync_ebird(args.back))
    elif args.command == "run":
        asyncio.run(ingest.run_forever())
    elif args.command == "merge":
        ingest.rebuild_aliases()
        print(ingest.merge_aliases())
    elif args.command == "reclassify":
        print(ingest.reclassify())
        print(ingest.prune_orphans())
    elif args.command == "alerts":
        print(ingest.evaluate_recent(args.days))
    elif args.command == "stats":
        _stats()
    elif args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(with_ingest=args.with_ingest), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
