#!/usr/bin/env bash
# Observability for Yukdhis: log-based metric, email channel, alert policies, uptime check.
#
#   bash infrastructure/gcp/monitoring.sh
#
# Needs PROJECT_ID (config.env) and API_URL (.state.env, written by deploy.sh services step).
# ALERT_EMAIL (config.env) is optional: without it no notification channel or alert policy is created.
# Idempotent: every resource is looked up by display name before it is created.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/config.env"
: "${PROJECT_ID:?set PROJECT_ID in config.env}"
API_SERVICE="${API_SERVICE:-yukdhis-api}"
ALERT_EMAIL="${ALERT_EMAIL:-}"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1
export CLOUDSDK_BILLING_QUOTA_PROJECT="$PROJECT_ID"
STATE="$HERE/.state.env"
if [ -f "$STATE" ]; then
  # shellcheck source=/dev/null
  source "$STATE"
fi
API_URL="${API_URL:-}"
gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable monitoring.googleapis.com logging.googleapis.com --project="$PROJECT_ID"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

METRIC="yukdhis_run_failed"
RUN_FAILED_POLICY="Yukdhis: pipeline run failed"
API_5XX_POLICY="Yukdhis: API 5xx responses"
UPTIME_NAME="yukdhis-api-health"
CHANNEL_NAME="Yukdhis on-call email"

# (a) log-based metric over the structured run_failed log line emitted by api/runs.py
if [ -z "$(gcloud logging metrics list --project="$PROJECT_ID" --filter="name=$METRIC" --format='value(name)')" ]; then
  gcloud logging metrics create "$METRIC" --project="$PROJECT_ID" \
    --description="Yukdhis pipeline runs that ended in run_failed" \
    --log-filter='jsonPayload.event="run_failed"'
else
  echo "log metric $METRIC already exists"
fi

# (e) uptime check on the API /health endpoint
if [ -z "$API_URL" ]; then
  echo "API_URL not set in $STATE (run 'deploy.sh services' first): skipping the uptime check"
else
  API_HOST="${API_URL#https://}"; API_HOST="${API_HOST%%/*}"
  if [ -z "$(gcloud monitoring uptime list-configs --project="$PROJECT_ID" --filter="displayName=$UPTIME_NAME" --format='value(name)')" ]; then
    gcloud monitoring uptime create "$UPTIME_NAME" --project="$PROJECT_ID" \
      --resource-type=uptime-url --resource-labels="host=$API_HOST,project_id=$PROJECT_ID" \
      --protocol=https --path=/health --period=5 --timeout=10 --validate-ssl=true
  else
    echo "uptime check $UPTIME_NAME already exists"
  fi
fi

# (b) email notification channel, then (c)/(d) alert policies
if [ -z "$ALERT_EMAIL" ]; then
  echo "ALERT_EMAIL is not set in config.env: skipping the notification channel and both alert policies"
  exit 0
fi
CHANNEL="$(gcloud beta monitoring channels list --project="$PROJECT_ID" \
  --filter="displayName=\"$CHANNEL_NAME\" AND labels.email_address=\"$ALERT_EMAIL\"" --format='value(name)' | head -n1)"
if [ -z "$CHANNEL" ]; then
  CHANNEL="$(gcloud beta monitoring channels create --project="$PROJECT_ID" --display-name="$CHANNEL_NAME" \
    --type=email --channel-labels="email_address=$ALERT_EMAIL" --format='value(name)')"
fi

policy_exists() {
  [ -n "$(gcloud monitoring policies list --project="$PROJECT_ID" --filter="displayName=\"$1\"" --format='value(name)')" ]
}

if policy_exists "$RUN_FAILED_POLICY"; then
  echo "alert policy '$RUN_FAILED_POLICY' already exists"
else
  cat > "$WORK/run_failed.json" <<JSON
{
  "displayName": "$RUN_FAILED_POLICY",
  "combiner": "OR",
  "notificationChannels": ["$CHANNEL"],
  "conditions": [{
    "displayName": "run_failed > 0 in 5 minutes",
    "conditionThreshold": {
      "filter": "metric.type=\"logging.googleapis.com/user/$METRIC\" AND resource.type=\"cloud_run_revision\"",
      "comparison": "COMPARISON_GT",
      "thresholdValue": 0,
      "duration": "0s",
      "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_SUM", "crossSeriesReducer": "REDUCE_SUM"}],
      "trigger": {"count": 1}
    }
  }],
  "documentation": {"mimeType": "text/markdown", "content": "A Yukdhis pipeline run ended in run_failed. Find it with: jsonPayload.event=\"run_failed\" in Cloud Logging (run_id and error are on the log line)."}
}
JSON
  gcloud monitoring policies create --project="$PROJECT_ID" --policy-from-file="$WORK/run_failed.json"
fi

if policy_exists "$API_5XX_POLICY"; then
  echo "alert policy '$API_5XX_POLICY' already exists"
else
  cat > "$WORK/api_5xx.json" <<JSON
{
  "displayName": "$API_5XX_POLICY",
  "combiner": "OR",
  "notificationChannels": ["$CHANNEL"],
  "conditions": [{
    "displayName": "Cloud Run $API_SERVICE 5xx > 0 in 5 minutes",
    "conditionThreshold": {
      "filter": "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"$API_SERVICE\" AND metric.labels.response_code_class=\"5xx\"",
      "comparison": "COMPARISON_GT",
      "thresholdValue": 0,
      "duration": "0s",
      "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_SUM", "crossSeriesReducer": "REDUCE_SUM"}],
      "trigger": {"count": 1}
    }
  }],
  "documentation": {"mimeType": "text/markdown", "content": "The $API_SERVICE Cloud Run service returned 5xx responses. Search Cloud Logging for jsonPayload.event=\"http_request\" AND jsonPayload.status>=500 to find the request_id."}
}
JSON
  gcloud monitoring policies create --project="$PROJECT_ID" --policy-from-file="$WORK/api_5xx.json"
fi
echo "monitoring configured for $PROJECT_ID"
