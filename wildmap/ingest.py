"""抓取與排程。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from . import alerts, config, db
from .sources import ebird, inat, tbn

log = logging.getLogger(__name__)

# 一次帶太多 taxon_id 會讓查詢字串過長，iNat 容易直接斷線
PROTECTED_CHUNK = 15

STATE_INAT_CURSOR = "inat_updated_since"
STATE_INAT_BACKFILLED = "inat_backfilled_at"
STATE_TBN_SYNCED = "tbn_synced_at"
STATE_INAT_IDS_RESOLVED = "inat_ids_resolved_at"


def _http() -> httpx.AsyncClient:
    # 長時間抓取時 iNat 會單方面關掉 keep-alive 連線，連線池若把它撿回來重用，
    # 每次都會拿到 RemoteProtocolError，退避再久也沒用。索性不重用連線。
    return httpx.AsyncClient(
        headers={"User-Agent": config.USER_AGENT},
        follow_redirects=True,
        limits=httpx.Limits(max_keepalive_connections=0, max_connections=4),
        timeout=httpx.Timeout(120.0, connect=30.0),
    )


def _store(rows: list[dict], emit_alerts: bool) -> tuple[int, int, int]:
    """寫入觀測與隨附的物種資訊，回傳 (新增, 更新, 觸發警示數)。"""
    inserted = updated = fired = 0
    with db.session() as conn:
        aliases = db.load_aliases(conn)
        for row in rows:
            # 來源用的可能是修訂後的新學名，換回帶著保育等級的正規鍵值
            canonical = aliases.get(row["taxon_key"])
            if canonical:
                row["taxon_key"] = canonical

            taxon = row.pop("_taxon", None)
            if taxon:
                if canonical:
                    taxon["taxon_key"] = canonical
                    # 正規列的學名與中文名以 TBN 為準，不讓來源端的新學名覆寫
                    taxon.pop("scientific_name", None)
                db.upsert_taxon(
                    conn, taxon, fill_only=("name_zh", "scientific_name", "is_introduced")
                )
                if taxon.get("name_zh"):
                    db.add_taxon_name(
                        conn, taxon["taxon_key"], "inat", "zh-TW", taxon["name_zh"], preferred=True
                    )
                if taxon.get("name_en"):
                    db.add_taxon_name(conn, taxon["taxon_key"], "inat", "en", taxon["name_en"])

            obs_id, is_new = db.upsert_observation(conn, row)
            if is_new:
                inserted += 1
                if emit_alerts:
                    fired += len(alerts.evaluate(conn, obs_id, row))
            else:
                updated += 1

    return inserted, updated, fired


async def _drain(
    client: inat.InatClient, emit_alerts: bool, keep: callable | None = None, **kwargs
) -> tuple[int, int, int]:
    """邊抓邊分批寫，避免把數十萬筆全堆在記憶體。

    keep 用來在本地篩掉不感興趣的類群，讓上游查詢可以放寬成一次抓完。
    """
    batch: list[dict] = []
    totals = [0, 0, 0]
    scanned = kept = 0

    async for raw in client.iter_observations(**kwargs):
        parsed = inat.parse_observation(raw)
        if not parsed:
            continue
        scanned += 1
        if keep and not keep(parsed):
            continue
        kept += 1
        if parsed:
            batch.append(parsed)
        if len(batch) >= 500:
            for i, n in enumerate(_store(batch, emit_alerts)):
                totals[i] += n
            batch = []

    if batch:
        for i, n in enumerate(_store(batch, emit_alerts)):
            totals[i] += n

    if keep:
        log.info("掃過 %d 筆，符合條件 %d 筆", scanned, kept)

    return tuple(totals)  # type: ignore[return-value]


async def sync_tbn() -> dict[str, int]:
    counts = await tbn.sync_dictionary()
    with db.session() as conn:
        db.set_state(conn, STATE_TBN_SYNCED, db.now_iso())
    log.info("TBN 字典完成：%s", counts)
    return counts


def _accept_hit(conn, taxon_key_value: str, hit: dict, matched_by: str) -> None:
    db.upsert_taxon(
        conn,
        {
            "taxon_key": taxon_key_value,
            "inat_taxon_id": hit.get("id"),
            "iconic_taxon": hit.get("iconic_taxon_name"),
        },
    )
    if hit.get("preferred_common_name"):
        db.add_taxon_name(conn, taxon_key_value, "inat", "zh-TW", hit["preferred_common_name"])
    # 靠中文名對上時，iNat 用的是修訂後的新屬名，留著才知道兩邊是同一種
    if matched_by == "zh" and hit.get("name"):
        from .taxonomy import taxon_key as make_key

        db.add_taxon_name(conn, taxon_key_value, "inat", "sci-synonym", hit["name"])
        db.add_alias(conn, make_key(hit["name"]), taxon_key_value, "inat")


async def resolve_inat_ids(limit: int | None = None) -> dict[str, int]:
    """把保育類物種對到 iNat 的 taxon id。

    先用學名查，對不上再用中文名查。鷹科在 2024 年做過屬級修訂，
    TBN 的 Accipiter trivirgatus 在 iNat 已經是 Lophospiza trivirgata，
    這種同物異名只有中文名接得起來。
    """
    with db.session() as conn:
        rows = conn.execute(
            """
            SELECT taxon_key, scientific_name, name_zh FROM taxon
            WHERE is_protected = 1 AND inat_taxon_id IS NULL
            ORDER BY taxon_key
            """
        ).fetchall()

    if limit:
        rows = rows[:limit]

    from .taxonomy import taxon_key as make_key

    stats = {"by_scientific": 0, "by_chinese": 0, "unresolved": 0}
    async with _http() as http:
        client = inat.InatClient(http)
        for row in rows:
            hit = matched_by = None
            try:
                candidate = await client.autocomplete_taxon(row["scientific_name"])
                if candidate and make_key(candidate.get("name")) == row["taxon_key"]:
                    hit, matched_by = candidate, "sci"
            except Exception as exc:
                log.warning("以學名查 %s 失敗：%s", row["scientific_name"], exc)

            if hit is None and row["name_zh"]:
                try:
                    candidate = await client.autocomplete_taxon(row["name_zh"])
                except Exception as exc:
                    log.warning("以中文名查 %s 失敗：%s", row["name_zh"], exc)
                    candidate = None
                # 中文名必須完全相同才採納，避免把近似名的物種接錯
                if candidate and candidate.get("preferred_common_name") == row["name_zh"]:
                    hit, matched_by = candidate, "zh"

            if hit is None:
                stats["unresolved"] += 1
                log.info("對不到 iNat：%s（%s）", row["taxon_key"], row["name_zh"] or "無中文名")
                continue

            with db.session() as conn:
                _accept_hit(conn, row["taxon_key"], hit, matched_by)
            stats["by_scientific" if matched_by == "sci" else "by_chinese"] += 1
            if matched_by == "zh":
                log.info("同物異名：%s = iNat %s", row["taxon_key"], hit.get("name"))

    with db.session() as conn:
        db.set_state(conn, STATE_INAT_IDS_RESOLVED, db.now_iso())
    merge_aliases()
    log.info("解析結果：%s", stats)
    return stats


def _protected_taxon_ids() -> list[int]:
    with db.session() as conn:
        rows = conn.execute(
            "SELECT inat_taxon_id FROM taxon WHERE is_protected = 1 AND inat_taxon_id IS NOT NULL"
        ).fetchall()
    return [r["inat_taxon_id"] for r in rows]


async def backfill_inat(days: int | None = None, only: str = "all") -> dict[str, int]:
    """首次回填。此時資料庫沒有基線，跑新記錄判定只會全部誤報，所以不產警示。"""
    days = days or config.BACKFILL_DAYS
    d1 = inat.default_since(days)[:10]
    stats = {"birds_inserted": 0, "protected_inserted": 0}

    async with _http() as http:
        client = inat.InatClient(http)

        if only in ("all", "birds"):
            log.info("回填鳥類，起始日 %s", d1)
            inserted, updated, _ = await _drain(client, False, d1=d1, iconic_taxa="Aves")
            stats["birds_inserted"] = inserted
            log.info("鳥類回填：新增 %d，更新 %d", inserted, updated)

        if only == "birds":
            log.info("回填完成：%s", stats)
            return stats

        ids = _protected_taxon_ids()
        log.info("回填保育類 %d 種", len(ids))
        for i in range(0, len(ids), PROTECTED_CHUNK):
            chunk = ids[i : i + PROTECTED_CHUNK]
            try:
                inserted, _, _ = await _drain(client, False, d1=d1, taxon_ids=chunk)
            except Exception:
                # 一批失敗不該讓整個回填前功盡棄，記下來之後補跑即可
                log.exception("保育類第 %d 批回填失敗，跳過", i // PROTECTED_CHUNK + 1)
                stats["failed_chunks"] = stats.get("failed_chunks", 0) + 1
                continue
            stats["protected_inserted"] += inserted

    with db.session() as conn:
        db.set_state(conn, STATE_INAT_BACKFILLED, db.now_iso())
        db.set_state(conn, STATE_INAT_CURSOR, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    log.info("回填完成：%s", stats)
    return stats


def _interest_filter() -> callable:
    """判斷一筆觀測要不要留。

    鳥類看 iNat 自己的類群旗標，保育類查本地字典（含同物異名）。
    """
    with db.session() as conn:
        protected = {
            r["taxon_key"]
            for r in conn.execute("SELECT taxon_key FROM taxon WHERE is_protected = 1")
        }
        aliases = db.load_aliases(conn)

    def keep(parsed: dict) -> bool:
        if (parsed.get("_taxon") or {}).get("is_bird"):
            return True
        key = parsed["taxon_key"]
        return key in protected or aliases.get(key, key) in protected

    return keep


async def sync_inat() -> dict[str, int]:
    """增量同步。遊標往回退五分鐘，換取邊界上的重複而非漏抓。

    一次抓全台灣的更新再本地過濾，不用 taxon_id 分批。分批要打二十幾次
    請求且多數回空，全類群增量一次通常一兩頁就抓完，對方負擔反而小。
    順帶讓非鳥的保育類不再受限於那份 taxon_id 對照表。
    """
    with db.session() as conn:
        cursor = db.get_state(conn, STATE_INAT_CURSOR)
    if not cursor:
        cursor = inat.default_since(1)

    started = datetime.now(timezone.utc)
    async with _http() as http:
        client = inat.InatClient(http)
        inserted, updated, fired = await _drain(
            client, True, updated_since=cursor, keep=_interest_filter()
        )

    new_cursor = started.timestamp() - 300
    with db.session() as conn:
        db.set_state(
            conn,
            STATE_INAT_CURSOR,
            datetime.fromtimestamp(new_cursor, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    result = {"inserted": inserted, "updated": updated, "alerts": fired}
    log.info("iNat 增量：%s", result)
    return result


def evaluate_recent(days: int = 7) -> dict[str, int]:
    """對最近幾天已入庫的觀測補跑警示判定。

    回填時刻意不產警示，否則整批歷史資料會全部誤報成新記錄。
    回填完成後用這支把最近幾天的判定補上，基線就是其餘的歷史資料。
    """
    from datetime import date, timedelta

    since = (date.today() - timedelta(days=days)).isoformat()
    counts: dict[str, int] = {}
    with db.session() as conn:
        rows = conn.execute(
            "SELECT * FROM observation WHERE observed_on >= ? ORDER BY observed_on, id",
            (since,),
        ).fetchall()
        for row in rows:
            for kind in alerts.evaluate(conn, row["id"], dict(row)):
                counts[kind] = counts.get(kind, 0) + 1

    log.info("補跑近 %d 天警示：%s", days, counts)
    return counts


def reclassify() -> dict[str, int]:
    """用留存的原始學名重算所有觀測的鍵值。

    正規化規則調整後（例如雜交個體改成自成一列）用這支離線修正，
    不必為了一條規則把幾萬筆重抓一次。沒有原始學名的舊資料只能跳過。
    """
    from .taxonomy import taxon_key as make_key

    stats = {"checked": 0, "moved": 0, "no_source_name": 0}
    with db.session() as conn:
        aliases = db.load_aliases(conn)
        rows = conn.execute(
            "SELECT id, taxon_key, source_scientific_name FROM observation"
        ).fetchall()

        for row in rows:
            stats["checked"] += 1
            raw = row["source_scientific_name"]
            if not raw:
                stats["no_source_name"] += 1
                continue

            key = make_key(raw)
            key = aliases.get(key, key)
            if key and key != row["taxon_key"]:
                conn.execute(
                    "UPDATE observation SET taxon_key = ? WHERE id = ?", (key, row["id"])
                )
                stats["moved"] += 1

    log.info("重新分類：%s", stats)
    return stats


def prune_orphans() -> dict[str, int]:
    """清掉沒有任何觀測、也沒有 TBN 屬性可言的物種列。

    規則變更後會留下一批再也沒有資料指向的舊鍵值。
    """
    with db.session() as conn:
        cur = conn.execute(
            """
            DELETE FROM taxon WHERE taxon_key NOT IN (SELECT DISTINCT taxon_key FROM observation)
              AND tbn_taxon_uuid IS NULL AND is_protected IS NOT 1 AND is_bird IS NOT 1
            """
        )
        removed = cur.rowcount
        cur = conn.execute(
            "DELETE FROM taxon_name WHERE taxon_key NOT IN (SELECT taxon_key FROM taxon)"
        )
        names = cur.rowcount

    log.info("清掉 %d 個孤兒物種列、%d 筆孤兒名稱", removed, names)
    return {"taxa": removed, "names": names}


def rebuild_aliases() -> int:
    """從名稱表裡的 sci-synonym 記錄重建對照。

    解析階段已經把來源端的修訂學名存進名稱表，所以補建不必再打一次 API。
    """
    from .taxonomy import taxon_key as make_key

    built = 0
    with db.session() as conn:
        rows = conn.execute(
            "SELECT taxon_key, name FROM taxon_name WHERE locale = 'sci-synonym'"
        ).fetchall()
        for row in rows:
            alias = make_key(row["name"])
            if alias and alias != row["taxon_key"]:
                db.add_alias(conn, alias, row["taxon_key"], "inat")
                built += 1

    log.info("重建 %d 筆同物異名對照", built)
    return built


def merge_aliases() -> dict[str, int]:
    """把已經以舊鍵值存在的資料併回正規鍵值。

    alias 是在解析階段才發現的，這之前寫入的觀測仍掛在修訂後的學名底下，
    需要事後收攏一次。
    """
    stats = {"observations": 0, "names": 0, "alerts": 0, "taxa": 0}
    with db.session() as conn:
        for alias_key, canonical in db.load_aliases(conn).items():
            if not conn.execute(
                "SELECT 1 FROM taxon WHERE taxon_key = ?", (canonical,)
            ).fetchone():
                continue

            cur = conn.execute(
                "UPDATE observation SET taxon_key = ? WHERE taxon_key = ?", (canonical, alias_key)
            )
            stats["observations"] += cur.rowcount
            cur = conn.execute(
                "UPDATE alert SET taxon_key = ? WHERE taxon_key = ?", (canonical, alias_key)
            )
            stats["alerts"] += cur.rowcount
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO taxon_name (taxon_key, source, locale, name, preferred)
                SELECT ?, source, locale, name, 0 FROM taxon_name WHERE taxon_key = ?
                """,
                (canonical, alias_key),
            )
            stats["names"] += cur.rowcount
            conn.execute("DELETE FROM taxon_name WHERE taxon_key = ?", (alias_key,))
            cur = conn.execute("DELETE FROM taxon WHERE taxon_key = ?", (alias_key,))
            stats["taxa"] += cur.rowcount

    log.info("同物異名合併：%s", stats)
    return stats


