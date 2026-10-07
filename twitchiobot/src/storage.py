"""Key/value file storage on S3 or a local directory.

Both backends take the same logical keys (``raw/snapshots/v2/...``), so a
local run writes the same layout the deployed tasks write to S3.
"""

import json
import logging
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False
    logger.warning("boto3 not installed. S3Storage unavailable. Install with: pip install boto3")


class BaseStorage(ABC):
    """Abstract base class for storage backends."""

    @abstractmethod
    def upload_json(self, key: str, data: dict, **kwargs) -> bool:
        """Upload JSON data to storage."""

    @abstractmethod
    def download_json(self, key: str) -> Optional[dict]:
        """Download JSON data from storage."""

    @abstractmethod
    def upload_file(self, key: str, file_path: str, **kwargs) -> bool:
        """Upload file from local path to storage."""

    @abstractmethod
    def download_file(self, key: str, destination: str) -> bool:
        """Download file from storage to local path."""

    @abstractmethod
    def list_files(self, prefix: str = "", suffix: str = "") -> List[str]:
        """List files matching prefix and suffix."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if file exists."""

    @abstractmethod
    def upload_parquet(self, key: str, data: bytes, **kwargs) -> bool:
        """Upload raw Parquet bytes to storage."""

    @abstractmethod
    def download_parquet(self, key: str) -> Optional[bytes]:
        """Download Parquet bytes from storage. Returns None if not found."""

    @abstractmethod
    def get_uri(self, key: str) -> str:
        """Get URI/path for file."""


class FileStorage(BaseStorage):
    """Keys are paths under ``base_dir``; no key may resolve outside it."""

    def __init__(self, base_dir: str = "logs"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._base_dir_resolved = self.base_dir.resolve()
        logger.info(f"FileStorage initialized: {self.base_dir.absolute()}")

    def _resolve_path(self, key: str) -> Path:
        """Resolve a logical key without allowing escape from the storage root."""
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Storage key must be a non-empty string")

        path = (self.base_dir / key).resolve()
        try:
            path.relative_to(self._base_dir_resolved)
        except ValueError as exc:
            raise ValueError("Storage key must remain within the storage root") from exc
        return path

    def upload_json(self, key: str, data: dict, **kwargs) -> bool:
        """Upload JSON data to local file."""
        try:
            path = self._resolve_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)

            indent = kwargs.get('indent', 2)
            with open(path, 'w') as f:
                json.dump(data, f, indent=indent)

            logger.debug(f"JSON uploaded: {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload JSON {key}: {e}")
            return False

    def download_json(self, key: str) -> Optional[dict]:
        """Download JSON data from local file."""
        try:
            path = self._resolve_path(key)
            if not path.exists():
                logger.debug(f"JSON not found: {path}")
                return None

            with open(path, 'r') as f:
                data = json.load(f)

            logger.debug(f"JSON downloaded: {path}")
            return data
        except Exception as e:
            logger.error(f"Failed to download JSON {key}: {e}")
            return None

    def upload_file(self, key: str, file_path: str, **kwargs) -> bool:
        """Copy file from local path to storage."""
        try:
            src = Path(file_path)
            dst = self._resolve_path(key)
            dst.parent.mkdir(parents=True, exist_ok=True)

            shutil.copy2(src, dst)
            logger.debug(f"File uploaded: {src} -> {dst}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload file {key}: {e}")
            return False

    def download_file(self, key: str, destination: str) -> bool:
        """Copy file from storage to local path."""
        try:
            src = self._resolve_path(key)
            dst = Path(destination)
            dst.parent.mkdir(parents=True, exist_ok=True)

            if not src.exists():
                logger.debug(f"File not found: {src}")
                return False

            shutil.copy2(src, dst)
            logger.debug(f"File downloaded: {src} -> {dst}")
            return True
        except Exception as e:
            logger.error(f"Failed to download file {key}: {e}")
            return False

    def list_files(self, prefix: str = "", suffix: str = "") -> List[str]:
        """List files matching prefix and suffix."""
        try:
            search_dir = self._resolve_path(prefix) if prefix else self._base_dir_resolved
            if not search_dir.exists():
                return []

            pattern = f"*{suffix}" if suffix else "*"
            files = []

            for path in search_dir.rglob(pattern):
                if path.is_file():
                    relative = path.relative_to(self._base_dir_resolved)
                    files.append(str(relative))

            logger.debug(f"Listed {len(files)} files with prefix='{prefix}', suffix='{suffix}'")
            return sorted(files)
        except Exception as e:
            logger.error(f"Failed to list files: {e}")
            return []

    def exists(self, key: str) -> bool:
        """Check if file exists."""
        return self._resolve_path(key).exists()

    def upload_parquet(self, key: str, data: bytes, **kwargs) -> bool:
        """Upload raw Parquet bytes to local file."""
        try:
            path = self._resolve_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'wb') as f:
                f.write(data)
            logger.debug(f"Parquet uploaded: {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload Parquet {key}: {e}")
            return False

    def download_parquet(self, key: str) -> Optional[bytes]:
        """Download Parquet bytes from local file."""
        try:
            path = self._resolve_path(key)
            if not path.exists():
                logger.debug(f"Parquet not found: {path}")
                return None
            with open(path, 'rb') as f:
                data = f.read()
            logger.debug(f"Parquet downloaded: {path}")
            return data
        except Exception as e:
            logger.error(f"Failed to download Parquet {key}: {e}")
            return None

    def get_uri(self, key: str) -> str:
        """Get file:// URI for local file."""
        path = self._resolve_path(key).absolute()
        return f"file://{path}"


