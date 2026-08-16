# 第三方資料、圖資與軟體的授權

本專案自己的原始碼採 MIT 授權（見 `LICENSE`），但執行時會取用以下第三方
資源，各有各的授權與限制。**其中兩項明確限制非商業使用，因此整個專案原樣
部署時僅限非商業用途。**

以下條款於 2026-08 查證。

## 觀測資料

### iNaturalist

- 網址：<https://www.inaturalist.org/>
- 取得方式：公開 API `https://api.inaturalist.org/v1/`，免金鑰
- 授權：**逐筆不同**。使用者上傳時預設採 CC BY-NC，但可自行改成 CC0、
  CC BY、CC BY-NC-SA 等，也可以保留所有權利
- 本專案作法：每筆觀測與每張照片的授權字串都存進資料庫（`observation.license`
  與 `observation.photo_attribution`），並在資訊卡逐筆顯示。照片以原網址嵌入，
  不另行重製散布
- 使用規範（官方 API Recommended Practices）與本專案的對應：

  | 官方規範 | 本專案 |
  | --- | --- |
  | 每秒約 1 次請求，每天約 1 萬次 | 內建 1 秒節流，預設 6 小時同步一次，一天約 20 次 |
  | 超過 1 萬筆請用 `id_above` 遞增遊標 | `iter_observations()` 正是這個作法 |
  | 分頁請用支援的最大值 | `per_page=200` |
  | 請設自訂 User-Agent | `WILDMAP_USER_AGENT` 可設定，預設帶專案名 |
  | 單一 IP，勿分散繞過限制 | 單一程序 |
  | 每小時勿下載超過 5 GB 媒體 | 只嵌入圖片網址，不下載儲存 |

### eBird

- 網址：<https://ebird.org/>
- 取得方式：API 2.0，需免費金鑰（<https://ebird.org/api/keygen>）
- 授權：eBird API Terms of Use（2021-10-19 版）
  - **僅限非商業使用**。條款允許用於「websites, web-based platforms, mobile
    applications」等非商業用途，商業使用需事先取得書面同意
  - **必須標示 eBird.org 為資料來源**，並盡可能附上連回 eBird.org 的連結
  - **不可分享 API 金鑰**
- 本專案作法：側欄頁尾標示 eBird 為來源並連回官網，每筆 eBird 觀測的資訊卡
  標示來源並連向該筆檢核表。金鑰只存在伺服器端的 `.env`，不進版控、不送到前端

## 物種與保育資料

### TBN 台灣生物多樣性網絡

- 網址：<https://www.tbn.org.tw/>
- 主管機關：農業部生物多樣性研究所
- 取得方式：公開 API `https://www.tbn.org.tw/api/v26/`，免金鑰
- 授權：**政府資料開放授權條款第 1 版**（<https://www.tbn.org.tw/about/OGDL-1.0>）
- 本專案作法：只取用物種字典（保育等級、紅皮書等級、特有性、原生性、
  TaiCOL 中文正名），不作為觀測來源

## 地圖圖資

### 臺灣通用電子地圖

- 提供者：內政部國土測繪中心（NLSC）
- 端點：`https://wmts.nlsc.gov.tw/wmts/EMAP/...`
- 授權：官方說明為免註冊、免費提供公眾使用的 WMTS 服務
- 本專案作法：地圖左下角標示圖資來源並連回官方網站

## 視覺設計

### animal-island-ui

- 網址：<https://github.com/guokaigdg/animal-island-ui>
- 授權：**CC BY-NC 4.0**
  - **禁止商業使用**
  - 需保留原始著作權聲明與授權聲明
- 本專案作法：未使用其原始碼（該專案是 React 元件庫，本專案是純 HTML）。
  視覺依照其公開文件 `docs/design-system/css-variables.md` 提供的變數表
  獨立實作，該文件明確標示該段是給「不依賴元件庫、自行重新實作」使用。
  出處註記於 `wildmap/web/index.html` 的 CSS 開頭

## 軟體套件

| 套件 | 授權 |
| --- | --- |
| Leaflet 1.9.4 | BSD-2-Clause |
| FastAPI | MIT |
| Starlette | BSD-3-Clause |
| Uvicorn | BSD-3-Clause |
| httpx / httpcore | BSD-3-Clause |
| Pydantic | MIT |
| anyio、h11、httptools、watchfiles、PyYAML | MIT |
| idna、click、websockets、python-dotenv | BSD-3-Clause |
| certifi | MPL-2.0 |
| typing_extensions | PSF-2.0 |

字型透過 Google Fonts 載入：

| 字型 | 授權 |
| --- | --- |
| Nunito | SIL Open Font License 1.1 |
| Noto Sans TC | SIL Open Font License 1.1 |

## 保育敏感資料

保育類與紅皮書受脅物種的座標，來源端（iNaturalist）已依其敏感物種政策
模糊化，通常只到縣市層級。本專案原樣沿用，不做還原也不做推估，資訊卡會
標示「位置經模糊化處理」，網格新記錄的警示判定也會略過這類紀錄，避免
用假座標推論出精確棲地。

## 想商業使用的話

需要自行處理以下三件事：

1. 移除 eBird 介接，或依 eBird 條款向康乃爾鳥類學研究室取得書面授權
2. 重寫視覺設計，不沿用 animal-island-ui 的設計規範
3. 逐筆檢查 iNaturalist 觀測與照片的授權，只保留允許商業使用的（CC0、CC BY、
   CC BY-SA）
