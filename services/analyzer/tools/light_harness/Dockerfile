# 輕量的測試映像檔（new-feature N4、N5）：在 Mac 上跑 analyzer/ 的全部測試，不必建 torch＋TensorFlow 的正式映像檔。
# 不是正式的分析服務：torch、numpy、matplotlib、OpenCV（headless）、Flask、boto3、mysql-connector、VADER、pandas、
# scikit-learn、OpenCC、huggingface-hub 和 uv.lock 同版本（Python 3.11）；deepface、pyannote.audio、transformers、librosa、
# faster-whisper 換成 stubs/ 裡的空殼
# （測試本來就會把這些模型換成假的）。真的模型、uv sync --locked 能不能裝起來，這裡都驗證不到。
# 用法（在 analyzer/ 底下）：
#   docker build -t adam-pytest-analyzer-harness:1 tools/light_harness
#   docker run --rm --network none -v "$PWD:/app:ro" adam-pytest-analyzer-harness:1 python -m pytest -q -m "not integration"
#   整合測試：照 docs/rebuild/steps/07_tests.md 的 7-8 開 MySQL 容器，再加 --network <網路> -e DB_HOST=<容器名稱>
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk tzdata \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.4.0
RUN pip install --no-cache-dir numpy==1.26.4 matplotlib==3.11.2 opencv-python-headless==4.10.0.84 \
    vaderSentiment==3.3.2 pandas==2.2.2 scikit-learn==1.5.1 joblib==1.4.2 boto3==1.43.122 \
    mysql-connector-python==26.10.0 Flask==3.0.3 pyannote.core==5.0.0 pillow==13.0.0 \
    pytest==9.2.0 pytest-cov==7.1.0 coverage==7.16.2 "moto[s3]==5.2.5" pypdf==6.21.4 \
    opencc-python-reimplemented==0.1.7 huggingface-hub==0.24.5
COPY stubs /stubs
ENV PYTHONPATH=/stubs TZ=America/Los_Angeles MPLBACKEND=Agg PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
