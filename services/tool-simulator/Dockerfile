# 同 API 使用固定版本的 3.12-slim，讓兩個服務有一致的 Python 環境。
FROM python:3.12.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# 依賴先於程式碼複製，利用 Docker layer cache 縮短重建時間。
COPY services/tool-simulator/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 非 root 執行即可連 Redis 與監聽 8081，不需要系統管理員權限。
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY --chown=app:app services/tool-simulator/simulator ./simulator
USER app
EXPOSE 8081

# metrics HTTP server 存活可由標準函式庫探測；Redis 寫入狀態另看 ticks。
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/metrics', timeout=2)"]
CMD ["python", "-m", "simulator.main"]
