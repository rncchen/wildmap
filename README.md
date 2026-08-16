# wildmap

台灣野生物即時觀測地圖。把 iNaturalist 與 eBird 的觀測資料拉回本地，配上 TBN
的保育等級與中文正名，用一張地圖看最近哪裡出現了什麼。

不是賞鳥導航，也不是捕捉工具，就是在家開著看的一張圖。

## 畫面上有什麼

- **地圖**：縮小時是網格熱區，放大到第 11 級改顯示個別觀測點。視覺上會重疊的
  點會併成一顆並標上筆數，點下去列出該處全部觀測
- **右欄**：物種分頁依觀測數排序，警示分頁列出最近觸發的提醒。點任一列都會
  開資料卡
- **篩選**：期間（3 天到半年）、範圍（全部／保育類／鳥類）、關鍵字。三者連動，
  地圖、清單、統計數字同時跟著變
- **警示**：保育類、紅皮書受脅、網格新記錄、外來種、eBird 判定的區域罕見

## 快速開始

需要 Python 3.12 以上。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .

# 建資料庫、抓物種字典、把保育類學名對到 iNaturalist
.\.venv\Scripts\python.exe -m wildmap init
.\.venv\Scripts\python.exe -m wildmap tbn
.\.venv\Scripts\python.exe -m wildmap resolve

# 回填近半年觀測（鳥類約 5 萬筆，要跑一陣子）
.\.venv\Scripts\python.exe -m wildmap backfill --days 180

# 補跑最近 7 天的警示判定
.\.venv\Scripts\python.exe -m wildmap alerts --days 7

# 開網站
.\.venv\Scripts\python.exe -m wildmap serve --port 8137
```

打開 <http://127.0.0.1:8137> 即可。要讓它自己持續更新，改用：

```powershell
.\.venv\Scripts\python.exe -m wildmap serve --with-ingest --port 8137
```

## 用 Docker 跑

```bash
# 需要 eBird 的話先把金鑰放進 .env
cp .env.example .env

docker compose up -d
docker compose logs -f
```

打開 <http://127.0.0.1:8137>。

容器預設跑 `serve --with-ingest`，**首次啟動會自動抓 TBN 字典、把保育類學名對到
iNaturalist、再回填近半年觀測**，這段要跑二十分鐘以上，期間頁面開得起來但資料是
空的，看 log 可以知道進度。資料庫放在 `wildmap-data` 這個 volume，容器重建不會掉。

映像檔也可以直接拉現成的：

```bash
docker run -d -p 8137:8000 -v wildmap-data:/data \
  -e EBIRD_API_KEY=你的金鑰 \
  ghcr.io/rncchen/wildmap:latest
```

### 資料目錄的權限

容器以 root 進入 entrypoint，把 `/data` 的擁有者對齊之後才降權執行，所以掛
具名 volume 或主機目錄都不必自己先喬權限。

想讓資料庫檔案屬於主機上的某個帳號（NAS 常見需求），把該帳號的 uid 與 gid
傳進來即可，不設就用預設的 10001：

```yaml
environment:
  PUID: "1026"
  PGID: "100"
