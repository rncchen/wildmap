"""taxon_key 是整個資料庫的主鍵，跨來源合併全靠它，所以單獨驗證。

直接執行即可：python tests/test_taxonomy.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wildmap.taxonomy import cell_id, clean_scientific_name, taxon_key  # noqa: E402

CASES = [
    # TBN 夾帶 HTML 斜體標籤
    ("<i>Bidens</i> <i>alba</i> (L.) DC.", "Bidens alba"),
    # iNat 給到亞種，要收斂回種
    ("Spilornis cheela hoya", "Spilornis cheela"),
    ("Gorsachius melanolophus melanolophus", "Gorsachius melanolophus"),
    ("Falco peregrinus calidus", "Falco peregrinus"),
    # 命名者接在種小名後面
    ("Prionailurus bengalensis Kerr, 1792", "Prionailurus bengalensis"),
    # 屬階層沒有雙名，原樣保留
    ("Urocissa", "Urocissa"),
    ("Ardeidae", "Ardeidae"),
    # 種小名大小寫要正規化
    ("urocissa CAERULEA", "Urocissa caerulea"),
    # 只有屬加命名者，不該把命名者當種小名
    ("Anas L.", "Anas"),
    # 變種與未定種
    ("Machilus thunbergii var. konishii", "Machilus thunbergii"),
    ("Rhododendron sp.", "Rhododendron"),
    ("", ""),
    # 雜交個體自成一列，不能併進親本
    (
        "Pycnonotus sinensis formosae × Pycnonotus taivanus",
        "Pycnonotus sinensis × Pycnonotus taivanus",
    ),
    ("Mareca penelope x Mareca americana", "Mareca americana × Mareca penelope"),
    ("Cairina moschata x Anas platyrhynchos", "Anas platyrhynchos × Cairina moschata"),
    # 兩個來源給的親本順序不同，要併成同一個鍵值
    ("Mareca americana × Mareca penelope", "Mareca americana × Mareca penelope"),
    # iNat 會省略後面親本的屬名，補回去才對得上 eBird 的寫法
    (
        "Pycnonotus taivanus × sinensis",
        "Pycnonotus sinensis × Pycnonotus taivanus",
    ),
    # × 在屬與種小名之間，是雜交起源的種，整串就是一個學名，不可拆成親本
    ("Citrus × limon", "Citrus × limon"),
    ("Magnolia × alba", "Magnolia × alba"),
    ("Crocosmia × crocosmiiflora", "Crocosmia × crocosmiiflora"),
    # 跨屬雜交
    (
        "Felis catus × Prionailurus bengalensis",
        "Felis catus × Prionailurus bengalensis",
    ),
]


def main() -> int:
    failed = 0
    for raw, expected in CASES:
        got = taxon_key(raw)
        mark = "OK " if got == expected else "FAIL"
        if got != expected:
            failed += 1
        print(f"{mark} {raw!r:<45} -> {got!r} (expected {expected!r})")

    # 同一座標必須落在同一網格，相鄰座標不可
    assert cell_id(23.5, 120.5) == cell_id(23.51, 120.51)
    assert cell_id(23.5, 120.5) != cell_id(23.6, 120.5)
    assert cell_id(None, 120.5) is None
    assert clean_scientific_name(None) == ""
    print("\n網格與清理函式檢查通過")

    print(f"\n{len(CASES) - failed}/{len(CASES)} 通過")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
