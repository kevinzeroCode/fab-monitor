# 使用 3.12-slim 的固定修訂版與 Debian 版本，避免浮動 tag 改變執行環境。
FROM python:3.12.12-slim-bookworm

# 不寫入 pyc，並立即輸出 log，方便在容器外排查錯誤。
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# 先複製依賴，修改程式時才能重用 pip 安裝層。
COPY services/tool-api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 固定 UID 並以非 root 執行，降低服務被入侵後的權限。
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY --chown=app:app services/tool-api/app ./app
USER app
EXPOSE 8080

# 用標準函式庫探測程序，不為了 healthcheck 額外安裝 curl。
# /health 不依賴 Redis；Redis 可用性另由 /ready 判斷。
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)"]

# exec 格式讓 uvicorn 能直接收到停止訊號；單 worker 使 chaos 狀態一致。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
