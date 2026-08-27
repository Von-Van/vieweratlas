#!/bin/bash
# Measure overlap_threshold for every published analysis window and print the
# config block to paste into the production preset (and YAML for local runs).
#
# Why this wrapper exists rather than running sweep_threshold.py three times:
#
#   1. sweep_threshold.py downloads the whole snapshot prefix to a fresh temp
#      directory on every invocation. Three windows over ~90 days of Parquet is
#      three full downloads and three lots of S3 egress for identical bytes.
#      This downloads once and points all three sweeps at the same local copy.
#
#   2. The sweep's own defaults are exploratory (resolution 1.2,
#      min-community-size 3, no author or observation floor). Production builds
#      the graph with get_rigorous_config: resolution 1.0, min_community_size 10,
#      min_channel_viewers 10, min_channel_observations 3. Sweeping with the
#      defaults calibrates against a graph the pipeline never builds, so the
#      numbers below are pinned to the rigorous preset instead. If you change
#      that preset, change these to match.
#
# Usage — no arguments needed; the snapshot location is read from
# infrastructure/aws/.env, the same file the deployment scripts use:
#
#   twitchiobot/scripts/calibrate_windows.sh
#
# Or point it somewhere explicitly:
#
#   ./calibrate_windows.sh s3://my-bucket/vieweratlas/raw/snapshots/v2/
#   ./calibrate_windows.sh /path/to/local/root        # skips the download
#   WINDOWS="14 30" ./calibrate_windows.sh            # subset of windows

set -euo pipefail
export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# Load the deployment .env the way the infrastructure/aws scripts do, so this
# works from either directory without exporting S3_BUCKET by hand. Prefers a
# .env in the working directory (running from infrastructure/aws), then falls
# back to the deployment file regardless of where it was invoked from.
load_env_file() {
    local env_file=""
    if [ -f ".env" ]; then
        env_file=".env"
    elif [ -f "${SCRIPT_DIR}/../infrastructure/aws/.env" ]; then
        env_file="${SCRIPT_DIR}/../infrastructure/aws/.env"
    else
        return
    fi

    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|'#'*) continue ;;
        esac
        line="${line#export }"
        if [[ ! "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            continue
        fi
        local key="${line%%=*}"
        local value="${line#*=}"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
        export "$key=$value"
    done < "$env_file"
}

load_env_file

# Keep in step with analysis_windows in config/config.yaml.
WINDOWS="${WINDOWS:-14 30 90}"

# Keep in step with get_rigorous_config in src/config.py.
RESOLUTION="${RESOLUTION:-1.0}"
MIN_COMMUNITY_SIZE="${MIN_COMMUNITY_SIZE:-10}"
MIN_AUTHORS="${MIN_AUTHORS:-10}"
MIN_OBSERVATIONS="${MIN_OBSERVATIONS:-3}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[ERROR]${NC} $1" >&2; exit 1; }

S3_BUCKET="${S3_BUCKET:-}"
S3_PREFIX="${S3_PREFIX:-}"
S3_KEY_PREFIX="${S3_PREFIX%/}"
[ -z "$S3_KEY_PREFIX" ] || S3_KEY_PREFIX="${S3_KEY_PREFIX}/"

SOURCE="${1:-}"
if [ -z "$SOURCE" ]; then
    [ -n "$S3_BUCKET" ] || fail "No source given and S3_BUCKET is not set. Either pass a path:
          $0 <dir-or-s3-uri>
       or run it where infrastructure/aws/.env can be found."
    SOURCE="s3://${S3_BUCKET}/${S3_KEY_PREFIX}raw/snapshots/v2/"
    info "No source given; using ${SOURCE}"
fi

# A bare s3:// means the caller expanded $S3_BUCKET in a shell that never had it.
case "$SOURCE" in
    s3:// | s3:///*)
        fail "Source resolved to '${SOURCE}' — S3_BUCKET was empty when your shell
       expanded the command. Run this with no arguments and let the script
       read the deployment .env itself."
        ;;
esac

OUT_DIR="$(mktemp -d)"
CLEANUP_DOWNLOAD=""

if [[ "$SOURCE" == s3://* ]]; then
    # Rebuild the raw/snapshots prefix locally: `aws s3 cp --recursive` strips
    # the source prefix, and the aggregator lists keys under raw/snapshots.
    DOWNLOAD_ROOT="$(mktemp -d)"
    CLEANUP_DOWNLOAD="$DOWNLOAD_ROOT"
    KEY="${SOURCE#s3://}"; KEY="${KEY#*/}"; KEY="${KEY%/}"
    case "$KEY" in
        *raw/snapshots*)
            # Keep everything from raw/snapshots onward, e.g. raw/snapshots/v2.
            DEST="${DOWNLOAD_ROOT}/raw/snapshots${KEY#*raw/snapshots}"
            ;;
        *)
            fail "URI must point at or inside raw/snapshots/ — got ${SOURCE}"
            ;;
    esac
    mkdir -p "$DEST"
    info "Downloading once from ${SOURCE}"
    info "  -> ${DEST}"
    aws s3 cp "$SOURCE" "$DEST" --recursive --only-show-errors || \
        fail "Download failed. Check the URI points at raw/snapshots/v2/"
    LOCAL_ROOT="$DOWNLOAD_ROOT"
    BYTES=$(du -sh "$DOWNLOAD_ROOT" | cut -f1)
    info "Downloaded ${BYTES}; all ${WINDOWS// /, }-day sweeps reuse it."
