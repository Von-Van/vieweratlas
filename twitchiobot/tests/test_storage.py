from unittest.mock import MagicMock, patch

from storage import FileStorage, S3Storage


class TestS3Storage:
    """S3Storage setup against a mocked boto3 client."""

    def test_storage_startup_uses_bucket_metadata_not_unscoped_listing(self):
        """The least-privilege task role must not need an unscoped ListBucket."""
        s3 = MagicMock()
        with patch("storage.boto3.client", return_value=s3):
            storage = S3Storage(
                bucket="private-surveys",
                prefix="vieweratlas/raw/snapshots/v2",
            )

        s3.get_bucket_location.assert_called_once_with(Bucket="private-surveys")
        s3.head_bucket.assert_not_called()
        assert storage.prefix == "vieweratlas/raw/snapshots/v2/"


class TestFileStorage:
    """FileStorage uploads take (key, file_path) in that order."""

    def test_upload_file_writes_under_key(self, tmp_path):
        source_dir = tmp_path / "out"
        source_dir.mkdir()
        nodes_csv = source_dir / "graph_nodes.csv"
        nodes_csv.write_text("channel,viewers\na,1\n")

        storage = FileStorage(base_dir=str(tmp_path / "store"))
        assert storage.upload_file("curated/analysis/2026-03-11/graph_nodes.csv", str(nodes_csv)) is True

        landed = tmp_path / "store" / "curated" / "analysis" / "2026-03-11" / "graph_nodes.csv"
        assert landed.exists()
        assert landed.read_text() == "channel,viewers\na,1\n"
