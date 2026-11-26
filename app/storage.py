"""Raw data storage: a local folder on your laptop, an S3 bucket on AWS.

    upload_csvs("data/raw")  -> s3://$S3_BUCKET/raw/*.csv   (run once from VS Code)
    download_csvs(dest)      -> copies s3://$S3_BUCKET/raw/*.csv to a local folder (the ingest Job)

Credentials come from the normal AWS chain: AWS_PROFILE / ~/.aws on your laptop,
the pod's IAM role (IRSA) on EKS. Nothing is hard-coded.
"""
import glob
import os
from functools import lru_cache

from app import config


@lru_cache(maxsize=1)
def s3():
    import boto3
    return boto3.client("s3", region_name=config.AWS_REGION)


def enabled():
    return bool(config.S3_BUCKET)


def list_csvs():
    """[(key, size)] of the CSV files under s3://bucket/prefix."""
    out = []
    for page in s3().get_paginator("list_objects_v2").paginate(Bucket=config.S3_BUCKET, Prefix=config.S3_PREFIX):
        out += [(o["Key"], o["Size"]) for o in page.get("Contents", []) if o["Key"].lower().endswith(".csv")]
    return out


def upload_csvs(folder=None):
    folder = folder or config.CSV_DIR
    files = sorted(p for p in glob.glob(os.path.join(folder, "*.csv")) if os.path.getsize(p) > 0)
    if not files:
        raise FileNotFoundError(f"No CSV files in {folder}")
    for path in files:
        key = config.S3_PREFIX + os.path.basename(path)
        print(f"  uploading {os.path.basename(path)} ({os.path.getsize(path) / 1e6:,.0f} MB) -> s3://{config.S3_BUCKET}/{key}",
              flush=True)
        # upload_file does multipart + retries for big files; SSE is also enforced by the bucket policy
        s3().upload_file(path, config.S3_BUCKET, key, ExtraArgs={"ServerSideEncryption": "AES256"})
    return len(files)


def download_csvs(dest):
    os.makedirs(dest, exist_ok=True)
    files = list_csvs()
    if not files:
        raise FileNotFoundError(f"No CSV files under s3://{config.S3_BUCKET}/{config.S3_PREFIX}")
    for key, size in files:
        path = os.path.join(dest, os.path.basename(key))
        if os.path.exists(path) and os.path.getsize(path) == size:
            continue  # already downloaded
        print(f"  downloading s3://{config.S3_BUCKET}/{key} ({size / 1e6:,.0f} MB)", flush=True)
        s3().download_file(config.S3_BUCKET, key, path)
    return dest
