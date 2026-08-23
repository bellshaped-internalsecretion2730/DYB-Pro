"""Artifact storage: S3/MinIO with a transparent local-filesystem fallback."""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredObject:
    key: str
    backend: str
    sha256: str
    size: int


class ArtifactStore:
    def __init__(self) -> None:
        self._client = None
        self._checked = False

    def _s3(self):
        if self._checked:
            return self._client
        self._checked = True
        if not (settings.s3_endpoint_url and settings.s3_access_key and settings.s3_secret_key):
            return None
        try:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint_url,
                aws_access_key_id=settings.s3_access_key,
                aws_secret_access_key=settings.s3_secret_key,
                region_name=settings.s3_region,
                config=Config(signature_version="s3v4", retries={"max_attempts": 2}),
            )
            existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
            if settings.s3_bucket not in existing:
                client.create_bucket(Bucket=settings.s3_bucket)
            self._client = client
        except Exception as exc:  # pragma: no cover - depends on infra
            logger.warning("object store unavailable, using local files: %s", exc)
            self._client = None
        return self._client

    def put(self, key: str, data: bytes, content_type: str = "text/plain") -> StoredObject:
        digest = hashlib.sha256(data).hexdigest()
        client = self._s3()
        if client is not None:
            try:
                client.put_object(
                    Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type
                )
                return StoredObject(key=key, backend="s3", sha256=digest, size=len(data))
            except Exception as exc:  # pragma: no cover
                logger.warning("s3 put failed for %s: %s", key, exc)
        path = self._local_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return StoredObject(key=key, backend="local", sha256=digest, size=len(data))

    def get(self, key: str, backend: str = "s3") -> bytes:
        if backend == "s3":
            client = self._s3()
            if client is not None:
                try:
                    return client.get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()
                except Exception as exc:  # pragma: no cover
                    logger.warning("s3 get failed for %s: %s", key, exc)
        with open(self._local_path(key), "rb") as fh:
            return fh.read()

    @staticmethod
    def _local_path(key: str) -> str:
        return os.path.join(settings.local_artifact_dir, key.replace("/", os.sep))


store = ArtifactStore()
