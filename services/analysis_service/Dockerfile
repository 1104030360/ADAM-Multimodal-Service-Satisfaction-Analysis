# 這是第 0 階段的假服務：假分數、3 張圖，只用來確認流程，不是原本的分析服務（差異見 README.md）。
# 同一個映像檔跑兩種容器（docs/rebuild/sqs/PLAN.md、DEPLOY.md）：預設的 CMD 是 API（app.py），
# worker 用 `docker run … <映像檔> python worker.py`，從 SQS 領工作做假分析。
# 原本的分析服務要另外移植（steps/06_analyzer.md），基底改成 python:3.11-slim，記憶體至少 8 GiB。
FROM python:3.12-slim
# 用固定版本的 uv 安裝套件（版本號要和產生 uv.lock 的 uv 相同或更新）
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/
# TZ：服務現場的時區（new-feature PLAN R4），和 RDS 的 time_zone、DB_TIME_ZONE 相同
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=America/Los_Angeles MPLBACKEND=Agg \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"
# ffmpeg 假服務用不到（留著不影響結果）；fonts-noto-cjk 讓假服務的中文圖表、報表 PDF 上的中文名字能顯示
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk tzdata \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# 照 uv.lock 裝進 /app/.venv（上面的 PATH 讓 python、gunicorn 都用這個環境）。
# 包含 dev 群組（pytest、moto、pypdf），才能在容器裡跑報表的測試（和 analyzer/ 一樣）
COPY pyproject.toml uv.lock ./
RUN uv sync --locked
# app.py（API）、worker.py（假分析），加上和 analyzer/ 共用的 analysis_queue.py（SQS）、報表的三個檔案
# （report_model.py、report_pdf.py、reports.py）、ollama_explain.py、service_outcome.py
COPY *.py ./
COPY tests ./tests
RUN useradd -m -u 10001 app
USER app
EXPOSE 5000
# HEALTHCHECK 只適用 API；worker 容器在 docker run 用 --health-cmd "python -m analysis_queue alive" 蓋掉（DEPLOY.md）
HEALTHCHECK CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/healthz',timeout=3).status==200 else 1)"
# API：報表的佇列在記憶體裡、只能有一份，所以只能 1 個 gunicorn worker；也不能加 --preload
# （outbox 送出器的 SQS client、執行緒要在 fork 之後才建立）。分析不在這裡跑：worker 容器是 python worker.py
CMD ["gunicorn", "-w", "1", "--threads", "4", "-b", "0.0.0.0:5000", "--timeout", "60", "app:app"]
