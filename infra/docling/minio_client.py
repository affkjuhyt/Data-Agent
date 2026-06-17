"""Minimal synchronous MinIO download — used inside the sync parse route."""
import boto3
import config as cfg


def download(bucket: str, object_key: str) -> bytes:
    s3 = boto3.client(
        "s3",
        endpoint_url=cfg.MINIO_ENDPOINT,
        aws_access_key_id=cfg.MINIO_ACCESS_KEY,
        aws_secret_access_key=cfg.MINIO_SECRET_KEY,
    )
    response = s3.get_object(Bucket=bucket, Key=object_key)
    return response["Body"].read()
