-- Athena/Glue schema for ViewerAtlas site analytics.
--
-- Source: CloudFront standard access logs (v2), delivered by
-- enable-access-logs.sh. These logs deliberately carry no viewer IP address,
-- cookie or forwarded-for header, so every query below counts *requests*, never
-- people. There is no unique-visitor number to be had from this table, and
-- attempting to reconstruct one — say by fingerprinting user agent plus edge
-- location — would defeat the reason the fields were dropped.
--
-- Column order must match RECORD_FIELDS in enable-access-logs.sh: the delivery
-- writes positional TSV, so reordering one without the other silently shifts
-- every value into the wrong column.

CREATE EXTERNAL TABLE IF NOT EXISTS vieweratlas_access_logs (
  log_date STRING COMMENT 'Request date, YYYY-MM-DD (edge local)',
  log_time STRING COMMENT 'Request time, HH:MM:SS',
  edge_location STRING COMMENT 'CloudFront edge that served it',
  sc_bytes BIGINT COMMENT 'Bytes returned to the viewer',
  cs_method STRING COMMENT 'HTTP method',
  uri_stem STRING COMMENT 'Requested path',
  status INT COMMENT 'HTTP status returned to the viewer',
  referer STRING COMMENT 'Referring URL',
  user_agent STRING COMMENT 'URL-encoded user agent',
  uri_query STRING COMMENT 'Query string, or - when absent',
  result_type STRING COMMENT 'Hit, Miss, Error, RefreshHit, …',
  host_header STRING COMMENT 'Host the viewer requested',
  time_taken DOUBLE COMMENT 'Edge processing seconds'
)
PARTITIONED BY (
  year STRING,
  month STRING,
  day STRING
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY '\t'
LOCATION 's3://${S3_BUCKET}/${S3_PREFIX}analytics/cloudfront/'
TBLPROPERTIES (
  'skip.header.line.count'='2',
  'projection.enabled'='true',
  'projection.year.type'='integer',
  'projection.year.range'='2026,2036',
  'projection.year.digits'='4',
  'projection.month.type'='integer',
  'projection.month.range'='1,12',
  'projection.month.digits'='2',
  'projection.day.type'='integer',
  'projection.day.range'='1,31',
  'projection.day.digits'='2',
  'storage.location.template'='s3://${S3_BUCKET}/${S3_PREFIX}analytics/cloudfront/year=${year}/month=${month}/day=${day}/'
);

-- ═══════════════════════════════════════════════════════════════════════
-- Queries
--
-- Every one filters out the SPA's own asset traffic. One page view pulls a
-- hashed JS bundle, a CSS file and a data payload, so unfiltered request counts
-- read about an order of magnitude high.
-- ═══════════════════════════════════════════════════════════════════════

-- 1. Page views per day.
--    Bots are excluded crudely but usefully: most declare themselves.
SELECT
  log_date,
  COUNT(*) AS page_views
FROM vieweratlas_access_logs
WHERE cs_method = 'GET'
  AND status < 400
  AND uri_stem NOT LIKE '/assets/%'
  AND uri_stem NOT LIKE '/data/%'
  AND uri_stem NOT LIKE '%.ico'
  AND LOWER(user_agent) NOT LIKE '%bot%'
  AND LOWER(user_agent) NOT LIKE '%crawler%'
  AND LOWER(user_agent) NOT LIKE '%spider%'
GROUP BY log_date
ORDER BY log_date DESC
LIMIT 90;

-- 2. Which paths get requested.
--    Note: the distribution rewrites 403/404 to /index.html for SPA routing.
--    Confirm empirically whether a deep link to /map records as /map or as
--    /index.html before reading this as per-route traffic — if it collapses to
--    /index.html, only entry pages are visible here and route-level counts need
--    the first-party beacon instead.
SELECT
  uri_stem,
  COUNT(*) AS requests
FROM vieweratlas_access_logs
WHERE cs_method = 'GET'
  AND uri_stem NOT LIKE '/assets/%'
  AND LOWER(user_agent) NOT LIKE '%bot%'
GROUP BY uri_stem
ORDER BY requests DESC
LIMIT 50;

-- 3. Where visitors come from.
SELECT
  referer,
  COUNT(*) AS requests
FROM vieweratlas_access_logs
WHERE referer <> '-'
  AND referer NOT LIKE '%' || host_header || '%'   -- drop in-site navigation
  AND status < 400
GROUP BY referer
ORDER BY requests DESC
LIMIT 50;

-- 4. Which analysis windows people actually open.
--    The best available proxy for real app loads: one fetch per window the
--    visitor selects, so the 14d/90d rows measure use of the map's time filter.
--    Unsuffixed frontend-data.json is the canonical 30-day file fetched on load.
SELECT
  uri_stem,
  COUNT(*) AS fetches,
  ROUND(AVG(time_taken), 4) AS avg_seconds
FROM vieweratlas_access_logs
WHERE uri_stem LIKE '/data/frontend-data%'
  AND status < 400
GROUP BY uri_stem
ORDER BY fetches DESC;

-- 5. Errors and cache health.
--    A rising Miss share on /data/* usually means the analysis is republishing
--    more often than the 3600s max-TTL on that behavior.
SELECT
  result_type,
  status,
  COUNT(*) AS requests
FROM vieweratlas_access_logs
WHERE log_date >= date_format(current_date - interval '7' day, '%Y-%m-%d')
GROUP BY result_type, status
ORDER BY requests DESC;
