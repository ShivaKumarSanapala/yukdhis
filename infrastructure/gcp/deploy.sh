#!/usr/bin/env bash
# Google Cloud deployment for Yukdhis - gcloud / firebase CLI only.
#
#   bash infrastructure/gcp/deploy.sh <step>      steps run in this order for "all":
#   apis iam network storage spanner cloudsql seed documentai dlp gemma memory build services pubsub eventarc web
#
# Steps are idempotent where gcloud allows ("already exists" is tolerated).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# shellcheck source=/dev/null
source "$HERE/config.env"
: "${PROJECT_ID:?set PROJECT_ID in config.env}"
export CLOUDSDK_CORE_DISABLE_PROMPTS=1   # never block on an interactive prompt
export CLOUDSDK_BILLING_QUOTA_PROJECT="$PROJECT_ID"
gcloud config set project "$PROJECT_ID" >/dev/null
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com iam.googleapis.com --project="$PROJECT_ID"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
API_SA="yukdhis-api@${PROJECT_ID}.iam.gserviceaccount.com"
ACTION_SA="yukdhis-actions@${PROJECT_ID}.iam.gserviceaccount.com"
EVENT_SA="yukdhis-events@${PROJECT_ID}.iam.gserviceaccount.com"
BUILD_SA="yukdhis-build@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/api"
SQL_CONN="${PROJECT_ID}:${REGION}:${CLOUDSQL_INSTANCE_NAME}"
STATE="$HERE/.state.env"; touch "$STATE"
# shellcheck source=/dev/null
source "$STATE"
ok() { "$@" 2>&1 | grep -v -E "already exists|ALREADY_EXISTS" || true; }
remember() { grep -v "^$1=" "$STATE" > "$STATE.tmp" || true; echo "$1=$2" >> "$STATE.tmp"; mv "$STATE.tmp" "$STATE"; export "$1=$2"; }

apis() {
  gcloud services enable aiplatform.googleapis.com run.googleapis.com cloudbuild.googleapis.com \
    artifactregistry.googleapis.com spanner.googleapis.com sqladmin.googleapis.com documentai.googleapis.com \
    dlp.googleapis.com cloudkms.googleapis.com secretmanager.googleapis.com pubsub.googleapis.com \
    eventarc.googleapis.com cloudscheduler.googleapis.com storage.googleapis.com servicenetworking.googleapis.com \
    compute.googleapis.com cloudquotas.googleapis.com firebase.googleapis.com firebasehosting.googleapis.com identitytoolkit.googleapis.com
}

iam() {
  for sa in yukdhis-api yukdhis-actions yukdhis-events; do ok gcloud iam service-accounts create "$sa" --display-name="$sa"; done
  for role in roles/aiplatform.user roles/spanner.databaseUser roles/cloudsql.client roles/cloudsql.instanceUser \
              roles/documentai.apiUser roles/dlp.user roles/secretmanager.secretAccessor roles/logging.logWriter; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$API_SA" --role="$role" --condition=None -q >/dev/null
  done
  for role in roles/cloudsql.client roles/cloudsql.instanceUser roles/dlp.user roles/logging.logWriter; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$ACTION_SA" --role="$role" --condition=None -q >/dev/null
  done
  for role in roles/eventarc.eventReceiver roles/run.invoker; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$EVENT_SA" --role="$role" --condition=None -q >/dev/null
  done
  # This org disables automatic service-agent grants: create and grant them explicitly.
  gcloud beta services identity create --service=eventarc.googleapis.com --project="$PROJECT_ID" >/dev/null
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --role=roles/eventarc.serviceAgent -q --condition=None \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-eventarc.iam.gserviceaccount.com" >/dev/null
  # Cloud Storage service agent publishes Eventarc events.
  gcloud storage service-agent --project="$PROJECT_ID" >/dev/null
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --role=roles/pubsub.publisher -q --condition=None \
    --member="serviceAccount:service-${PROJECT_NUMBER}@gs-project-accounts.iam.gserviceaccount.com" >/dev/null
}

network() {
  ok gcloud compute addresses create google-managed-services --global --purpose=VPC_PEERING --prefix-length=16 --network="$NETWORK"
  ok gcloud services vpc-peerings connect --service=servicenetworking.googleapis.com --ranges=google-managed-services --network="$NETWORK"
}

storage() {
  ok gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$API_SA" --role=roles/storage.objectAdmin >/dev/null
}

