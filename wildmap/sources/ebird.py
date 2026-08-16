"""eBird 觀測抓取。

需要免費金鑰，到 https://ebird.org/api/keygen 申請後設 EBIRD_API_KEY。
即時端點的 back 參數最多只能回溯 30 天，三個月與半年的區間得靠每天累積，
所以這裡只負責把近期資料持續灌進來，歷史深度由本地資料庫自己長出來。
"""

from __future__ import annotations

import logging

import httpx

from .. import config
from ..taxonomy import cell_id, clean_scientific_name, in_taiwan, taxon_key

log = logging.getLogger(__name__)

# eBird 的繁中語系代碼，與 iNat 的 zh-TW 寫法不同
SPP_LOCALE = "zh"


def enabled() -> bool:
    return bool(config.EBIRD_API_KEY)


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> list[dict]:
    resp = await client.get(
        f"{config.EBIRD_API}/{path}",
        params=params,
        headers={"X-eBirdApiToken": config.EBIRD_API_KEY},
        timeout=90,
    )
    if resp.status_code == 403:
        raise RuntimeError("eBird 金鑰無效或未設定")
    resp.raise_for_status()
    return resp.json()


async def recent_observations(
    client: httpx.AsyncClient, back: int = 14, notable: bool = False
) -> list[dict]:
    path = f"data/obs/{config.EBIRD_REGION}/recent"
    if notable:
        path += "/notable"
    return await _get(
        client,
        path,
        {"back": min(back, 30), "sppLocale": SPP_LOCALE, "detail": "full", "maxResults": 10000},
    )


def parse_observation(row: dict, notable: bool = False) -> dict | None:
    sci = clean_scientific_name(row.get("sciName"))
    key = taxon_key(sci, "species")
    if not key:
        return None

    lat, lng = row.get("lat"), row.get("lng")
    if not in_taiwan(lat, lng):
        return None

    # obsDt 可能是 "2026-08-16 07:30" 或只有日期
    obs_dt = (row.get("obsDt") or "").strip()
    observed_on = obs_dt[:10] or None

    sub_id = row.get("subId") or ""
    species_code = row.get("speciesCode") or ""

    return {
        "source": "ebird",
        # 同一張檢核表可能記錄多個物種，兩者合起來才唯一
        "source_id": f"{sub_id}:{species_code}",
        "taxon_key": key,
        "source_scientific_name": row.get("sciName"),
        "observed_on": observed_on,
        "observed_at": obs_dt if len(obs_dt) > 10 else None,
        "lat": lat,
        "lng": lng,
        "positional_accuracy": None,
        "obscured": 0,
        "place_guess": row.get("locName"),
        "county": None,
        "quality_grade": "research" if row.get("obsValid") else "needs_id",
        "photo_url": None,
        "source_url": f"https://ebird.org/checklist/{sub_id}" if sub_id else None,
        "observer": row.get("userDisplayName"),
        # eBird 條款要求凡是顯示資料的地方都要標示來源並連回 eBird.org
        "license": "eBird",
        "photo_attribution": None,
        "cell": cell_id(lat, lng),
        "source_created_at": None,
        "source_updated_at": None,
        "_notable": notable,
        "_taxon": {
            "taxon_key": key,
            "scientific_name": sci,
            "rank": "species",
            "name_zh": row.get("comName"),
            "ebird_species_code": species_code or None,
            "iconic_taxon": "Aves",
            "is_bird": 1,
        },
    }