async def sync_ebird(back: int = 14) -> dict[str, int]:
    """抓近期觀測與罕見紀錄。沒設金鑰就直接跳過，不讓整個排程掛掉。"""
    if not ebird.enabled():
        log.info("未設定 EBIRD_API_KEY，略過 eBird")
        return {"skipped": 1}

    rows: list[dict] = []
    async with _http() as http:
        for notable in (False, True):
            raw = await ebird.recent_observations(http, back=back, notable=notable)
            for item in raw:
                parsed = ebird.parse_observation(item, notable=notable)
                if parsed:
                    rows.append(parsed)

    inserted, updated, fired = _store(rows, emit_alerts=True)
    result = {"inserted": inserted, "updated": updated, "alerts": fired}
    log.info("eBird 同步：%s", result)
    return result


async def bootstrap() -> None:
    db.init_db()
    with db.session() as conn:
        tbn_done = db.get_state(conn, STATE_TBN_SYNCED)
        ids_done = db.get_state(conn, STATE_INAT_IDS_RESOLVED)
        backfilled = db.get_state(conn, STATE_INAT_BACKFILLED)

    if not tbn_done:
        await sync_tbn()
    if not ids_done:
        await resolve_inat_ids()
    if not backfilled:
        await backfill_inat()


async def run_forever() -> None:
    await bootstrap()

    async def loop(name: str, interval: int, fn) -> None:
        while True:
            try:
                await fn()
            except Exception:
                log.exception("%s 同步失敗", name)
            await asyncio.sleep(interval)

    tasks = [
        loop("iNaturalist", config.INTERVAL_INAT, sync_inat),
        loop("TBN", config.INTERVAL_TBN, sync_tbn),
    ]
    if ebird.enabled():
        tasks.append(loop("eBird", config.INTERVAL_EBIRD, sync_ebird))

    await asyncio.gather(*tasks)