```

## 設定

複製 `.env.example` 成 `.env` 後編輯。全部都有預設值，不設也能跑，只是沒有 eBird。

| 變數 | 預設 | 說明 |
| --- | --- | --- |
| `EBIRD_API_KEY` | 空 | eBird 金鑰，到 <https://ebird.org/api/keygen> 免費申請。沒填就跳過 eBird |
| `EBIRD_REGION` | `TW` | eBird 區域代碼，縣市可用 `TW-TPE` 這種寫法 |
| `WILDMAP_DB` | `data/wildmap.db` | 資料庫位置 |
| `WILDMAP_INTERVAL_INAT` | 21600（6 小時） | iNaturalist 同步間隔 |
| `WILDMAP_INTERVAL_EBIRD` | 43200（12 小時） | eBird 同步間隔。別超過一天，它的即時端點最多只能回溯 30 天 |
| `WILDMAP_INTERVAL_TBN` | 2592000（30 天） | TBN 物種字典同步間隔 |
| `WILDMAP_CELL_SIZE` | 0.05 | 網格新記錄判定的網格邊長（度），約 5 公里 |
| `WILDMAP_BASELINE_DAYS` | 365 | 判定「該網格首次出現」時回看的天數 |
| `WILDMAP_BACKFILL_DAYS` | 180 | 首次啟動的回填天數 |

## 指令

```
init         建立資料庫結構
tbn          同步 TBN 物種字典（保育等級與中文正名）
resolve      把保育類學名對到 iNaturalist 的 taxon id
backfill     回填歷史觀測，可加 --days 與 --only birds|protected|all
sync         跑一次 iNaturalist 增量同步
ebird        跑一次 eBird 同步，可加 --back 回溯天數（上限 30）
alerts       對最近幾天已入庫的觀測補跑警示判定
merge        把同物異名的舊鍵值資料併回正規鍵值
reclassify   用留存的原始學名重算觀測歸屬，並清掉孤兒物種列
stats        顯示資料庫概況
run          常駐執行，依設定的間隔持續同步
serve        啟動網頁服務，加 --with-ingest 同時在背景同步
```

## 資料來源

| 來源 | 角色 | 實測延遲 |
| --- | --- | --- |
| iNaturalist | 主要觀測來源，全類群 | 數秒 |
| eBird | 鳥類觀測與區域罕見紀錄警示 | 當日 |
| TBN | 物種字典，不當觀測來源 | 批次匯入，可達數月 |

TBN 之所以只當字典，是因為實測它最新一批資料停在九天前，觀測日期還落在數月前，
它是彙整平台不是即時來源。但保育等級、紅皮書、特有性與 TaiCOL 中文正名是本土權威。

## 幾個關鍵的資料處理

這些是踩過坑之後才定下來的，改動前建議先看懂為什麼。

**以學名為主鍵，中文名只是顯示層。** 三個來源唯一共通的識別是學名。

**但學名本身會被分類修訂改掉。** TBN 的 `Accipiter trivirgatus`（鳳頭蒼鷹）在
iNaturalist 已經改成 `Lophospiza trivirgata`，六個 Accipiter 屬的猛禽全部對不上。
`taxon_alias` 表用來接起這種同物異名，靠中文名反查建立。光鳳頭蒼鷹就有 469 筆
觀測是靠這張表才正確帶上保育等級的。

**亞種收斂到種，但屬性要分開看。** 亞種一律併回種，否則同一隻大冠鷲會因為有沒有
標亞種而在地圖上散成兩層。但原生性與特有性會因亞種而異，不能照抄：黃鸝是瀕臨
絕種保育類，卻另有一個外來引進亞種，照抄會把整個種標成外來種。保育地位則相反，
亞種有就算，寧可多報。

**雜交個體自成一列，而且 `×` 有兩種意思。** `Citrus × limon`（檸檬）是雜交起源
的種，`×` 是學名的一部分；`Felis catus × Prionailurus bengalensis` 才是兩個親本
的雜交個體。另外 iNaturalist 會省略後面親本的屬名，不補回去就對不上 eBird 的寫法。

**中文查詢只有一條路。** iNaturalist 的 `/v1/taxa?q=` 對中文不做斷詞，查「台灣藍鵲」
會回軟體動物門。只有 `/v1/taxa/autocomplete` 能用。本專案的搜尋一律查自己累積的
名稱表，不打 iNaturalist 的搜尋端點。

**TaiCOL 正名不等於通用名。** `Prionailurus bengalensis` 的正名是「豹貓」而非
「石虎」，所以搜尋結果會標示是靠哪個別名命中的。

**敏感物種的座標是假的。** 保育類的座標來源端已模糊化到縣市層級，網格新記錄的
判定會略過這類紀錄，不然只是拿假座標製造噪音。

## 專案結構

```
wildmap/
  config.py          設定，讀 .env
  db.py              SQLite 存取與欄位 migration
  schema.sql         資料表
  taxonomy.py        學名正規化、亞種收斂、雜交處理、網格計算
  alerts.py          五種警示的判定
  ingest.py          抓取、排程、同物異名合併、重新分類
  api.py             查詢 API
  sources/
    inat.py          iNaturalist
    ebird.py         eBird
    tbn.py           TBN 物種字典
  web/index.html     前端（單檔，無建置流程）
tests/
  test_taxonomy.py   學名正規化的 21 個案例，直接執行即可
Dockerfile           容器映像
compose.yaml         一行啟動用
.github/workflows/   CI 與映像檔建置
```

## CI

推上 GitHub 後會自動跑兩條流程：

- `ci.yml`：Python 3.12 與 3.13 各跑一次學名正規化測試、建資料庫、檢查 API 路由
  齊全、檢查前端 JavaScript 語法，另外起一次服務打過所有端點
- `docker.yml`：建 linux/amd64 與 linux/arm64 兩種架構的映像檔推到 GitHub
  Container Registry，推完再把容器拉起來確認服務起得來

CI 完全不打外部 API，不會消耗 iNaturalist 或 eBird 的額度。

## 已知限制

- 回填只涵蓋鳥類與保育類，其餘類群只有增量期間進來的
- 有兩個保育類物種（溪流細鯽、長吻真海豚）在 iNaturalist 對不到，抓不到牠們的觀測
- eBird 的即時端點最多回溯 30 天，更早的歷史要靠本地每天累積
- 縣市是從地點字串比對出來的，iNaturalist 沒有提供結構化的行政區欄位
- 前端沒有行動裝置版面

## 授權

本專案原始碼採 MIT 授權，見 `LICENSE`。

但執行時取用的資料、圖資與視覺設計各有授權，其中 **eBird 資料與
animal-island-ui 的視覺設計明確限制非商業使用**，因此整個專案原樣部署時
僅限非商業用途。完整清單與各家條款見 `NOTICE.md`。

公開部署時請保留頁面上的來源標示，那是 eBird 條款的硬性要求，也是 CC BY 系列
授權對原作者應有的尊重。
