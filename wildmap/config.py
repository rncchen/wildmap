"""執行期設定，全部可用環境變數覆寫。"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent


def _load_dotenv(path: Path) -> None:
    """讀專案根目錄的 .env。既有的環境變數優先，臨時想覆寫時
    直接在指令前設變數就好，不必動檔案。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv(PROJECT_DIR / ".env")

DB_PATH = Path(os.getenv("WILDMAP_DB", PROJECT_DIR / "data" / "wildmap.db"))

# iNaturalist 要求帶可識別的 User-Agent，否則有被限流的風險
USER_AGENT = os.getenv(
    "WILDMAP_USER_AGENT",
    "wildmap/0.1 (https://github.com/rncchen/wildmap; wildlife occurrence map)",
)

INAT_API = "https://api.inaturalist.org/v1"
INAT_PLACE_TAIWAN = 7887  # 國家層級；7888 是省層級，範圍不同
INAT_PAGE_SIZE = 200  # API 上限

TBN_API = "https://www.tbn.org.tw/api/v26"
TBN_PAGE_SIZE = 1000  # v2.6 起的上限

EBIRD_API = "https://api.ebird.org/v2"
EBIRD_API_KEY = os.getenv("EBIRD_API_KEY", "")
EBIRD_REGION = os.getenv("EBIRD_REGION", "TW")

# 台灣本島加離島的概略範圍，用於過濾座標明顯離譜的紀錄
TAIWAN_BBOX = (118.0, 21.0, 123.0, 26.5)

# 新記錄警示的網格邊長（度）。0.05 度約 5 公里，與 TBN 的 5x5 網格量級相當
ALERT_CELL_SIZE = float(os.getenv("WILDMAP_CELL_SIZE", "0.05"))

# 判定「該網格首次出現」時回看的天數
ALERT_BASELINE_DAYS = int(os.getenv("WILDMAP_BASELINE_DAYS", "365"))

# 排程間隔（秒）。這是自家看地圖用的節奏，不是即時捕捉，
# 拉長間隔對觀察體驗沒有差別，卻能大幅減少對方伺服器的請求數。
INTERVAL_INAT = int(os.getenv("WILDMAP_INTERVAL_INAT", str(6 * 3600)))
# eBird 的即時端點最多回溯 30 天，間隔不要超過一天，免得中間的紀錄補不回來
INTERVAL_EBIRD = int(os.getenv("WILDMAP_INTERVAL_EBIRD", str(12 * 3600)))
# 保育名錄與紅皮書等級一年也未必動一次
INTERVAL_TBN = int(os.getenv("WILDMAP_INTERVAL_TBN", str(30 * 86400)))

# 首次啟動時回填的天數
BACKFILL_DAYS = int(os.getenv("WILDMAP_BACKFILL_DAYS", "180"))
