#!/bin/bash
# Safe rollback for the one-shot collector architecture.
#
# The previous collector revision used the retired IRC implementation and must
# never be reactivated. Rollback therefore pauses new surveys and preserves the
# last valid public frontend dataset while the current release is investigated.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

echo "Pausing ViewerAtlas survey collection."
echo "The website will continue serving its last valid public dataset."

# Emergency rollback must keep working even when ECS task definitions, IAM, or
# VPC egress are broken. This helper reads each existing schedule, preserves its
# timing/target configuration, and changes only its state.
bash "$SCRIPT_DIR/pause-schedules.sh" all

echo
echo "Rollback safety state applied:"
echo "  - Survey schedule: disabled"
echo "  - Analysis schedule: disabled"
echo "  - Retired IRC collector: not reactivated"
echo
echo "After a corrected image is deployed and smoke-tested, resume with:"
echo "  SURVEY_SCHEDULE_STATE=ENABLED ANALYSIS_SCHEDULE_STATE=ENABLED ./create-schedules.sh"
