PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- 物種主檔。主鍵是正規化後的種階層學名，因為三個來源只有學名可靠，
-- 中文名與各家的內部 ID 都只是掛在上面的屬性。
CREATE TABLE IF NOT EXISTS taxon (
    taxon_key           TEXT PRIMARY KEY,
    scientific_name     TEXT NOT NULL,
    rank                TEXT,
    name_zh             TEXT,
    name_en             TEXT,
    iconic_taxon        TEXT,
    class_name          TEXT,
    order_name          TEXT,
    family              TEXT,
    genus               TEXT,
    inat_taxon_id       INTEGER,
    ebird_species_code  TEXT,
    tbn_taxon_uuid      TEXT,
    taicol_id           TEXT,
    protected_status_tw TEXT,
    redlist_tw          TEXT,
    iucn                TEXT,
    endemism            TEXT,
    nativeness          TEXT,
    is_bird             INTEGER NOT NULL DEFAULT 0,
    is_protected        INTEGER NOT NULL DEFAULT 0,
    is_introduced       INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_taxon_inat ON taxon(inat_taxon_id);
CREATE INDEX IF NOT EXISTS idx_taxon_ebird ON taxon(ebird_species_code);
CREATE INDEX IF NOT EXISTS idx_taxon_flags ON taxon(is_bird, is_protected);

-- 同一物種在不同來源可能有數個中文名（黑冠麻鷺／黑冠鳽），
-- 全部留著才能讓搜尋涵蓋使用者可能輸入的任一種寫法。
CREATE TABLE IF NOT EXISTS taxon_name (
    taxon_key TEXT NOT NULL,
    source    TEXT NOT NULL,
    locale    TEXT NOT NULL,
    name      TEXT NOT NULL,
    preferred INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (taxon_key, source, locale, name),
    FOREIGN KEY (taxon_key) REFERENCES taxon(taxon_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_taxon_name_name ON taxon_name(name);

-- 分類修訂造成的同物異名。TBN 的 Accipiter trivirgatus 在 iNat 已是
-- Lophospiza trivirgata，觀測進來時要換回帶著保育等級的那個鍵值，
-- 否則同一種鳥會分裂成兩列，保育警示也就不會觸發。
CREATE TABLE IF NOT EXISTS taxon_alias (
    alias_key  TEXT PRIMARY KEY,
    taxon_key  TEXT NOT NULL,
    source     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (taxon_key) REFERENCES taxon(taxon_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS observation (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source              TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    taxon_key           TEXT NOT NULL,
    -- 來源端原始的學名字串。留著才能在正規化規則改變時離線重算鍵值，
    -- 不必為了修一條規則就把幾萬筆重抓一次
    source_scientific_name TEXT,
    observed_on         TEXT,
    observed_at         TEXT,
    lat                 REAL,
    lng                 REAL,
    positional_accuracy INTEGER,
    obscured            INTEGER NOT NULL DEFAULT 0,
    place_guess         TEXT,
    county              TEXT,
    quality_grade       TEXT,
    photo_url           TEXT,
    source_url          TEXT,
    observer            TEXT,
    -- 觀測與照片的授權各自獨立，而且逐筆不同（iNat 從 CC0 到保留所有權利都有）。
    -- 要公開展示就必須逐筆標示，不能只在頁尾寫一句來源
    license             TEXT,
    photo_attribution   TEXT,
    cell                TEXT,
    source_created_at   TEXT,
    source_updated_at   TEXT,
    ingested_at         TEXT NOT NULL,
    UNIQUE (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_obs_observed_on ON observation(observed_on);
CREATE INDEX IF NOT EXISTS idx_obs_taxon ON observation(taxon_key, observed_on);
CREATE INDEX IF NOT EXISTS idx_obs_cell ON observation(taxon_key, cell, observed_on);
CREATE INDEX IF NOT EXISTS idx_obs_bbox ON observation(lat, lng);
CREATE INDEX IF NOT EXISTS idx_obs_ingested ON observation(ingested_at);

CREATE TABLE IF NOT EXISTS alert (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,
    taxon_key      TEXT NOT NULL,
    observation_id INTEGER NOT NULL,
    cell           TEXT,
    detail         TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE (kind, observation_id),
    FOREIGN KEY (observation_id) REFERENCES observation(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_alert_created ON alert(created_at);
CREATE INDEX IF NOT EXISTS idx_alert_kind ON alert(kind, created_at);

CREATE TABLE IF NOT EXISTS sync_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
