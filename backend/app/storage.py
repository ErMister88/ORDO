"""Provider-neutral object storage with fail-closed production configuration."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import mimetypes
import os
from pathlib import Path
import re
from typing import Protocol

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError


MAX_UPLOAD_BYTES = 12 * 1024 * 1024
PUBLIC_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"})
PRIVATE_DOCUMENT_TYPES = frozenset({*PUBLIC_IMAGE_TYPES, "application/pdf"})
_SAFE_FILE_ID = re.compile(r"^file_[0-9a-f]{24}$")
_EXTENSIONS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/heic": ".heic", "image/heif": ".heif", "application/pdf": ".pdf",
}


class StorageError(RuntimeError):
    pass


class StorageNotConfigured(StorageError):
    pass


class StorageObjectNotFound(StorageError):
    pass


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    content_type: str
    checksum_sha256: str
    etag: str | None = None


class StorageProvider(Protocol):
    name: str

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject: ...
    def read(self, key: str) -> tuple[bytes, str]: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def metadata(self, key: str) -> dict: ...
    def signed_url(self, key: str, *, expires_seconds: int = 300) -> str | None: ...


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def storage_backend() -> str:
    return _env("STORAGE_BACKEND").lower()


def storage_configuration_status() -> tuple[str, str]:
    backend = storage_backend()
    if not backend:
        return "not_configured", "Dateispeicher nicht konfiguriert"
    if backend == "s3":
        required = ("S3_BUCKET", "S3_REGION", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
        if all(_env(name) for name in required):
            return "available", "S3-kompatibler Dateispeicher konfiguriert"
        return "degraded", "S3-Konfiguration unvollständig"
    if backend == "local":
        environment = _env("APP_ENV").lower()
        if environment not in {"dev", "development", "test", "testing"}:
            return "unavailable", "Lokaler Dateispeicher ist außerhalb der Entwicklung gesperrt"
        if not _env("STORAGE_LOCAL_DIRECTORY"):
            return "degraded", "Lokales Speicherverzeichnis fehlt"
        return "available", "Lokaler Entwicklungs-Dateispeicher konfiguriert"
    return "unavailable", "Unbekannter Dateispeicher"


def _validate_key(key: str) -> str:
    if not key or key.startswith(("/", "\\")) or "\\" in key:
        raise StorageError("Invalid storage key")
    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise StorageError("Invalid storage key")
    return key


def build_storage_key(
    *, tenant_id: str, visibility: str, resource_type: str, resource_id: str, file_id: str,
    content_type: str,
) -> str:
    if visibility not in {"public", "private"}:
        raise StorageError("Invalid file visibility")
    if not _SAFE_FILE_ID.fullmatch(file_id):
        raise StorageError("Invalid file id")
    resource_token = sha256(resource_id.encode("utf-8")).hexdigest()[:20]
    extension = _EXTENSIONS.get(content_type) or mimetypes.guess_extension(content_type) or ".bin"
    return _validate_key(
        f"tenants/{tenant_id}/{visibility}/{resource_type}/{resource_token}/{file_id}{extension}"
    )


def detect_content_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in {
        b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1",
    }:
        return "image/heic"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def validate_upload(data: bytes, declared_content_type: str, *, visibility: str) -> str:
    if not data:
        raise StorageError("Empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise StorageError("Upload exceeds size limit")
    detected = detect_content_type(data)
    allowed = PUBLIC_IMAGE_TYPES if visibility == "public" else PRIVATE_DOCUMENT_TYPES
    if declared_content_type not in allowed or detected not in allowed:
        raise StorageError("Unsupported file content")
    if declared_content_type in {"image/heif", "image/heic"} and detected == "image/heic":
        return declared_content_type
    if declared_content_type != detected:
        raise StorageError("Declared and detected content types differ")
    return detected


class S3StorageProvider:
    name = "s3"

    def __init__(self) -> None:
        status, message = storage_configuration_status()
        if storage_backend() != "s3" or status != "available":
            raise StorageNotConfigured(message)
        self.bucket = _env("S3_BUCKET")
        self.client = boto3.client(
            "s3",
            endpoint_url=_env("S3_ENDPOINT_URL") or None,
            region_name=_env("S3_REGION"),
            aws_access_key_id=_env("S3_ACCESS_KEY_ID"),
            aws_secret_access_key=_env("S3_SECRET_ACCESS_KEY"),
            config=Config(signature_version="s3v4", s3={"addressing_style": _env("S3_ADDRESSING_STYLE") or "auto"}),
        )

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        key = _validate_key(key)
        checksum = sha256(data).hexdigest()
        try:
            response = self.client.put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                Metadata={"sha256": checksum},
            )
        except (BotoCoreError, ClientError, OSError) as exc:
            raise StorageError("Storage write failed") from exc
        return StoredObject(key, len(data), content_type, checksum, (response.get("ETag") or "").strip('"') or None)

    def read(self, key: str) -> tuple[bytes, str]:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=_validate_key(key))
            content = response["Body"].read()
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"NoSuchKey", "404", "NotFound"}:
                raise StorageObjectNotFound("Object not found") from exc
            raise StorageError("Storage read failed") from exc
        except (BotoCoreError, OSError, KeyError) as exc:
            raise StorageError("Storage read failed") from exc
        return content, response.get("ContentType") or "application/octet-stream"

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=_validate_key(key))
        except (BotoCoreError, ClientError, OSError) as exc:
            raise StorageError("Storage delete failed") from exc

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_validate_key(key))
            return True
        except ClientError as exc:
            if str(exc.response.get("Error", {}).get("Code", "")) in {"NoSuchKey", "404", "NotFound"}:
                return False
            raise StorageError("Storage metadata lookup failed") from exc
        except (BotoCoreError, OSError) as exc:
            raise StorageError("Storage metadata lookup failed") from exc

    def metadata(self, key: str) -> dict:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=_validate_key(key))
        except (BotoCoreError, ClientError, OSError) as exc:
            raise StorageError("Storage metadata lookup failed") from exc
        return {
            "size": response.get("ContentLength"), "contentType": response.get("ContentType"),
            "checksumSha256": (response.get("Metadata") or {}).get("sha256"),
        }

    def signed_url(self, key: str, *, expires_seconds: int = 300) -> str:
        try:
            return self.client.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": _validate_key(key)},
                ExpiresIn=max(30, min(expires_seconds, 900)),
            )
        except (BotoCoreError, ClientError, OSError) as exc:
            raise StorageError("Storage link generation failed") from exc


class LocalStorageProvider:
    name = "local"

    def __init__(self) -> None:
        status, message = storage_configuration_status()
        if storage_backend() != "local" or status != "available":
            raise StorageNotConfigured(message)
        self.root = Path(_env("STORAGE_LOCAL_DIRECTORY")).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / _validate_key(key)).resolve()
        if self.root not in candidate.parents:
            raise StorageError("Invalid storage key")
        return candidate

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        return StoredObject(key, len(data), content_type, sha256(data).hexdigest())

    def read(self, key: str) -> tuple[bytes, str]:
        path = self._path(key)
        if not path.is_file():
            raise StorageObjectNotFound("Object not found")
        return path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def metadata(self, key: str) -> dict:
        path = self._path(key)
        if not path.is_file():
            raise StorageObjectNotFound("Object not found")
        return {"size": path.stat().st_size, "contentType": mimetypes.guess_type(path.name)[0]}

    def signed_url(self, key: str, *, expires_seconds: int = 300) -> None:
        return None


def get_storage_provider() -> StorageProvider:
    backend = storage_backend()
    if backend == "s3":
        return S3StorageProvider()
    if backend == "local":
        return LocalStorageProvider()
    raise StorageNotConfigured("Object storage is not configured")
