"""Archive of original payloads, so any transaction can be replayed exactly as it arrived.

INTEGRX_ARCHIVE=local:./data/archive      (default; works everywhere)
INTEGRX_ARCHIVE=s3://bucket-name           (any S3-compatible store; set AWS_ENDPOINT_URL for a local one)
"""
from __future__ import annotations

from pathlib import Path

from .config import get_settings


def _target() -> tuple[str, str]:
    spec = get_settings().archive
    if spec.startswith("s3://"):
        return "s3", spec.removeprefix("s3://").strip("/")
    return "local", spec.removeprefix("local:")


def put(key: str, data: bytes) -> str:
    kind, where = _target()
    if kind == "s3":
        import boto3

        s3 = boto3.client("s3")
        s3.put_object(Bucket=where, Key=key, Body=data)
        return f"s3://{where}/{key}"
    path = Path(where) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"local:{key}"


def get(ref: str) -> bytes:
    if ref.startswith("s3://"):
        import boto3

        bucket, _, key = ref.removeprefix("s3://").partition("/")
        return boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    _, where = _target()
    return (Path(where) / ref.removeprefix("local:")).read_bytes()
