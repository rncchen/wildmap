"""警示判定。

四種觸發條件分開存，前端可以各自開關，不會混成一鍋。
判定只在觀測首次寫入時跑一次，後續的欄位更新不重複觸發。
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from . import config, db

KIND_PROTECTED = "protected"
KIND_REDLIST = "redlist"
KIND_NEW_IN_CELL = "new_in_cell"
KIND_INTRODUCED = "introduced"
KIND_NOTABLE = "notable"  # eBird 自己判定的罕見紀錄，不需本地基線

# 達到這幾級才算受脅，暫無危機與資料缺乏不觸發
_THREATENED = ("極危", "瀕危", "易危", "接近受脅", "野外滅絕", "區域滅絕", "滅絕")


def _record(
    conn: sqlite3.Connection, kind: str, taxon_key: str, obs_id: int, cell: str | None, detail: str
) -> None:
    conn.execute(
        """
        INSERT INTO alert (kind, taxon_key, observation_id, cell, detail, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(kind, observation_id) DO NOTHING
        """,
        (kind, taxon_key, obs_id, cell, detail, db.now_iso()),
    )


def _is_new_in_cell(
    conn: sqlite3.Connection, taxon_key: str, cell: str, obs_id: int, observed_on: str | None
) -> bool:
    """該物種在這個網格是否為回看期內首見。

    基線只看比這筆更早的紀錄。若把同期紀錄也算進去，同一格同物種的
    第一筆與第二筆會互為基線，兩筆都不算首見；同日的多筆則改用寫入
    順序分先後，否則同一天同一格會重複報好幾次。
    """
    if not observed_on:
        return False

    since = (date.today() - timedelta(days=config.ALERT_BASELINE_DAYS)).isoformat()
    row = conn.execute(
        """
        SELECT 1 FROM observation
        WHERE taxon_key = ? AND cell = ? AND observed_on >= ?
          AND (observed_on < ? OR (observed_on = ? AND id < ?))
        LIMIT 1
        """,
        (taxon_key, cell, since, observed_on, observed_on, obs_id),
    ).fetchone()
    return row is None


def evaluate(conn: sqlite3.Connection, obs_id: int, obs: dict) -> list[str]:
    """回傳這筆觀測觸發的警示種類。"""
    taxon = conn.execute(
        "SELECT * FROM taxon WHERE taxon_key = ?", (obs["taxon_key"],)
    ).fetchone()
    if taxon is None:
        return []

    name = taxon["name_zh"] or taxon["scientific_name"]
    fired: list[str] = []

    if obs.get("_notable"):
        _record(
            conn, KIND_NOTABLE, obs["taxon_key"], obs_id, obs.get("cell"),
            f"{name}／eBird 判定為區域罕見",
        )
        fired.append(KIND_NOTABLE)

    if taxon["is_protected"]:
        _record(
            conn, KIND_PROTECTED, obs["taxon_key"], obs_id, obs.get("cell"),
            f"{name}／{taxon['protected_status_tw']}",
        )
        fired.append(KIND_PROTECTED)

    redlist = taxon["redlist_tw"] or ""
    if any(redlist.startswith(level) for level in _THREATENED):
        _record(conn, KIND_REDLIST, obs["taxon_key"], obs_id, obs.get("cell"), f"{name}／{redlist}")
        fired.append(KIND_REDLIST)

    if taxon["is_introduced"]:
        _record(
            conn, KIND_INTRODUCED, obs["taxon_key"], obs_id, obs.get("cell"),
            f"{name}／{taxon['nativeness'] or '外來種'}",
        )
        fired.append(KIND_INTRODUCED)

    # 敏感物種的座標被來源端模糊化到縣市層級，網格位置是假的，
    # 拿它判定首見只會製造噪音
    cell = None if obs.get("obscured") else obs.get("cell")
    if cell and _is_new_in_cell(conn, obs["taxon_key"], cell, obs_id, obs.get("observed_on")):
        _record(
            conn, KIND_NEW_IN_CELL, obs["taxon_key"], obs_id, cell,
            f"{name}／近 {config.ALERT_BASELINE_DAYS} 天在此網格首見",
        )
        fired.append(KIND_NEW_IN_CELL)

    return fired
