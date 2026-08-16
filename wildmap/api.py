"""查詢 API 與靜態頁面。"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from . import config, db, ingest

# 前端只提供這幾個區間，直接對到天數
RANGES = {"3d": 3, "1w": 7, "1m": 30, "3m": 90, "6m": 180}

# iNat 的 county 欄位是空的，地點只有一串 place_guess，
# 拿這份清單去比對才問得出「這物種最近在哪些縣市出現」
COUNTIES = [
    "臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市",
    "基隆市", "新竹市", "嘉義市", "新竹縣", "苗栗縣", "彰化縣",
    "南投縣", "雲林縣", "嘉義縣", "屏東縣", "宜蘭縣", "花蓮縣",
    "臺東縣", "澎湖縣", "金門縣", "連江縣",
]


def _counties_in(place_guesses: list[str]) -> list[str]:
    """從地點字串認出縣市。台與臺兩種寫法都會出現，一併比對。"""
    blob = " ".join(p for p in place_guesses if p)
    found = []
    for county in COUNTIES:
        if county in blob or county.replace("臺", "台") in blob:
            found.append(county)
    return found

SCOPES = {
    "birds": "t.is_bird = 1",
    "protected": "t.is_protected = 1",
    "all": "1 = 1",
}


def _since(range_key: str) -> str:
    days = RANGES.get(range_key)
    if days is None:
        raise HTTPException(400, f"range 只接受 {', '.join(RANGES)}")
    return (date.today() - timedelta(days=days)).isoformat()


def _rows(conn: sqlite3.Connection, sql: str, params: list) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _filters(
    range_key: str, scope: str, taxon_key: str | None = None, q: str | None = None
) -> tuple[list[str], list]:
    """組出各端點共用的篩選條件。

    文字篩選要連名稱表一起找，使用者可能輸入的是別名（查「石虎」而正名
    是「豹貓」）。用 EXISTS 而非 join，否則一個物種有多個別名就會重複計數。
    """
    where = SCOPES.get(scope)
    if not where:
        raise HTTPException(400, f"scope 只接受 {', '.join(SCOPES)}")

    clauses = ["o.observed_on >= ?", where]
    params: list = [_since(range_key)]

    if taxon_key:
        clauses.append("o.taxon_key = ?")
        params.append(taxon_key)

    if q and q.strip():
        pattern = f"%{q.strip()}%"
        clauses.append(
            """(t.name_zh LIKE ? OR t.scientific_name LIKE ?
                OR EXISTS (SELECT 1 FROM taxon_name n
                           WHERE n.taxon_key = t.taxon_key AND n.name LIKE ?))"""
        )
        params += [pattern, pattern, pattern]

    return clauses, params


def create_app(with_ingest: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.init_db()
        task = asyncio.create_task(ingest.run_forever()) if with_ingest else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="wildmap", version="0.1.0", lifespan=lifespan)

    @app.get("/api/status")
    def status() -> dict:
        """各來源的最後入庫時間，供前端顯示資料新鮮度並決定要不要自動刷新。"""
        with db.session() as conn:
            sources = _rows(
                conn,
                """
                SELECT source, COUNT(*) AS n, MAX(ingested_at) AS last_ingested,
                       MAX(observed_on) AS last_observed
                FROM observation GROUP BY source
                """,
                [],
            )
            latest = conn.execute("SELECT MAX(ingested_at) AS t FROM observation").fetchone()
            tbn_synced = db.get_state(conn, "tbn_synced_at")
            cursor = db.get_state(conn, "inat_updated_since")

        return {
            "sources": {r.pop("source"): r for r in sources},
            "last_ingested": latest["t"],
            "tbn_synced_at": tbn_synced,
            "inat_cursor": cursor,
            "intervals": {
                "inat": config.INTERVAL_INAT,
                "ebird": config.INTERVAL_EBIRD,
                "tbn": config.INTERVAL_TBN,
            },
        }

    @app.get("/api/summary")
    def summary(range: str = "1w", scope: str = "birds", q: str | None = None) -> dict:
        clauses, params = _filters(range, scope, q=q)
        where = " AND ".join(clauses)

        with db.session() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS observations, COUNT(DISTINCT o.taxon_key) AS species
                FROM observation o JOIN taxon t ON t.taxon_key = o.taxon_key
                WHERE {where}
                """,
                params,
            ).fetchone()
            # 警示統計也要吃同一組條件，否則切到保育類仍看到鳥類的數字
            alerts = conn.execute(
                f"""
                SELECT a.kind, COUNT(*) AS n FROM alert a
                JOIN observation o ON o.id = a.observation_id
                JOIN taxon t ON t.taxon_key = a.taxon_key
                WHERE {where}
                GROUP BY a.kind
                """,
                params,
            ).fetchall()

        return {
            "range": range,
            "scope": scope,
            "since": _since(range),
            "observations": row["observations"],
            "species": row["species"],
            "alerts": {r["kind"]: r["n"] for r in alerts},
        }

    @app.get("/api/species")
    def species(
        range: str = "1w",
        scope: str = "birds",
        q: str | None = None,
        limit: int = Query(100, le=1000),
    ) -> list[dict]:
        clauses, params = _filters(range, scope, q=q)

        with db.session() as conn:
            return _rows(
                conn,
                f"""
                SELECT t.taxon_key, t.scientific_name, t.name_zh, t.name_en,
                       t.protected_status_tw, t.redlist_tw, t.endemism, t.nativeness,
                       t.inat_taxon_id, COUNT(*) AS n, MAX(o.observed_on) AS last_seen
                FROM observation o JOIN taxon t ON t.taxon_key = o.taxon_key
                WHERE {" AND ".join(clauses)}
                GROUP BY t.taxon_key
                ORDER BY n DESC
                LIMIT ?
                """,
                [*params, limit],
            )

    @app.get("/api/observations")
    def observations(
        range: str = "1w",
        scope: str = "birds",
        taxon_key: str | None = None,
        q: str | None = None,
        swlat: float | None = None,
        swlng: float | None = None,
        nelat: float | None = None,
        nelng: float | None = None,
        limit: int = Query(2000, le=10000),
    ) -> dict:
        """近景用的實際點位。框選範圍越小回得越快，全台範圍請改用網格聚合。"""
        clauses, params = _filters(range, scope, taxon_key, q)
        if None not in (swlat, swlng, nelat, nelng):
            clauses.append("o.lat BETWEEN ? AND ? AND o.lng BETWEEN ? AND ?")
            params += [swlat, nelat, swlng, nelng]

        params.append(limit)
        with db.session() as conn:
            rows = _rows(
                conn,
                f"""
                SELECT o.id, o.source, o.lat, o.lng, o.observed_on, o.obscured,
                       o.place_guess, o.quality_grade, o.photo_url, o.source_url,
                       o.observer, o.license, o.photo_attribution,
                       t.taxon_key, t.name_zh, t.scientific_name, t.protected_status_tw, t.redlist_tw
                FROM observation o JOIN taxon t ON t.taxon_key = o.taxon_key
                WHERE {" AND ".join(clauses)}
                ORDER BY o.observed_on DESC
                LIMIT ?
                """,
                params,
            )

        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [r["lng"], r["lat"]]},
                    "properties": {k: v for k, v in r.items() if k not in ("lat", "lng")},
                }
                for r in rows
            ],
        }

    @app.get("/api/grid")
    def grid(
        range: str = "1w",
        scope: str = "birds",
        taxon_key: str | None = None,
        q: str | None = None,
        size: float | None = None,
    ) -> dict:
        """全台分佈用的網格聚合。

        iNat 的圖磚只吃得下類群層級的篩選，表達不了「台灣保育類」這種
        本地才有的條件，所以遠景改用自己的資料算。
        """
        step = size or config.ALERT_CELL_SIZE
        clauses, params = _filters(range, scope, taxon_key, q)
        clauses.append("o.lat IS NOT NULL")

        with db.session() as conn:
            rows = _rows(
                conn,
                f"""
                SELECT CAST(o.lat / ? AS INTEGER) AS row_idx,
                       CAST(o.lng / ? AS INTEGER) AS col_idx,
                       COUNT(*) AS n,
                       COUNT(DISTINCT o.taxon_key) AS species,
                       MAX(o.observed_on) AS last_seen,
                       SUM(CASE WHEN t.is_protected = 1 THEN 1 ELSE 0 END) AS protected_n
                FROM observation o JOIN taxon t ON t.taxon_key = o.taxon_key
                WHERE {" AND ".join(clauses)}
                GROUP BY row_idx, col_idx
                """,
                [step, step, *params],
            )

        return {
            "size": step,
            "max": max((r["n"] for r in rows), default=0),
            "cells": [
                {
                    "lat": (r["row_idx"] + 0.5) * step,
                    "lng": (r["col_idx"] + 0.5) * step,
                    "n": r["n"],
                    "species": r["species"],
                    "protected_n": r["protected_n"],
                    "last_seen": r["last_seen"],
                }
                for r in rows
            ],
        }

    @app.get("/api/alerts")
    def recent_alerts(
        kind: str | None = None,
        days: int = Query(7, le=180),
        limit: int = Query(200, le=1000),
    ) -> list[dict]:
        since = (date.today() - timedelta(days=days)).isoformat()
        clauses = ["a.created_at >= ?"]
        params: list = [since]
        if kind:
            clauses.append("a.kind = ?")
            params.append(kind)
        params.append(limit)

        with db.session() as conn:
            return _rows(
                conn,
                f"""
                SELECT a.id, a.kind, a.detail, a.created_at, a.cell,
                       o.lat, o.lng, o.observed_on, o.place_guess, o.photo_url, o.source_url,
                       t.taxon_key, t.name_zh, t.scientific_name,
                       t.protected_status_tw, t.redlist_tw
                FROM alert a
                JOIN observation o ON o.id = a.observation_id
                JOIN taxon t ON t.taxon_key = a.taxon_key
                WHERE {" AND ".join(clauses)}
                ORDER BY a.created_at DESC
                LIMIT ?
                """,
                params,
            )

    @app.get("/api/search")
    def search(q: str, limit: int = Query(20, le=100)) -> list[dict]:
        """中文與學名都走本地索引。iNat 的 taxa?q= 對中文完全不可靠，
        所以搜尋一律查自己累積的名稱表。

        回傳 matched_name 說明是靠哪個名字命中的。TaiCOL 正名不見得是
        台灣最通用的說法，查「石虎」命中的是正名為「豹貓」的那一種，
        不講清楚使用者會以為搜錯了。
        """
        pattern = f"%{q.strip()}%"
        with db.session() as conn:
            return _rows(
                conn,
                """
                SELECT t.taxon_key, t.scientific_name, t.name_zh, t.name_en,
                       t.protected_status_tw, t.redlist_tw, t.is_bird,
                       t.inat_taxon_id,
                       COALESCE(
                           MIN(CASE WHEN n.name LIKE ? THEN n.name END),
                           CASE WHEN t.name_zh LIKE ? THEN t.name_zh END,
                           t.scientific_name
                       ) AS matched_name
                FROM taxon t
                LEFT JOIN taxon_name n ON n.taxon_key = t.taxon_key
                WHERE t.name_zh LIKE ? OR t.scientific_name LIKE ? OR n.name LIKE ?
                GROUP BY t.taxon_key
                ORDER BY t.name_zh IS NULL, t.name_zh
                LIMIT ?
                """,
                [pattern, pattern, pattern, pattern, pattern, limit],
            )

    @app.get("/api/taxon")
    def taxon_detail(
        taxon_key: str,
        range: str = "1m",
        scope: str = "all",
        recent: int = Query(6, le=30),
    ) -> dict:
        """單一物種的完整資料，供清單點擊後的資料卡使用。"""
        clauses, params = _filters(range, scope, taxon_key)
        where = " AND ".join(clauses)

        with db.session() as conn:
            taxon = conn.execute(
                "SELECT * FROM taxon WHERE taxon_key = ?", (taxon_key,)
            ).fetchone()
            if taxon is None:
                raise HTTPException(404, "查無此物種")

            stat = conn.execute(
                f"""
                SELECT COUNT(*) AS n, MIN(o.observed_on) AS first_seen,
                       MAX(o.observed_on) AS last_seen,
                       SUM(o.photo_url IS NOT NULL) AS with_photo
                FROM observation o JOIN taxon t ON t.taxon_key = o.taxon_key
                WHERE {where}
                """,
                params,
            ).fetchone()

            places = [
                r["place_guess"]
                for r in conn.execute(
                    f"""
                    SELECT DISTINCT o.place_guess
                    FROM observation o JOIN taxon t ON t.taxon_key = o.taxon_key
                    WHERE {where} AND o.place_guess IS NOT NULL
                    """,
                    params,
                )
            ]

            # 有照片的排前面，資料卡才不會開出來是空的
            sightings = _rows(
                conn,
                f"""
                SELECT o.id, o.observed_on, o.place_guess, o.lat, o.lng, o.obscured,
                       o.photo_url, o.source_url, o.quality_grade, o.source,
                       o.observer, o.license, o.photo_attribution
                FROM observation o JOIN taxon t ON t.taxon_key = o.taxon_key
                WHERE {where}
                ORDER BY o.photo_url IS NULL, o.observed_on DESC
                LIMIT ?
                """,
                [*params, recent],
            )

            names = _rows(
                conn,
                """
                SELECT DISTINCT name, locale FROM taxon_name
                WHERE taxon_key = ? AND locale NOT LIKE 'sci%'
                """,
                [taxon_key],
            )

        return {
            "taxon": dict(taxon),
            "stats": dict(stat),
            "counties": _counties_in(places),
            "sightings": sightings,
            "aliases": sorted(
                {n["name"] for n in names if n["name"] != taxon["name_zh"]}
            ),
        }

    @app.get("/api/names/{taxon_key:path}")
    def names(taxon_key: str) -> dict:
        """列出一個物種在各來源的所有名稱，用來看清楚同物異名的狀況。"""
        with db.session() as conn:
            taxon = conn.execute(
                "SELECT * FROM taxon WHERE taxon_key = ?", (taxon_key,)
            ).fetchone()
            if taxon is None:
                raise HTTPException(404, "查無此物種")
            rows = _rows(
                conn,
                "SELECT source, locale, name, preferred FROM taxon_name WHERE taxon_key = ?",
                [taxon_key],
            )
        return {"taxon": dict(taxon), "names": rows}

    @app.get("/")
    def index() -> FileResponse:
        # 開發時頁面改得很頻繁，讓瀏覽器快取住舊版會查半天假問題
        return FileResponse(
            config.BASE_DIR / "web" / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @app.exception_handler(HTTPException)
    def _http_error(_, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app
