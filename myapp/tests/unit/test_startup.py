"""步驟 1：沒有雲端憑證、沒有資料庫，app 也要 import 得起來（之前一 import 就連 GCS）。"""
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


def test_app_imports_without_cloud_credentials_or_database(tmp_path):
    # 用新的 Python 程序：這個測試程序早就 import 過 myapp 了。conftest 已經清掉 AWS／DB 環境變數
    env = dict(os.environ, PYTHONPATH=str(SRC))
    result = subprocess.run([sys.executable, "-c", "import myapp.main"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
