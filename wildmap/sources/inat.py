"""iNaturalist 觀測抓取。

延遲只有數秒，是這個專案的即時來源。兩個必須繞開的坑：
一是中文查詢只有 taxa/autocomplete 認得，taxa?q= 與 observations?q= 會回垃圾；
二是分頁超過一萬筆會被擋，所以一律走 id_above 遊標而非 page。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import httpx

from .. import config
from ..taxonomy import cell_id, clean_scientific_name, in_taiwan, taxon_key

log = logging.getLogger(__name__)

# 官方建議平均每秒不超過一次請求
_REQUEST_INTERVAL = 1.0


class InatClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self._last_call = 0.0

    async def _get(self, path: str, params: dict) -> dict:
        elapsed = asyncio.get_event_loop().time() - self._last_call
        if elapsed < _REQUEST_INTERVAL:
            await asyncio.sleep(_REQUEST_INTERVAL - elapsed)

        for attempt in range(8):
            try:
                resp = await self.client.get(f"{config.INAT_API}/{path}", params=params)
            except httpx.HTTPError as exc:
                # 長時間抓取會遇到伺服器主動斷線與讀取逾時，退避後重試即可。
                # 退避上限壓在 60 秒，免得尾端幾次等太久還是同樣結果
                wait = min(60, 2 ** (attempt + 2))
                log.warning("iNat 連線異常（%s），%d 秒後重試", type(exc).__name__, wait)
                await asyncio.sleep(wait)
                self._last_call = asyncio.get_event_loop().time()
                continue

            self._last_call = asyncio.get_event_loop().time()
            if resp.status_code == 429:
                wait = min(120, 2 ** (attempt + 3))
                log.warning("iNat 限流，等待 %d 秒", wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 500:
                wait = min(60, 2 ** (attempt + 2))
                log.warning("iNat 回 %d，%d 秒後重試", resp.status_code, wait)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()

        raise RuntimeError(f"iNaturalist 連續失敗，放棄請求：{path}")

    async def autocomplete_taxon(self, name: str) -> dict | None:
        """中文名換 taxon id 的唯一可靠入口。"""
        payload = await self._get(
            "taxa/autocomplete", {"q": name, "locale": "zh-TW", "per_page": 1}
        )
        results = payload.get("results") or []
        return results[0] if results else None

    async def iter_observations(
        self,
        *,
        updated_since: str | None = None,
        d1: str | None = None,
        iconic_taxa: str | None = None,
        taxon_ids: list[int] | None = None,
        max_pages: int = 500,
    ) -> AsyncIterator[dict]:
        """以 id 遞增遊標翻頁，回傳原始 observation。"""
        params: dict = {
            "place_id": config.INAT_PLACE_TAIWAN,
            "per_page": config.INAT_PAGE_SIZE,
            "locale": "zh-TW",
            "order_by": "id",
            "order": "asc",
        }
        if updated_since:
            params["updated_since"] = updated_since
        if d1:
            params["d1"] = d1
        if iconic_taxa:
            params["iconic_taxa"] = iconic_taxa
        if taxon_ids:
            params["taxon_id"] = ",".join(str(t) for t in taxon_ids)

        id_above = 0
        for _ in range(max_pages):
            payload = await self._get("observations", {**params, "id_above": id_above})
            results = payload.get("results") or []
            if not results:
                return
            for row in results:
                yield row
            id_above = results[-1]["id"]


def parse_observation(row: dict) -> dict | None:
    """把 iNat 的回傳整理成本地欄位。座標不在台灣範圍內就丟掉，
    模糊化過的紀錄仍保留，只標記 obscured 讓前端知道位置不精確。"""
    taxon = row.get("taxon") or {}
    sci = clean_scientific_name(taxon.get("name"))
    key = taxon_key(sci, taxon.get("rank"))
    if not key:
        return None

    location = row.get("location")
    lat = lng = None
    if location:
        try:
            lat_s, lng_s = location.split(",")
            lat, lng = float(lat_s), float(lng_s)
        except ValueError:
            lat = lng = None
    if not in_taiwan(lat, lng):
        return None

    photos = row.get("photos") or []
    photo_url = photo_attribution = None
    if photos:
        # url 給的是縮圖，換成中等尺寸才夠看
        photo_url = (photos[0].get("url") or "").replace("/square.", "/medium.")
        # iNat 直接給組好的標示字串，照抄最不會出錯
        photo_attribution = photos[0].get("attribution")

    observed_on = row.get("observed_on")
    observed_at = row.get("time_observed_at") or None

    return {
        "source": "inat",
        "source_id": str(row["id"]),
        "taxon_key": key,
        "source_scientific_name": taxon.get("name"),
        "observed_on": observed_on,
        "observed_at": observed_at,
        "lat": lat,
        "lng": lng,
        "positional_accuracy": row.get("positional_accuracy"),
        "obscured": int(bool(row.get("obscured") or row.get("geoprivacy"))),
        "place_guess": row.get("place_guess"),
        "county": None,
        "quality_grade": row.get("quality_grade"),
        "photo_url": photo_url or None,
        "source_url": f"https://www.inaturalist.org/observations/{row['id']}",
        "observer": (row.get("user") or {}).get("login"),
        "license": row.get("license_code"),
        "photo_attribution": photo_attribution,
        "cell": cell_id(lat, lng),
        "source_created_at": row.get("created_at"),
        "source_updated_at": row.get("updated_at"),
        "_taxon": {
            "taxon_key": key,
            "scientific_name": sci,
            "rank": taxon.get("rank"),
            "name_zh": taxon.get("preferred_common_name"),
            "name_en": taxon.get("english_common_name"),
            "iconic_taxon": taxon.get("iconic_taxon_name"),
            "inat_taxon_id": taxon.get("id"),
            "is_bird": 1 if taxon.get("iconic_taxon_name") == "Aves" else None,
            "is_introduced": 1 if taxon.get("introduced") else None,
        },
    }


def default_since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
