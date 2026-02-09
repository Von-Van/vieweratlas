-- Athena/Glue table schema for curated VOD presence snapshots
-- Creates external table to query Parquet snapshots via Athena

CREATE EXTERNAL TABLE IF NOT EXISTS vieweratlas_vod_snapshots (
  platform STRING COMMENT 'Platform name (twitch)',
  source STRING COMMENT 'Data source (vod or live)',
  channel STRING COMMENT 'Channel login name (lowercase)',
  channel_id STRING COMMENT 'Twitch channel ID',
  content_id STRING COMMENT 'VOD identifier (vod:12345)',
  bucket_start_ts STRING COMMENT 'ISO timestamp of bucket start (null for VOD)',
  offset_s INT COMMENT 'Offset from VOD start in seconds (null for live)',
  bucket_len_s INT COMMENT 'Bucket window size in seconds',
  chatters ARRAY<STRING> COMMENT 'List of unique chatters (lowercase)',
  viewer_count INT COMMENT 'Count of unique viewers in bucket',
  game_name STRING COMMENT 'Game/category name (Unknown for VOD)',
  title STRING COMMENT 'Stream title',
  started_at STRING COMMENT 'Stream start timestamp',
  timestamp STRING COMMENT 'Snapshot timestamp'
)
PARTITIONED BY (
  source_partition STRING COMMENT 'Source partition (vod)',
  channel_partition STRING COMMENT 'Channel partition'
)
STORED AS PARQUET
LOCATION 's3://${S3_BUCKET}/vieweratlas/curated/presence_snapshots/'
TBLPROPERTIES (
  'parquet.compression'='SNAPPY',
  'classification'='parquet',
  'projection.enabled'='true',
  'projection.source_partition.type'='enum',
  'projection.source_partition.values'='live,vod',
  'projection.channel_partition.type'='injected',
  'storage.location.template'='s3://${S3_BUCKET}/vieweratlas/curated/presence_snapshots/source=${source_partition}/channel=${channel_partition}/'
);

-- Add partitions (if not using partition projection)
-- MSCK REPAIR TABLE vieweratlas_vod_snapshots;

-- Example queries:

-- 1. Count total VOD snapshots by channel
SELECT 
  channel,
  COUNT(*) as snapshot_count,
  COUNT(DISTINCT content_id) as vod_count,
  SUM(viewer_count) as total_chatter_appearances
FROM vieweratlas_vod_snapshots
WHERE source = 'vod'
GROUP BY channel
ORDER BY snapshot_count DESC
LIMIT 20;

-- 2. Get VOD chatter distribution
SELECT 
  channel,
  content_id,
  AVG(viewer_count) as avg_chatters_per_bucket,
  MAX(viewer_count) as peak_chatters,
  COUNT(*) as bucket_count,
  COUNT(*) * bucket_len_s / 60 as vod_duration_minutes
FROM vieweratlas_vod_snapshots
WHERE source = 'vod'
GROUP BY channel, content_id, bucket_len_s
ORDER BY vod_duration_minutes DESC;

-- 3. Find overlap between live and VOD chatters
SELECT 
  live.chatter,
  COUNT(DISTINCT live.channel) as live_channels,
  COUNT(DISTINCT vod.channel) as vod_channels
FROM vieweratlas_vod_snapshots live
CROSS JOIN UNNEST(live.chatters) AS t(chatter)
LEFT JOIN (
  SELECT DISTINCT channel, chatter
  FROM vieweratlas_vod_snapshots
  CROSS JOIN UNNEST(chatters) AS t(chatter)
  WHERE source = 'vod'
) vod ON live.channel = vod.channel AND t.chatter = vod.chatter
WHERE live.source = 'live'
GROUP BY live.chatter
HAVING COUNT(DISTINCT live.channel) >= 2
ORDER BY live_channels DESC
LIMIT 100;

-- 4. VOD processing stats
SELECT 
  DATE(from_iso8601_timestamp(timestamp)) as processing_date,
  COUNT(DISTINCT content_id) as vods_processed,
  COUNT(DISTINCT channel) as unique_channels,
  SUM(viewer_count) as total_chatter_observations
FROM vieweratlas_vod_snapshots
WHERE source = 'vod'
GROUP BY DATE(from_iso8601_timestamp(timestamp))
ORDER BY processing_date DESC;