class S3Storage(BaseStorage):
    """Keys are prefixed with ``prefix``; every write requests SSE-S3 encryption.

    Raises ValueError at construction if the bucket is missing or inaccessible.
    """

    def __init__(self, bucket: str, prefix: str = "", region: str = "us-east-1"):
        if not HAS_BOTO3:
            raise ImportError("boto3 required for S3Storage. Install with: pip install boto3")

        self.bucket = bucket
        self.prefix = prefix.rstrip('/') + '/' if prefix else ''
        self.region = region

        self.s3 = boto3.client('s3', region_name=region)

        # Verify bucket access without requiring an unrestricted ListBucket grant.
        # HeadBucket is authorized as s3:ListBucket and cannot be scoped to this
        # storage instance's prefix, whereas GetBucketLocation is bucket metadata
        # only. Object and listing permissions remain restricted by IAM prefixes.
        try:
            self.s3.get_bucket_location(Bucket=bucket)
            logger.info(f"S3Storage initialized: s3://{bucket}/{self.prefix}")
        except ClientError as e:
            error_code = str(e.response.get('Error', {}).get('Code', ''))
            if error_code in {'404', 'NoSuchBucket'}:
                raise ValueError(f"S3 bucket not found: {bucket}")
            elif error_code in {'403', 'AccessDenied'}:
                raise ValueError(f"Access denied to S3 bucket: {bucket}")
            else:
                raise ValueError(f"S3 bucket access check failed ({error_code or 'unknown error'})")
        except NoCredentialsError:
            raise ValueError("AWS credentials not found. Configure with aws configure or environment variables.")

    def _resolve_key(self, key: str) -> str:
        """Resolve logical key to full S3 key with prefix."""
        return self.prefix + key.lstrip('/')

    def upload_json(self, key: str, data: dict, **kwargs) -> bool:
        """Upload JSON data to S3."""
        try:
            s3_key = self._resolve_key(key)
            indent = kwargs.get('indent', 2)
            json_str = json.dumps(data, indent=indent)
            self.s3.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=json_str.encode('utf-8'),
                ContentType='application/json',
                ServerSideEncryption='AES256'
            )

            logger.debug(f"JSON uploaded to S3: s3://{self.bucket}/{s3_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload JSON to S3 {key}: {e}")
            return False

    def download_json(self, key: str) -> Optional[dict]:
        """Download JSON data from S3."""
        try:
            s3_key = self._resolve_key(key)

            response = self.s3.get_object(Bucket=self.bucket, Key=s3_key)
            json_str = response['Body'].read().decode('utf-8')
            data = json.loads(json_str)

            logger.debug(f"JSON downloaded from S3: s3://{self.bucket}/{s3_key}")
            return data
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                logger.debug(f"JSON not found in S3: {key}")
                return None
            logger.error(f"Failed to download JSON from S3 {key}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to download JSON from S3 {key}: {e}")
            return None

    def upload_file(self, key: str, file_path: str, **kwargs) -> bool:
        """Upload file from local path to S3."""
        try:
            s3_key = self._resolve_key(key)
            content_type = kwargs.get('content_type', 'application/octet-stream')
            if key.endswith('.json'):
                content_type = 'application/json'
            elif key.endswith('.csv'):
                content_type = 'text/csv'
            elif key.endswith('.html'):
                content_type = 'text/html'
            elif key.endswith('.png'):
                content_type = 'image/png'

            self.s3.upload_file(
                file_path,
                self.bucket,
                s3_key,
                ExtraArgs={
                    'ContentType': content_type,
                    'ServerSideEncryption': 'AES256'
                }
            )

            logger.debug(f"File uploaded to S3: {file_path} -> s3://{self.bucket}/{s3_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload file to S3 {key}: {e}")
            return False

    def download_file(self, key: str, destination: str) -> bool:
        """Download file from S3 to local path."""
        try:
            s3_key = self._resolve_key(key)
            Path(destination).parent.mkdir(parents=True, exist_ok=True)

            self.s3.download_file(self.bucket, s3_key, destination)
            logger.debug(f"File downloaded from S3: s3://{self.bucket}/{s3_key} -> {destination}")
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                logger.debug(f"File not found in S3: {key}")
                return False
            logger.error(f"Failed to download file from S3 {key}: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to download file from S3 {key}: {e}")
            return False

    def list_files(self, prefix: str = "", suffix: str = "") -> List[str]:
        """List files matching prefix and suffix."""
        try:
            search_prefix = self._resolve_key(prefix)

            paginator = self.s3.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket, Prefix=search_prefix)

            files = []
            for page in pages:
                if 'Contents' not in page:
                    continue

                for obj in page['Contents']:
                    key = obj['Key']
                    if key.startswith(self.prefix):
                        logical_key = key[len(self.prefix):]
                    else:
                        logical_key = key
                    if suffix and not logical_key.endswith(suffix):
                        continue

                    files.append(logical_key)

            logger.debug(f"Listed {len(files)} files in S3 with prefix='{prefix}', suffix='{suffix}'")
            return sorted(files)
        except Exception as e:
            logger.error(f"Failed to list files in S3: {e}")
            return []

    def exists(self, key: str) -> bool:
        """Check if file exists in S3."""
        try:
            s3_key = self._resolve_key(key)
            self.s3.head_object(Bucket=self.bucket, Key=s3_key)
            return True
        except ClientError:
            return False

    def upload_parquet(self, key: str, data: bytes, **kwargs) -> bool:
        """Upload raw Parquet bytes to S3."""
        try:
            s3_key = self._resolve_key(key)
            self.s3.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=data,
                ContentType='application/octet-stream',
                ServerSideEncryption='AES256'
            )
            logger.debug(f"Parquet uploaded to S3: s3://{self.bucket}/{s3_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload Parquet to S3 {key}: {e}")
            return False

    def download_parquet(self, key: str) -> Optional[bytes]:
        """Download Parquet bytes from S3."""
        try:
            s3_key = self._resolve_key(key)
            response = self.s3.get_object(Bucket=self.bucket, Key=s3_key)
            data = response['Body'].read()
            logger.debug(f"Parquet downloaded from S3: s3://{self.bucket}/{s3_key}")
            return data
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                logger.debug(f"Parquet not found in S3: {key}")
                return None
            logger.error(f"Failed to download Parquet from S3 {key}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to download Parquet from S3 {key}: {e}")
            return None

    def get_uri(self, key: str) -> str:
        """Get s3:// URI for file."""
        s3_key = self._resolve_key(key)
        return f"s3://{self.bucket}/{s3_key}"


def get_storage(storage_type: str = None, **kwargs) -> BaseStorage:
    """Build a backend from arguments, falling back to the environment.

    Environment: STORAGE_TYPE ('file' or 's3'), S3_BUCKET, S3_PREFIX,
    S3_REGION (default us-east-1), LOGS_DIR (default logs).
    """
    if storage_type is None:
        storage_type = os.getenv('STORAGE_TYPE', 'file').lower()

    if storage_type == 's3':
        bucket = kwargs.get('bucket') or os.getenv('S3_BUCKET')
        if not bucket:
            raise ValueError("S3_BUCKET environment variable or bucket parameter required for S3 storage")

        prefix = kwargs.get('prefix') or os.getenv('S3_PREFIX', '')
        region = kwargs.get('region') or os.getenv('S3_REGION', 'us-east-1')

        return S3Storage(bucket=bucket, prefix=prefix, region=region)

    elif storage_type == 'file':
        base_dir = kwargs.get('base_dir') or os.getenv('LOGS_DIR', 'logs')
        return FileStorage(base_dir=base_dir)

    else:
        raise ValueError(f"Unknown storage type: {storage_type}. Use 'file' or 's3'")
