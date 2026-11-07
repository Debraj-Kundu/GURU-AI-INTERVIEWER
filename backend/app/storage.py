"""S3 via boto3, the official AWS SDK (Floci locally). boto3 is blocking, so every call goes through asyncio.to_thread (PLAN §4.8)."""

import asyncio
from functools import cache

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from . import settings


def s3():
    return _client(settings.S3_ENDPOINT)


@cache  # one client per endpoint: the API's own, and the browser-facing one for presigned URLs
def _client(endpoint: str | None):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        # path-style so http://localhost:4566/{bucket}/{key} works; SigV4 is required for presigned PUTs
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


async def ensure_bucket() -> None:
    def run():
        try:
            s3().head_bucket(Bucket=settings.S3_BUCKET)
        except ClientError:
            s3().create_bucket(Bucket=settings.S3_BUCKET)
        # Browser uploads need bucket CORS on real S3; Floci also allows the origin via its env var.
        s3().put_bucket_cors(
            Bucket=settings.S3_BUCKET,
            CORSConfiguration={"CORSRules": [{
                "AllowedOrigins": [settings.WEB_ORIGIN],
                "AllowedMethods": ["PUT"],
                "AllowedHeaders": ["*"],
                "MaxAgeSeconds": 3600,
            }]},
        )

    await asyncio.to_thread(run)


def presign_put(key: str, content_type: str, expires: int = 600) -> str:
    """URL the browser PUTs the file to directly; the API never proxies upload bytes (PLAN §4.3)."""
    # Signed for the address the browser uses, which differs from the API's when it runs in Docker.
    return _client(settings.S3_PUBLIC_ENDPOINT).generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=expires,
    )


async def get_bytes(key: str) -> bytes:
    def run() -> bytes:
        return s3().get_object(Bucket=settings.S3_BUCKET, Key=key)["Body"].read()

    return await asyncio.to_thread(run)


async def size_of(key: str) -> int | None:
    def run() -> int | None:
        try:
            return s3().head_object(Bucket=settings.S3_BUCKET, Key=key)["ContentLength"]
        except ClientError:
            return None

    return await asyncio.to_thread(run)


async def delete(key: str) -> None:
    await asyncio.to_thread(lambda: s3().delete_object(Bucket=settings.S3_BUCKET, Key=key))
