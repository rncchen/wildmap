"""TBN 物種字典。

TBN 的觀測資料是批次匯入的，即時性不足以當觀測來源，
但它的保育等級、紅皮書、特有性與中文俗名是本土權威，拿來當字典正好。
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from .. import config, db
from ..taxonomy import clean_scientific_name, taxon_key

log = logging.getLogger(__name__)

PROTECTED_CODES = ["I", "II", "III"]


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    resp = await client.get(f"{config.TBN_API}/{path}", params=params, timeout=90)
    resp.raise_for_status()
    return resp.json()


async def fetch_taxa(client: httpx.AsyncClient, params: dict) -> list[dict]:
    """TBN 的分頁靠回傳的 links.next，照著走到沒有為止。"""
    out: list[dict] = []
    url = f"{config.TBN_API}/taxon"
    query: dict | None = {**params, "limit": config.TBN_PAGE_SIZE}

    while url:
        resp = await client.get(url, params=query, timeout=90)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("meta", {}).get("status") != "SUCCESS":
            break
        out.extend(payload.get("data") or [])
        # next 已經是帶齊參數的完整網址，再附加 params 會重複
        url = (payload.get("links") or {}).get("next") or ""
        query = None
        if url:
            await asyncio.sleep(0.5)

    return out


def _row_to_taxon(row: dict) -> dict | None:
    sci = clean_scientific_name(row.get("simplifiedScientificName") or row.get("scientificName"))
    key = taxon_key(sci, row.get("taxonRank"))
    if not key:
        return None

    # 亞種的俗名長成「遊隼(赤胸隼)」，拿它當主名會蓋掉種的正名，
    # 只留進名稱表供搜尋命中
    is_subspecies = row.get("taxonRank") == "種下階層"
    protected = row.get("protectedStatusTW")

    taxon = {
        "taxon_key": key,
        "scientific_name": key if is_subspecies else sci,
        "rank": "種" if is_subspecies else row.get("taxonRank"),
        "name_zh": None if is_subspecies else row.get("vernacularName"),
        "class_name": row.get("class"),
        "order_name": row.get("order"),
        "family": row.get("family"),
        "genus": row.get("genus"),
        "tbn_taxon_uuid": row.get("taxonUUID"),
        "taicol_id": row.get("taiCOLTaxonID"),
        "redlist_tw": row.get("categoryRedlistTW"),
        "iucn": row.get("categoryIUCN"),
        "is_bird": 1 if row.get("taxonGroup") == "鳥類" else None,
        # 保育地位就算只掛在某個亞種上，整個種也該進警示名單，寧可多報
        "protected_status_tw": protected,
        "is_protected": 1 if protected else None,
    }

    # 原生性與特有性會因亞種而異，收斂到種之後就不成立了。黃鸝在台灣是
    # 瀕臨絕種保育類，卻另有一個外來引進亞種，照抄會把整個種標成外來種。
    if not is_subspecies:
        taxon["endemism"] = row.get("endemism")
        taxon["nativeness"] = row.get("nativeness")
        # 寫死 0 而非留空，這樣 iNat 那個全球尺度的 introduced 旗標
        # 就不會透過 COALESCE 補進來，台灣的原生性以 TBN 為準
        if row.get("nativeness"):
            taxon["is_introduced"] = 1 if row["nativeness"].startswith("外來") else 0

    return taxon


async def sync_dictionary() -> dict[str, int]:
    """抓保育類與鳥類兩份清單，寫進物種主檔。"""
    headers = {"User-Agent": config.USER_AGENT}
    counts = {"protected": 0, "birds": 0}

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        batches: list[tuple[str, list[dict]]] = []
        for code in PROTECTED_CODES:
            rows = await fetch_taxa(client, {"protectedStatusTW": code})
            log.info("TBN 保育類 %s：%d 種", code, len(rows))
            batches.append(("protected", rows))

        rows = await fetch_taxa(client, {"taxonGroup": "birds"})
        log.info("TBN 鳥類：%d 種", len(rows))
        batches.append(("birds", rows))

    with db.session() as conn:
        # 學名正規化規則一改，同一筆資料會落到不同鍵值，舊記錄留著會讓
        # 搜尋命中錯誤的物種。字典重建視為整批取代。
        conn.execute("DELETE FROM taxon_name WHERE source = 'tbn'")

        for label, rows in batches:
            for row in rows:
                taxon = _row_to_taxon(row)
                if not taxon:
                    continue
                db.upsert_taxon(conn, taxon)
                counts[label] += 1

                key = taxon["taxon_key"]
                vernacular = row.get("vernacularName")
                is_subspecies = row.get("taxonRank") == "種下階層"
                if vernacular:
                    db.add_taxon_name(
                        conn, key, "tbn", "zh-TW", vernacular, preferred=not is_subspecies
                    )
                # 亞種學名留著，日後要細分台灣特有亞種與外來引進亞種時才有依據
                if is_subspecies:
                    sub_sci = clean_scientific_name(row.get("simplifiedScientificName"))
                    if sub_sci:
                        db.add_taxon_name(conn, key, "tbn", "sci-sub", sub_sci)

    return counts
