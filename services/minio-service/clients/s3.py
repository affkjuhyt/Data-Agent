from typing import Any

import boto3
import structlog
from botocore.exceptions import ClientError

from config import MINIO_ACCESS_KEY, MINIO_ENDPOINT, MINIO_SECRET_KEY

log = structlog.get_logger()


def s3() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
    )


def ensure_bucket(client: Any, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
            client.create_bucket(Bucket=bucket)
            log.info("bucket.created", bucket=bucket)
        else:
            raise
