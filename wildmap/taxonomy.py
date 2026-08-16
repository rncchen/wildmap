"""跨來源的學名正規化。

三個來源對同一隻生物給的字串長相不同：TBN 夾帶 HTML 斜體標籤與命名者，
iNat 會給到亞種階層，eBird 走 Clements 分類。要把它們併成同一列，
得先把學名壓成統一的鍵值。
"""

from __future__ import annotations

import math
import re
import unicodedata

from . import config

_HTML_TAG = re.compile(r"<[^>]+>")
_PAREN = re.compile(r"\([^)]*\)")
_SPACES = re.compile(r"\s+")

# 雜交個體的分隔符。iNat 用 " × "，eBird 用 " x "，兩邊都要認
_HYBRID = re.compile(r"\s*(?:×|\sx\s)\s*")

# 出現在種小名之後就代表後面接的是命名者或分類註記，不屬於學名本身
_STOP_WORDS = {
    "var", "var.", "subsp", "subsp.", "ssp", "ssp.", "f", "f.", "forma",
    "cf", "cf.", "aff", "aff.", "sp", "sp.", "spp", "spp.", "×", "x",
}


def clean_scientific_name(raw: str | None) -> str:
    """去掉 HTML、括號內容與多餘空白。"""
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw)
    text = _HTML_TAG.sub(" ", text)
    text = _PAREN.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def taxon_key(scientific_name: str | None, rank: str | None = None) -> str:
    """把學名壓成種階層的鍵值。

    亞種、變種一律收斂到種，否則同一隻大冠鷲會因為有沒有標亞種而在
    地圖上散成兩個圖層。屬以上的階層沒有雙名，原樣保留。

    雜交個體自成一個鍵值，不能併進親本。雜頭翁併進白頭翁的話，
    白頭翁的觀測數會灌水，連顯示名都會被雜交名蓋掉。
    """
    name = clean_scientific_name(scientific_name)
    if not name:
        return ""

    crosses = [p.strip() for p in _HYBRID.split(name) if p.strip()]
    if len(crosses) > 1:
        return _hybrid_key(crosses)

    return _species_key(name)


def _is_bare_epithet(text: str) -> bool:
    """單獨一個小寫詞，代表省略了屬名的種小名。"""
    return " " not in text and text[:1].islower()


def _hybrid_key(crosses: list[str]) -> str:
    """處理帶 × 的學名。

    這個符號有兩種完全不同的意思。`Citrus × limon`（檸檬）是雜交起源的種，
    × 是命名法的一部分，整串就是一個學名；`Felis catus × Prionailurus
    bengalensis` 才是兩個親本的雜交個體。拆錯的話檸檬會變成柑橘屬與一個
    不存在的屬相乘。
    """
    # 屬名 × 小寫種小名，是雜交種的正式寫法
    if (
        len(crosses) == 2
        and " " not in crosses[0]
        and crosses[0][:1].isupper()
        and _is_bare_epithet(crosses[1])
    ):
        return f"{crosses[0].capitalize()} × {crosses[1].lower()}"

    # 親本雜交。iNat 會把後面親本的屬名省掉（Pycnonotus taivanus × sinensis），
    # 補回去才對得上另一個來源的寫法
    genus = ""
    parents = []
    for part in crosses:
        if _is_bare_epithet(part) and genus:
            part = f"{genus} {part}"
        key = _species_key(part)
        if not key:
            continue
        genus = key.split(" ")[0]
        parents.append(key)

    unique = sorted(set(parents))
    if not unique:
        return ""
    # 兩個來源給的親本順序不一定相同，排序後才併得起來
    return " × ".join(unique) if len(unique) > 1 else unique[0]


def _species_key(name: str) -> str:
    parts = name.split(" ")
    if len(parts) == 1:
        return parts[0].capitalize()

    genus = parts[0].capitalize()
    epithet = parts[1]

    if epithet.lower().rstrip(".") in _STOP_WORDS:
        return genus
    if not epithet.replace("-", "").isalpha():
        return genus
    # 首字大寫的通常是命名者（Anas L.、Prionailurus bengalensis Kerr）。
    # 全大寫則多半是來源端的排版問題，那仍是種小名。
    if epithet[:1].isupper() and not (epithet.isupper() and len(epithet) > 3):
        return genus

    return f"{genus} {epithet.lower()}"


def is_species_rank(rank: str | None) -> bool:
    if not rank:
        return False
    return rank.lower() in {"species", "種"}


def in_taiwan(lat: float | None, lng: float | None) -> bool:
    if lat is None or lng is None:
        return False
    west, south, east, north = config.TAIWAN_BBOX
    return south <= lat <= north and west <= lng <= east


def cell_id(lat: float | None, lng: float | None, size: float | None = None) -> str | None:
    """把座標落到固定邊長的網格，供「該網格首次出現」判定使用。"""
    if lat is None or lng is None:
        return None
    step = size or config.ALERT_CELL_SIZE
    row = math.floor(lat / step)
    col = math.floor(lng / step)
    return f"{row}_{col}"


def cell_bounds(cell: str, size: float | None = None) -> tuple[float, float, float, float]:
    """回傳 (南, 西, 北, 東)。"""
    step = size or config.ALERT_CELL_SIZE
    row, col = (int(p) for p in cell.split("_"))
    return row * step, col * step, (row + 1) * step, (col + 1) * step
