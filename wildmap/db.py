"""SQLite 存取層。"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from . import config


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    try:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, timeout=30)
    except (OSError, sqlite3.OperationalError) as exc:
        # 在容器裡最常見的原因是掛進來的目錄不屬於執行中的使用者，
        # 但原始訊息只說 unable to open database file，看不出是權限問題
        detail = f"開不了資料庫 {config.DB_PATH}：{exc}"
        if hasattr(os, "getuid"):
            detail += (
                f"\n目前的使用者是 uid {os.getuid()}，"
                f"對 {config.DB_PATH.parent} 可能沒有寫入權限。"
                "\n在容器裡可以用 PUID／PGID 指定要對齊的主機帳號。"
            )
        raise RuntimeError(detail) from exc

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# schema.sql 的 CREATE TABLE IF NOT EXISTS 對既有資料表加不了欄位，
# 新增欄位就補在這裡，重複執行無妨
_MIGRATIONS = [
    ("observation", "source_scientific_name", "TEXT"),
    ("observation", "license", "TEXT"),
    ("observation", "photo_attribution", "TEXT"),
]


def init_db() -> None:
    sql = (config.BASE_DIR / "schema.sql").read_text(encoding="utf-8")
    with session() as conn:
        conn.executescript(sql)

        for table, column, coltype in _MIGRATIONS:
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO sync_state (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, now_iso()),
    )


TAXON_COLUMNS = [
    "scientific_name", "rank", "name_zh", "name_en", "iconic_taxon",
    "class_name", "order_name", "family", "genus",
    "inat_taxon_id", "ebird_species_code", "tbn_taxon_uuid", "taicol_id",
    "protected_status_tw", "redlist_tw", "iucn", "endemism", "nativeness",
    "is_bird", "is_protected", "is_introduced",
]


def upsert_taxon(
    conn: sqlite3.Connection, taxon: dict, fill_only: tuple[str, ...] = ()
) -> None:
    """寫入物種主檔。已存在時只覆蓋有值的欄位，避免 TBN 的保育等級被
    iNat 的空值洗掉，反之亦然。

    fill_only 列出的欄位只在原本是空的時候才寫入。中文名走這條路徑，
    因為 TBN 用的是 TaiCOL 台灣物種名錄，該當正名，iNat 的社群名只是備位。
    """
    key = taxon["taxon_key"]
    present = {c: taxon[c] for c in TAXON_COLUMNS if taxon.get(c) is not None}
    present.setdefault("scientific_name", key)

    cols = ["taxon_key", *present.keys(), "updated_at"]
    vals = [key, *present.values(), now_iso()]
    updates = ", ".join(
        f"{c} = COALESCE(taxon.{c}, excluded.{c})" if c in fill_only else f"{c} = excluded.{c}"
        for c in present
    )
    conn.execute(
        f"""
        INSERT INTO taxon ({", ".join(cols)}) VALUES ({", ".join("?" * len(cols))})
        ON CONFLICT(taxon_key) DO UPDATE SET {updates}, updated_at = excluded.updated_at
        """,
        vals,
    )


def add_taxon_name(
    conn: sqlite3.Connection, taxon_key: str, source: str, locale: str, name: str, preferred: bool = False
) -> None:
    conn.execute(
        """
        INSERT INTO taxon_name (taxon_key, source, locale, name, preferred)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(taxon_key, source, locale, name) DO UPDATE SET preferred = excluded.preferred
        """,
        (taxon_key, source, locale, name, int(preferred)),
    )


OBSERVATION_COLUMNS = [
    "source", "source_id", "taxon_key", "source_scientific_name", "observed_on", "observed_at",
    "lat", "lng", "positional_accuracy", "obscured", "place_guess", "county",
    "quality_grade", "photo_url", "source_url", "observer", "cell",
    "license", "photo_attribution",
    "source_created_at", "source_updated_at",
]


def add_alias(conn: sqlite3.Connection, alias_key: str, taxon_key: str, source: str) -> None:
    if alias_key == taxon_key:
        return
    conn.execute(
        """
        INSERT INTO taxon_alias (alias_key, taxon_key, source, created_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(alias_key) DO UPDATE SET taxon_key = excluded.taxon_key, source = excluded.source
        """,
        (alias_key, taxon_key, source, now_iso()),
    )


def resolve_alias(conn: sqlite3.Connection, taxon_key: str) -> str:
    row = conn.execute(
        "SELECT taxon_key FROM taxon_alias WHERE alias_key = ?", (taxon_key,)
    ).fetchone()
    return row["taxon_key"] if row else taxon_key


def load_aliases(conn: sqlite3.Connection) -> dict[str, str]:
    """整份載入。抓取迴圈每筆都查一次資料庫太慢，量也只有數百筆。"""
    return {r["alias_key"]: r["taxon_key"] for r in conn.execute("SELECT * FROM taxon_alias")}


def upsert_observation(conn: sqlite3.Connection, obs: dict) -> tuple[int, bool]:
    """回傳 (observation id, 是否為首次寫入)。是否首次決定了要不要跑警示判定，
    重複收到同一筆的更新不該再觸發一次通知。"""
    existing = conn.execute(
        "SELECT id FROM observation WHERE source = ? AND source_id = ?",
        (obs["source"], obs["source_id"]),
    ).fetchone()

    vals = [obs.get(c) for c in OBSERVATION_COLUMNS]
    if existing:
        assignments = ", ".join(
            f"{c} = ?" for c in OBSERVATION_COLUMNS if c not in ("source", "source_id")
        )
        update_vals = [
            obs.get(c) for c in OBSERVATION_COLUMNS if c not in ("source", "source_id")
        ]
        conn.execute(
            f"UPDATE observation SET {assignments} WHERE id = ?",
            [*update_vals, existing["id"]],
        )
        return existing["id"], False

    cur = conn.execute(
        f"""
        INSERT INTO observation ({", ".join(OBSERVATION_COLUMNS)}, ingested_at)
        VALUES ({", ".join("?" * len(OBSERVATION_COLUMNS))}, ?)
        """,
        [*vals, now_iso()],
    )
    return int(cur.lastrowid), True