spanner() {
  ok gcloud spanner instances create "$SPANNER_INSTANCE" --config="regional-$REGION" --edition=ENTERPRISE \
    --processing-units="$SPANNER_PROCESSING_UNITS" --description="Yukdhis BIAN knowledge graph"
  ok gcloud spanner databases create "$SPANNER_DATABASE" --instance="$SPANNER_INSTANCE"
  if ! gcloud spanner databases ddl describe "$SPANNER_DATABASE" --instance="$SPANNER_INSTANCE" | grep -q "PROPERTY GRAPH"; then
    SPANNER_INSTANCE="$SPANNER_INSTANCE" SPANNER_DATABASE="$SPANNER_DATABASE" bash "$ROOT/infrastructure/spanner_graph/deploy.sh"
  fi
  GOOGLE_CLOUD_PROJECT="$PROJECT_ID" SPANNER_INSTANCE="$SPANNER_INSTANCE" SPANNER_DATABASE="$SPANNER_DATABASE" TENANT_ID="$TENANT_ID" \
    "$ROOT/venv/bin/python" "$ROOT/scripts/seed_spanner_graph.py"
}

cloudsql() {
  ok gcloud sql instances create "$CLOUDSQL_INSTANCE_NAME" --database-version=POSTGRES_16 --edition=ENTERPRISE --tier="$CLOUDSQL_TIER" \
    --region="$REGION" --network="projects/$PROJECT_ID/global/networks/$NETWORK" --no-assign-ip \
    --database-flags=cloudsql.iam_authentication=on --storage-auto-increase --backup
  ok gcloud sql databases create bank_ops --instance="$CLOUDSQL_INSTANCE_NAME"
  ok gcloud sql databases create yukdhis_app --instance="$CLOUDSQL_INSTANCE_NAME"
  ok gcloud sql users create "${API_SA%.gserviceaccount.com}" --instance="$CLOUDSQL_INSTANCE_NAME" --type=cloud_iam_service_account
  ok gcloud sql users create "${ACTION_SA%.gserviceaccount.com}" --instance="$CLOUDSQL_INSTANCE_NAME" --type=cloud_iam_service_account
  if ! gcloud secrets describe yukdhis-sql-admin >/dev/null 2>&1; then
    # Random admin password goes straight from /dev/urandom into Cloud SQL and Secret Manager; never printed.
    PW="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 28)"
    gcloud sql users set-password postgres --instance="$CLOUDSQL_INSTANCE_NAME" --password="$PW" >/dev/null
    printf '%s' "$PW" | gcloud secrets create yukdhis-sql-admin --data-file=- >/dev/null
    unset PW
  fi
}

seed() {  # runs inside the VPC as a Cloud Run job (Cloud SQL has private IP only)
  gcloud artifacts docker images describe "$IMAGE:latest" >/dev/null 2>&1 || build
  ok gcloud run jobs create yukdhis-seed --image="$IMAGE:latest" --region="$REGION" --service-account="$API_SA" \
    --network="$NETWORK" --subnet="$SUBNET" --vpc-egress=private-ranges-only \
    --set-secrets=CLOUDSQL_ADMIN_PASSWORD=yukdhis-sql-admin:latest \
    --set-env-vars="CLOUDSQL_INSTANCE=$SQL_CONN,API_DB_USER=${API_SA%.gserviceaccount.com},ACTION_DB_USER=${ACTION_SA%.gserviceaccount.com}" \
    --command=python --args=scripts/seed_cloudsql.py
  gcloud secrets add-iam-policy-binding yukdhis-sql-admin --member="serviceAccount:$API_SA" --role=roles/secretmanager.secretAccessor >/dev/null
  gcloud run jobs execute yukdhis-seed --region="$REGION" --wait
}

documentai() {
  if [ -z "${DOCUMENTAI_PROCESSOR:-}" ]; then
    local name
    name="$(curl -sf -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" -H "Content-Type: application/json" \
      "https://${DOCAI_LOCATION}-documentai.googleapis.com/v1/projects/${PROJECT_ID}/locations/${DOCAI_LOCATION}/processors" \
      -d '{"type":"LAYOUT_PARSER_PROCESSOR","displayName":"yukdhis-layout-parser"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["name"])')"
    remember DOCUMENTAI_PROCESSOR "$name"
  fi
}

