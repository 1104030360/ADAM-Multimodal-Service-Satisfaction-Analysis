# 移植版分析服務（步驟 6）。模型不放進映像檔：放在主機的 /opt/adam/models，執行時唯讀掛載到 /models。
# SQS（docs/rebuild/sqs/PLAN.md、T49）之後同一個映像檔跑兩種容器：預設的 CMD 是 API（app.py，不載入模型）；
# 分析的 worker 是 docker run … python worker.py（載入模型、領 SQS 的工作，一台可以開兩個），見 docs/rebuild/sqs/DEPLOY.md。
# pyannote 不看 HF_HOME，下載位置要另外用 PYANNOTE_CACHE 指定，否則會存在容器裡、跟著容器消失。
# Whisper（faster-whisper）也不看 HF_HOME，下載位置是 WHISPER_DIR（real-analyze PLAN）。
FROM python:3.11-slim
# 用固定版本的 uv 安裝套件（版本號要和 Mac 上產生 uv.lock 的 uv 相同或更新）
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/
# TZ：服務現場的時區（new-feature PLAN R4），和 RDS 的 time_zone、DB_TIME_ZONE 相同
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=America/Los_Angeles MPLBACKEND=Agg \
    HF_HOME=/models/hf PYANNOTE_CACHE=/models/pyannote DEEPFACE_HOME=/models/deepface MODEL_DIR=/models/adam \
    WHISPER_DIR=/models/whisper \
    NUMBA_CACHE_DIR=/tmp/numba \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"
# libgl1、libglib2.0-0 是 opencv-python 需要的；libgomp1 是 torchaudio 需要的；
# fonts-noto-cjk 讓報表 PDF（report_pdf.py）上的中文名字能顯示
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 tzdata fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# 照 uv.lock 裝進 /app/.venv（上面的 PATH 讓 python、gunicorn 都用這個環境）。
# torch、torchaudio 從 PyTorch 的 CPU 來源安裝，寫在 pyproject.toml 的 [tool.uv.sources]。
# 包含 dev 群組（pytest、pytest-cov、coverage、moto），才能在容器裡跑單元測試與整合測試。
COPY pyproject.toml uv.lock ./
RUN uv sync --locked && uv pip check
COPY *.py ./
COPY tests ./tests
RUN useradd -m -u 10001 app
USER app
EXPOSE 5000
# API 的健康檢查（/healthz 讀得到資料庫才是 200）。worker 容器沒有開 port：docker run 時用
# --health-cmd "python -m analysis_queue alive" 蓋掉（看 worker 最近 2 分鐘有沒有回報）
HEALTHCHECK CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/healthz',timeout=3).status==200 else 1)"
# 報表的佇列還在記憶體裡：只能 1 個 gunicorn worker。也不能加 --preload：outbox 送出器的執行緒和 SQS client
# 要在 fork 之後、各自的 process 裡建立
CMD ["gunicorn", "-w", "1", "--threads", "4", "-b", "0.0.0.0:5000", "--timeout", "60", "app:app"]
