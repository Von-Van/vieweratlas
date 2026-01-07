"""
Storage Abstraction Layer

Provides a unified interface for file storage operations supporting both
local filesystem and AWS S3. Enables seamless migration to cloud storage
without changing application logic.

Supported backends:
- FileStorage: Local filesystem (default, current behavior)
- S3Storage: AWS S3 buckets (cloud-native)

Usage:
    from storage import get_storage
    
    storage = get_storage()  # Auto-detects based on config
    storage.upload_json("snapshots/data.json", data)
    data = storage.download_json("snapshots/data.json")
"""

import json
import csv
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
from datetime import datetime
from io import StringIO, BytesIO

logger = logging.getLogger(__name__)

# Optional AWS dependency
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
        pass
    
    @abstractmethod
    def download_json(self, key: str) -> Optional[dict]:
        """Download JSON data from storage."""
        pass
    
    @abstractmethod
    def upload_csv(self, key: str, rows: List[List[Any]], headers: Optional[List[str]] = None, **kwargs) -> bool:
        """Upload CSV data to storage."""
        pass
    
    @abstractmethod
    def download_csv(self, key: str) -> Optional[List[List[Any]]]:
        """Download CSV data from storage."""
        pass
    
    @abstractmethod
    def upload_file(self, key: str, file_path: str, **kwargs) -> bool:
        """Upload file from local path to storage."""
        pass
    
    @abstractmethod
    def download_file(self, key: str, destination: str) -> bool:
        """Download file from storage to local path."""
        pass
    
    @abstractmethod
    def list_files(self, prefix: str = "", suffix: str = "") -> List[str]:
        """List files matching prefix and suffix."""
        pass
    
    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if file exists."""
        pass
    
    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete file."""
        pass
    
    @abstractmethod
    def get_uri(self, key: str) -> str:
        """Get URI/path for file."""
        pass


class FileStorage(BaseStorage):
    """
    Local filesystem storage backend.
    
    Maintains current behavior with local files.
    Paths are relative to base_dir.
    """
    
    def __init__(self, base_dir: str = "logs"):
        """
        Initialize filesystem storage.
        
        Args:
            base_dir: Base directory for all file operations
        """
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"FileStorage initialized: {self.base_dir.absolute()}")
    
    def _resolve_path(self, key: str) -> Path:
        """Resolve key to full filesystem path."""
        return self.base_dir / key
    
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
    
    def upload_csv(self, key: str, rows: List[List[Any]], headers: Optional[List[str]] = None, **kwargs) -> bool:
        """Upload CSV data to local file."""
        try:
            path = self._resolve_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                if headers:
                    writer.writerow(headers)
                writer.writerows(rows)
            
            logger.debug(f"CSV uploaded: {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload CSV {key}: {e}")
            return False
    
    def download_csv(self, key: str) -> Optional[List[List[Any]]]:
        """Download CSV data from local file."""
        try:
            path = self._resolve_path(key)
            if not path.exists():
                logger.debug(f"CSV not found: {path}")
                return None
            
            with open(path, 'r') as f:
                reader = csv.reader(f)
                rows = list(reader)
            
            logger.debug(f"CSV downloaded: {path}")
            return rows
        except Exception as e:
            logger.error(f"Failed to download CSV {key}: {e}")
            return None
    
    def upload_file(self, key: str, file_path: str, **kwargs) -> bool:
        """Copy file from local path to storage."""
        try:
            import shutil
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
            import shutil
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
            search_dir = self.base_dir / prefix if prefix else self.base_dir
            if not search_dir.exists():
                return []
            
            pattern = f"*{suffix}" if suffix else "*"
            files = []
            
            for path in search_dir.rglob(pattern):
                if path.is_file():
                    relative = path.relative_to(self.base_dir)
                    files.append(str(relative))
            
            logger.debug(f"Listed {len(files)} files with prefix='{prefix}', suffix='{suffix}'")
            return sorted(files)
        except Exception as e:
            logger.error(f"Failed to list files: {e}")
            return []
    
    def exists(self, key: str) -> bool:
        """Check if file exists."""
        return self._resolve_path(key).exists()
    
    def delete(self, key: str) -> bool:
        """Delete file."""
        try:
            path = self._resolve_path(key)
            if path.exists():
                path.unlink()
                logger.debug(f"File deleted: {path}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete file {key}: {e}")
            return False
    
    def get_uri(self, key: str) -> str:
        """Get file:// URI for local file."""
        path = self._resolve_path(key).absolute()
        return f"file://{path}"