dlp() {
  ok gcloud kms keyrings create yukdhis --location=global
  ok gcloud kms keys create dlp-wrap --keyring=yukdhis --location=global --purpose=encryption
  remember DLP_KMS_KEY "projects/${PROJECT_ID}/locations/global/keyRings/yukdhis/cryptoKeys/dlp-wrap"
  if ! gcloud secrets describe yukdhis-dlp-wrapped-key >/dev/null 2>&1; then
    # 256-bit data key generated and wrapped in one pipe; only the KMS-wrapped form is stored.
    head -c 32 /dev/urandom | gcloud kms encrypt --key=dlp-wrap --keyring=yukdhis --location=global \
      --plaintext-file=- --ciphertext-file=- | base64 | tr -d '\n' | gcloud secrets create yukdhis-dlp-wrapped-key --data-file=- >/dev/null
  fi
  gcloud beta services identity create --service=dlp.googleapis.com --project="$PROJECT_ID" >/dev/null
  for sa in "$API_SA" "$ACTION_SA" "service-${PROJECT_NUMBER}@dlp-api.iam.gserviceaccount.com"; do
    ok gcloud kms keys add-iam-policy-binding dlp-wrap --keyring=yukdhis --location=global \
      --member="serviceAccount:$sa" --role=roles/cloudkms.cryptoKeyDecrypter
  done
  gcloud secrets add-iam-policy-binding yukdhis-dlp-wrapped-key --member="serviceAccount:$ACTION_SA" \
    --role=roles/secretmanager.secretAccessor >/dev/null
}

gemma() {
  [ "${DEPLOY_GEMMA:-1}" = "1" ] || { echo "Skipping Gemma (DEPLOY_GEMMA=0); redaction falls back to DLP + heuristics."; return; }
  if [ -z "${GEMMA_VERTEX_ENDPOINT:-}" ]; then
    echo "Deploying $GEMMA_MODEL from Model Garden (accepts the Gemma terms of use on your behalf; ~20 min, L4 GPU billing applies)."
    gcloud ai model-garden models deploy --model="$GEMMA_MODEL" --region="$REGION" --machine-type="$GEMMA_MACHINE" \
      --accelerator-type=NVIDIA_L4 --accelerator-count=1 --endpoint-display-name=yukdhis-gemma-redactor --accept-eula --billing-project="$PROJECT_ID"
    remember GEMMA_VERTEX_ENDPOINT "projects/$PROJECT_ID/locations/$REGION/endpoints/$(gcloud ai endpoints list --region="$REGION" --filter='displayName=yukdhis-gemma-redactor' --format='value(name.basename())' | head -1)"
  fi
}

memory() {
  if [ -z "${MEMORY_BANK_AGENT_ENGINE_ID:-}" ]; then
    remember MEMORY_BANK_AGENT_ENGINE_ID "$(GOOGLE_CLOUD_PROJECT="$PROJECT_ID" GOOGLE_CLOUD_LOCATION="$REGION" \
      "$ROOT/venv/bin/python" "$ROOT/scripts/create_memory_bank.py")"
  fi
}

build() {
  ok gcloud artifacts repositories create "$ARTIFACT_REPO" --repository-format=docker --location="$REGION"
  # Dedicated build identity: org policy gives the default compute SA no roles.
  ok gcloud iam service-accounts create yukdhis-build --display-name=yukdhis-build
  for role in roles/logging.logWriter roles/storage.objectAdmin roles/artifactregistry.writer; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$BUILD_SA" --role="$role" --condition=None -q >/dev/null
  done
  gcloud builds submit "$ROOT" --config="$ROOT/cloudbuild.yaml" --service-account="projects/$PROJECT_ID/serviceAccounts/$BUILD_SA" --substitutions="SHORT_SHA=$(git -C "$ROOT" rev-parse --short HEAD)"
}

common_env() {
  echo "YUKDHIS_BACKEND=gcp,AGENT_MODE=agentic,TENANT_ID=$TENANT_ID,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$REGION,GOOGLE_GENAI_USE_VERTEXAI=1,\
SPANNER_INSTANCE=$SPANNER_INSTANCE,SPANNER_DATABASE=$SPANNER_DATABASE,CLOUDSQL_INSTANCE=$SQL_CONN,\
DOCUMENTAI_PROCESSOR=${DOCUMENTAI_PROCESSOR:-},DLP_KMS_KEY=${DLP_KMS_KEY:-},GEMMA_VERTEX_ENDPOINT=${GEMMA_VERTEX_ENDPOINT:-},\
GEMMA_BACKEND=$([ -n "${GEMMA_VERTEX_ENDPOINT:-}" ] && echo gcp || echo local),\
PUBSUB_TOPIC=projects/$PROJECT_ID/topics/$PUBSUB_TOPIC_NAME,CIRCULARS_BUCKET=$BUCKET,MEMORY_BANK_AGENT_ENGINE_ID=${MEMORY_BANK_AGENT_ENGINE_ID:-},\
AUTH_MODE=firebase,EVENTARC_SA_EMAIL=$EVENT_SA,APP_DB_GRANTEES=${API_SA%.gserviceaccount.com};${ACTION_SA%.gserviceaccount.com},ACTION_ENGINE_INVOKER_SA=$EVENT_SA,ALLOWED_EMAIL_DOMAIN=${ALLOWED_EMAIL_DOMAIN:-}"
}

