"""S3 上傳與下載。

import 這個模組時不連線、不讀憑證，第一次上傳或下載時才建立 client。
憑證由 boto3 自己找：AWS_PROFILE 指定的 profile、AWS_ACCESS_KEY_ID 等環境變數，
或 EC2 上的 instance role。有設定 AWS_ENDPOINT_URL 時會改連那個位址（本機測試用的 moto）。
"""
import os
from functools import lru_cache

import boto3
from botocore.config import Config

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


def upload(local_path, key):
    content_type = CONTENT_TYPES.get(os.path.splitext(local_path)[1].lower())
    extra_args = {"ContentType": content_type} if content_type else None
    s3_client().upload_file(local_path, bucket(), key, ExtraArgs=extra_args)
    return f"s3://{bucket()}/{key}"


def download(key, local_path):
    s3_client().download_file(bucket(), key, local_path)
    return local_path