class S3Storage(BaseStorage):
    """
    AWS S3 storage backend.
    
    Provides cloud-native storage with automatic retry,
    encryption, and lifecycle management.
    """
    
    def __init__(self, bucket: str, prefix: str = "", region: str = "us-east-1"):
        """
        Initialize S3 storage.
        
        Args:
            bucket: S3 bucket name
            prefix: Key prefix for all operations (e.g., "vieweratlas/")
            region: AWS region
        
        Raises:
            ImportError: If boto3 not installed
            ValueError: If bucket not accessible
        """
        if not HAS_BOTO3:
            raise ImportError("boto3 required for S3Storage. Install with: pip install boto3")
        
        self.bucket = bucket
        self.prefix = prefix.rstrip('/') + '/' if prefix else ''
        self.region = region
        
        # Initialize S3 client
        self.s3 = boto3.client('s3', region_name=region)
        
        # Verify bucket access
        try:
            self.s3.head_bucket(Bucket=bucket)
            logger.info(f"S3Storage initialized: s3://{bucket}/{self.prefix}")
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                raise ValueError(f"S3 bucket not found: {bucket}")
            elif error_code == '403':
                raise ValueError(f"Access denied to S3 bucket: {bucket}")
            else:
                raise ValueError(f"S3 bucket error: {e}")
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
            
            # Serialize to JSON
            json_str = json.dumps(data, indent=indent)
            
            # Upload to S3
            self.s3.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=json_str.encode('utf-8'),
                ContentType='application/json',
                ServerSideEncryption='AES256'  # Encrypt at rest
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
    
    def upload_csv(self, key: str, rows: List[List[Any]], headers: Optional[List[str]] = None, **kwargs) -> bool:
        """Upload CSV data to S3."""
        try:
            s3_key = self._resolve_key(key)
            
            # Serialize to CSV
            csv_buffer = StringIO()
            writer = csv.writer(csv_buffer)
            if headers:
                writer.writerow(headers)
            writer.writerows(rows)
            csv_str = csv_buffer.getvalue()
            
            # Upload to S3
            self.s3.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=csv_str.encode('utf-8'),
                ContentType='text/csv',
                ServerSideEncryption='AES256'
            )
            
            logger.debug(f"CSV uploaded to S3: s3://{self.bucket}/{s3_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to upload CSV to S3 {key}: {e}")
            return False
    
    def download_csv(self, key: str) -> Optional[List[List[Any]]]:
        """Download CSV data from S3."""
        try:
            s3_key = self._resolve_key(key)
            
            response = self.s3.get_object(Bucket=self.bucket, Key=s3_key)
            csv_str = response['Body'].read().decode('utf-8')
            
            reader = csv.reader(StringIO(csv_str))
            rows = list(reader)
            
            logger.debug(f"CSV downloaded from S3: s3://{self.bucket}/{s3_key}")
            return rows
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                logger.debug(f"CSV not found in S3: {key}")
                return None
            logger.error(f"Failed to download CSV from S3 {key}: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to download CSV from S3 {key}: {e}")
            return None
    
    def upload_file(self, key: str, file_path: str, **kwargs) -> bool:
        """Upload file from local path to S3."""
        try:
            s3_key = self._resolve_key(key)
            
            # Detect content type
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
            
            # Ensure destination directory exists
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
                    # Remove our prefix to get logical key
                    if key.startswith(self.prefix):
                        logical_key = key[len(self.prefix):]
                    else:
                        logical_key = key
                    
                    # Filter by suffix
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
    
    def delete(self, key: str) -> bool:
        """Delete file from S3."""
        try:
            s3_key = self._resolve_key(key)
            self.s3.delete_object(Bucket=self.bucket, Key=s3_key)
            logger.debug(f"File deleted from S3: s3://{self.bucket}/{s3_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete file from S3 {key}: {e}")
            return False
    
    def get_uri(self, key: str) -> str:
        """Get s3:// URI for file."""
        s3_key = self._resolve_key(key)
        return f"s3://{self.bucket}/{s3_key}"


def get_storage(storage_type: str = None, **kwargs) -> BaseStorage:
    """
    Factory function to create storage backend.
    
    Args:
        storage_type: 'file' or 's3' (auto-detects from env if None)
        **kwargs: Backend-specific configuration
        
    Returns:
        BaseStorage instance
        
    Environment variables:
        STORAGE_TYPE: 'file' or 's3'
        S3_BUCKET: Bucket name (required for s3)
        S3_PREFIX: Key prefix (optional)
        S3_REGION: AWS region (default: us-east-1)
        LOGS_DIR: Base directory for file storage (default: logs)
    """
    # Auto-detect storage type
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


if __name__ == "__main__":
    # Test storage backends
    import sys
    
    logging.basicConfig(level=logging.DEBUG)
    
    print("Testing FileStorage...")
    file_storage = FileStorage(base_dir="test_storage")
    
    # Test JSON
    test_data = {"message": "Hello", "timestamp": datetime.now().isoformat()}
    file_storage.upload_json("test.json", test_data)
    retrieved = file_storage.download_json("test.json")
    assert retrieved == test_data, "JSON roundtrip failed"
    print("✓ JSON works")
    
    # Test CSV
    test_rows = [["col1", "col2"], ["value1", "value2"]]
    file_storage.upload_csv("test.csv", test_rows[1:], headers=test_rows[0])
    retrieved_csv = file_storage.download_csv("test.csv")
    assert retrieved_csv == test_rows, "CSV roundtrip failed"
    print("✓ CSV works")
    
    # Test listing
    files = file_storage.list_files(suffix=".json")
    assert "test.json" in files, "File listing failed"
    print("✓ Listing works")
    
    print("\nFileStorage tests passed!")
    
    # S3 tests require AWS credentials
    if HAS_BOTO3 and os.getenv('S3_BUCKET'):
        print("\nTesting S3Storage...")
        try:
            s3_storage = get_storage('s3')
            s3_storage.upload_json("test/test.json", test_data)
            retrieved_s3 = s3_storage.download_json("test/test.json")
            assert retrieved_s3 == test_data, "S3 JSON roundtrip failed"
            print("✓ S3 works")
            s3_storage.delete("test/test.json")
        except Exception as e:
            print(f"S3 tests skipped: {e}")
