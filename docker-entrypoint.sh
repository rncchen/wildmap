#!/bin/sh
set -e

# 掛進來的 /data 不論是具名 volume 還是主機目錄，擁有者都不一定是容器內的
# 那個使用者，直接以非 root 啟動就會卡在 SQLite 開不了檔。
# 所以先以 root 進來把權限喬好，再降權執行。
#
# PUID/PGID 是 NAS 慣用的作法：想讓資料庫檔案屬於主機上的某個帳號，
# 把那個帳號的 uid/gid 傳進來即可。

PUID="${PUID:-10001}"
PGID="${PGID:-10001}"
DATA_DIR="$(dirname "${WILDMAP_DB:-/data/wildmap.db}")"

mkdir -p "$DATA_DIR"

if [ "$(id -u)" != "0" ]; then
    # 已經被指定成非 root 使用者（例如 compose 寫了 user:），
    # 那就沒有調權限的能力，直接跑，跑不動的話錯誤訊息會說明原因
    exec "$@"
fi

# 對齊指定的 uid/gid
if [ "$(id -u wildmap)" != "$PUID" ] || [ "$(id -g wildmap)" != "$PGID" ]; then
    groupmod -o -g "$PGID" wildmap 2>/dev/null || true
    usermod -o -u "$PUID" -g "$PGID" wildmap 2>/dev/null || true
fi

chown -R "$PUID:$PGID" "$DATA_DIR" 2>/dev/null || {
    echo "警告：無法變更 $DATA_DIR 的擁有者，改以 root 執行" >&2
    exec "$@"
}

# setpriv 來自 util-linux，Debian 一定有；真的缺了也不要讓容器起不來
if command -v setpriv > /dev/null 2>&1; then
    exec setpriv --reuid="$PUID" --regid="$PGID" --init-groups "$@"
fi

echo "警告：找不到 setpriv，改以 root 執行" >&2
exec "$@"