services() {
  local net="--network=$NETWORK --subnet=$SUBNET --vpc-egress=private-ranges-only"
  # shellcheck disable=SC2086
  gcloud run deploy "$API_SERVICE" --image="$IMAGE:latest" --region="$REGION" --service-account="$API_SA" $net \
    --allow-unauthenticated --no-cpu-throttling --min-instances=1 --max-instances=5 --memory=2Gi --cpu=2 --timeout=900 \
    --set-env-vars="^|^$(common_env | tr ',' '|')|CLOUDSQL_IAM_USER=${API_SA%.gserviceaccount.com}|CORS_ORIGINS=https://${PROJECT_ID}.web.app,https://${PROJECT_ID}.firebaseapp.com" \
    --set-secrets=DLP_WRAPPED_KEY=yukdhis-dlp-wrapped-key:latest
  remember API_URL "$(gcloud run services describe "$API_SERVICE" --region="$REGION" --format='value(status.url)')"
  # Cloud Run serves both URLs; Eventarc and Scheduler may sign for either (auth also accepts the URL the caller used).
  local api_urls; api_urls="$(gcloud run services describe "$API_SERVICE" --region="$REGION" --format='value(metadata.annotations."run.googleapis.com/urls")' | tr -d '[]"')"
  gcloud run services update "$API_SERVICE" --region="$REGION" --update-env-vars="^|^SERVICE_AUDIENCE=${api_urls:-$API_URL}" >/dev/null
  # Action engine: private, only identity allowed to read customer contact columns.
  # shellcheck disable=SC2086
  gcloud run deploy "$ACTION_SERVICE" --image="$IMAGE:latest" --region="$REGION" --service-account="$ACTION_SA" $net \
    --no-allow-unauthenticated --max-instances=3 --memory=1Gi \
    --set-env-vars="$(common_env),ENABLE_ACTION_ENGINE=1,CLOUDSQL_IAM_USER=${ACTION_SA%.gserviceaccount.com}" \
    --set-secrets=DLP_WRAPPED_KEY=yukdhis-dlp-wrapped-key:latest
  remember ACTION_URL "$(gcloud run services describe "$ACTION_SERVICE" --region="$REGION" --format='value(status.url)')"
  gcloud run services update "$ACTION_SERVICE" --region="$REGION" --update-env-vars="SERVICE_AUDIENCE=$ACTION_URL" >/dev/null
  gcloud run services add-iam-policy-binding "$ACTION_SERVICE" --region="$REGION" --member="serviceAccount:$EVENT_SA" --role=roles/run.invoker >/dev/null
}

pubsub() {
  ok gcloud pubsub topics create "$PUBSUB_TOPIC_NAME"
  gcloud pubsub topics add-iam-policy-binding "$PUBSUB_TOPIC_NAME" --member="serviceAccount:$API_SA" --role=roles/pubsub.publisher >/dev/null
  ok gcloud pubsub subscriptions create yukdhis-action-engine --topic="$PUBSUB_TOPIC_NAME" \
    --push-endpoint="${ACTION_URL}/internal/actions/dispatch" --push-auth-service-account="$EVENT_SA" \
    --push-auth-token-audience="$ACTION_URL" --ack-deadline=120
}

eventarc() {
  ok gcloud eventarc triggers create yukdhis-watcher --location="$REGION" \
    --destination-run-service="$API_SERVICE" --destination-run-region="$REGION" --destination-run-path=/api/events/gcs \
    --event-filters="type=google.cloud.storage.object.v1.finalized" --event-filters="bucket=$BUCKET" \
    --service-account="$EVENT_SA"
  ok gcloud scheduler jobs create http yukdhis-watch-poll --location="$REGION" --schedule="*/15 * * * *" \
    --uri="${API_URL}/api/watch/poll" --http-method=POST --oidc-service-account-email="$EVENT_SA" --oidc-token-audience="$API_URL"
}

web() {
  cd "$ROOT/web"
  npm ci --silent
  VITE_API_BASE="$API_URL" npm run build
  npx --yes firebase-tools@latest projects:addfirebase "$PROJECT_ID" 2>/dev/null || true
  npx --yes firebase-tools@latest deploy --only hosting --project "$PROJECT_ID"
  echo "UI: https://${PROJECT_ID}.web.app  (enable Google sign-in under Firebase Authentication and add VITE_FIREBASE_* to web/.env.production)"
}

all() { apis; iam; network; storage; spanner; cloudsql; seed; documentai; dlp; gemma; memory; services; pubsub; eventarc; web; }

"${1:-all}"