else
    [ -d "$SOURCE" ] || fail "Not a directory: $SOURCE"
    LOCAL_ROOT="$SOURCE"
    info "Using local snapshots at ${LOCAL_ROOT}"
fi

echo ""
info "Sweeping with the rigorous preset's filters:"
info "  resolution=${RESOLUTION} min_community_size=${MIN_COMMUNITY_SIZE} \
min_authors=${MIN_AUTHORS} min_observations=${MIN_OBSERVATIONS}"
echo ""

RESULTS=""
FAILED=""
SHORT=""

for window in $WINDOWS; do
    echo "════════════════════════════════════════════════════════════════"
    info "Window: ${window} days"
    echo "════════════════════════════════════════════════════════════════"

    log_file="${OUT_DIR}/sweep-${window}d.txt"
    if ! "$PYTHON_BIN" "${SCRIPT_DIR}/sweep_threshold.py" "$LOCAL_ROOT" \
            --window-days "$window" \
            --resolution "$RESOLUTION" \
            --min-community-size "$MIN_COMMUNITY_SIZE" \
            --min-authors "$MIN_AUTHORS" \
            --min-observations "$MIN_OBSERVATIONS" \
            --mode shared_count 2>&1 | tee "$log_file"; then
        warn "Sweep failed for ${window}d"
        FAILED="${FAILED} ${window}"
        continue
    fi

    # A window wider than the retained data silently becomes a narrower sweep
    # and still prints a confident threshold. That is the dangerous case — the
    # number looks calibrated and is not — so compare what was asked for against
    # what the aggregator actually resolved.
    span_line=$(grep -o 'window: [0-9-]* \.\. [0-9-]*' "$log_file" | tail -1 || true)
    if [ -n "$span_line" ]; then
        span_start="${span_line#window: }"; span_start="${span_start%% ..*}"
        span_end="${span_line##*.. }"
        actual_days=$("$PYTHON_BIN" -c "
import datetime, sys
start = datetime.date.fromisoformat(sys.argv[1])
end = datetime.date.fromisoformat(sys.argv[2])
print((end - start).days + 1)
" "$span_start" "$span_end" 2>/dev/null || true)
        # An unparseable span is not worth aborting a long sweep over; the
        # threshold above is still usable, it just goes unchecked.
        if [ -n "$actual_days" ] && [ "$actual_days" -lt "$window" ]; then
            SHORT="${SHORT}${window}(${actual_days}d) "
            warn "Requested ${window}d but only ${actual_days} days of surveys exist"
            warn "(${span_start} .. ${span_end}). This is a ${actual_days}-day"
            warn "calibration wearing a ${window}-day label."
        fi
    fi

    suggested=$(grep -o 'Suggested overlap_threshold (shared_count): [0-9]*' "$log_file" \
                | tail -1 | grep -o '[0-9]*$' || true)
    if [ -n "$suggested" ]; then
        RESULTS="${RESULTS}${window}:${suggested} "
    else
        warn "No threshold kept enough of the ${window}d graph connected."
        FAILED="${FAILED} ${window}"
    fi
    echo ""
done

echo "════════════════════════════════════════════════════════════════"
info "Full sweep output kept in ${OUT_DIR}/"
echo ""

if [ -n "$RESULTS" ]; then
    # The scheduled task runs `main.py analyze rigorous`, so config/config.yaml
    # is never read in production. The numbers have to go in the preset.
    echo "Add to get_rigorous_config() in src/config.py, inside AnalysisConfig(:"
    echo ""
    echo "    window_overlap_thresholds={"
    for pair in $RESULTS; do
        echo "        ${pair%%:*}: ${pair##*:},"
    done
    echo "    },"
    echo ""
    echo "Then rebuild and redeploy the analysis image — the preset is baked in,"
    echo "so an edit alone changes nothing that is running."
    echo ""
    echo 'For local `analyze config.yaml` runs, the same values in YAML form:'
    echo ""
    echo "  window_overlap_thresholds:"
    for pair in $RESULTS; do
        echo "    ${pair%%:*}: ${pair##*:}"
    done
    echo ""
    echo "Then confirm the widest window no longer logs EDGE_CAP_BOUND on the"
    echo "next analysis run. If it still does, raise that window's threshold"
    echo "rather than frontend_max_edges: the cap exists so the browser is not"
    echo "asked to draw a graph nobody can read."
fi

if [ -n "$SHORT" ]; then
    echo ""
    warn "Short of data:${SHORT}"
    warn "Those thresholds describe the days that exist, not the window they are"
    warn "labelled with. They will drift as surveys accumulate — re-run this once"
    warn "each window is genuinely full."
fi

if [ -n "$FAILED" ]; then
    echo ""
    warn "No suggestion for:${FAILED}"
    warn "Usually means too few surveys in that window. Check the 'window:' line"
    warn "in the sweep output — a 90d sweep over 40 days of data is really a 40d"
    warn "sweep, and its threshold should not be trusted until the data catches up."
fi

[ -z "$CLEANUP_DOWNLOAD" ] || rm -rf "$CLEANUP_DOWNLOAD"
