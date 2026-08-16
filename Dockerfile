FROM python:3.13-slim

# 台北時區。觀測日期與警示都以當地日期判定，時區不對會差一天
ENV TZ=Asia/Taipei \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先只複製套件描述，讓相依套件那層在原始碼變動時還能沿用快取
COPY pyproject.toml README.md ./
COPY wildmap/__init__.py ./wildmap/
RUN pip install --no-cache-dir \
        "httpx>=0.28" "fastapi>=0.141" "uvicorn[standard]>=0.34"

COPY wildmap/ ./wildmap/
COPY docker-entrypoint.sh /usr/local/bin/

RUN pip install --no-cache-dir --no-deps -e . \
    && groupadd --gid 10001 wildmap \
    && useradd --create-home --uid 10001 --gid 10001 wildmap \
    && mkdir -p /data \
    && chown -R wildmap:wildmap /app /data \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

# 這裡刻意不寫 USER。容器以 root 進入 entrypoint，把 /data 的權限對齊之後
# 才降權到 wildmap 執行，否則掛進來的 volume 只要不是這個 uid 所有，
# SQLite 就會直接開不了檔。要指定成主機上的某個帳號請用 PUID/PGID。
ENV WILDMAP_DB=/data/wildmap.db
VOLUME ["/data"]

EXPOSE 8000

# 首次啟動會自動跑 TBN 字典、學名對照與回填，需要一段時間，
# 所以 start-period 給得長一點
HEALTHCHECK --interval=60s --timeout=10s --start-period=180s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=5).status == 200 else 1)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "wildmap", "serve", "--host", "0.0.0.0", "--port", "8000", "--with-ingest"]
