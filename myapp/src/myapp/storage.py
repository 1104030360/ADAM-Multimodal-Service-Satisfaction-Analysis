"""S3 上傳與下載。

import 這個模組時不連線、不讀憑證，第一次上傳或下載時才建立 client。
憑證由 boto3 自己找：AWS_PROFILE 指定的 profile、AWS_ACCESS_KEY_ID 等環境變數，
或 EC2 上的 instance role。有設定 AWS_ENDPOINT_URL 時會改連那個位址（本機測試用的 moto）。
"""
import os
from contextlib import suppress
from functools import lru_cache

import boto3
from boto3.s3.transfer import TransferConfig, create_transfer_manager
from botocore.config import Config
from s3transfer.exceptions import CancelledError as TransferCancelled
from s3transfer.subscribers import BaseSubscriber

from myapp.tasks import Cancelled

CONTENT_TYPES = {".avi": "video/x-msvideo", ".wav": "audio/wav", ".png": "image/png"}


@lru_cache(maxsize=1)
def s3_client():
    session = boto3.session.Session(profile_name=os.getenv("AWS_PROFILE") or None,
                                    region_name=os.getenv("AWS_REGION") or None)
    return session.client("s3", config=Config(retries={"max_attempts": 5, "mode": "standard"}))


def bucket():
    name = os.getenv("S3_BUCKET")
    if not name:
        raise RuntimeError("S3_BUCKET 未設定")
    return name


def _extra_args(local_path):
    content_type = CONTENT_TYPES.get(os.path.splitext(local_path)[1].lower())
    return {"ContentType": content_type} if content_type else None


def upload(local_path, key):
    s3_client().upload_file(local_path, bucket(), key, ExtraArgs=_extra_args(local_path))
    return f"s3://{bucket()}/{key}"


def download(key, local_path):
    s3_client().download_file(bucket(), key, local_path)
    return local_path


# ---- 背景工作用：有進度、可以取消（前端重建 F3，ENG-13）------------------------------------------
# 在 TaskRunner 的 io lane 裡呼叫：report 是 TaskRunner 給的 Throttle，傳「這次又傳了多少 bytes, 總量」；
# token 取消時呼叫 s3transfer 的 future.cancel()，丟出 tasks.Cancelled（TaskRunner 不把它當成錯誤）。


class _Progress(BaseSubscriber):
    def __init__(self, report, size=None):
        self._report = report
        self._size = size  # 上傳時是檔案大小；下載時 None，等 s3transfer 的 HEAD 之後從 future.meta.size 拿

    def on_progress(self, future, bytes_transferred, **kwargs):
        total = self._size if self._size is not None else (future.meta.size or 0)
        self._report(bytes_transferred, total)


def _wait(future, token):
    if token is not None:
        token.on_cancel(future.cancel)
    try:
        return future.result()
    except TransferCancelled as exc:
        raise Cancelled() from exc


def upload_with_progress(local_path, key, token=None, report=None):
    """上傳 local_path 到 S3 的 key，回傳 s3:// URL。取消時丟出 Cancelled（S3 上不會留下這個物件）。"""
    subscribers = [_Progress(report, os.path.getsize(local_path))] if report else None
    with create_transfer_manager(s3_client(), TransferConfig()) as manager:
        future = manager.upload(local_path, bucket(), key, extra_args=_extra_args(local_path), subscribers=subscribers)
        _wait(future, token)
    return f"s3://{bucket()}/{key}"


def download_with_progress(key, local_path, token=None, report=None):
    """把 S3 的 key 下載到 local_path，回傳 local_path。

    先寫到 local_path + ".part"，成功才改名：取消或失敗時刪掉 .part，不留下半個檔案，
    也不會蓋掉 local_path 原本的內容（快取還能用）。
    """
    part = local_path + ".part"
    subscribers = [_Progress(report)] if report else None
    try:
        with create_transfer_manager(s3_client(), TransferConfig()) as manager:
            future = manager.download(bucket(), key, part, subscribers=subscribers)
            _wait(future, token)
    except BaseException:
        with suppress(FileNotFoundError):
            os.remove(part)
        raise
    os.replace(part, local_path)
    return local_path
