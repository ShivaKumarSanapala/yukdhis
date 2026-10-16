# Deployment (Google Cloud only)

Prerequisites: `gcloud` authenticated as a project Owner or Editor, `npm`, and the local `venv`.

```bash
cp infrastructure/gcp/config.env.example infrastructure/gcp/config.env   # set PROJECT_ID etc.
bash infrastructure/gcp/deploy.sh all
```

You can also run the steps one at a time, in this order:

| Step | Creates |
| --- | --- |
| `apis` | Enables Vertex AI, Run, Build, Artifact Registry, Spanner, SQL Admin, Document AI, DLP, KMS, Secret Manager, Pub/Sub, Eventarc, Scheduler, Firebase |
| `iam` | Service accounts `yukdhis-api` (agents), `yukdhis-actions` (PII hydration only), `yukdhis-events` (Eventarc/Scheduler/Pub/Sub push) |
| `network` | Private Services Access for private-IP Cloud SQL |
| `storage` | Circulars bucket (uniform access, public access prevented). Drop files in `incoming/` to trigger the Watcher |
| `spanner` | Enterprise-edition instance (100 PU), database, Spanner Graph DDL, BIAN seed |
| `cloudsql` | Postgres 16 with private IP, IAM DB auth, `bank_ops` + `yukdhis_app`, admin password in Secret Manager |
| `seed` | Builds the image and runs Cloud Run job `yukdhis-seed`, which loads the synthetic bank and grants `compliance_analyst` / `pii_hydrator` |
| `documentai` | Layout Parser processor |
| `dlp` | KMS key + wrapped 256-bit DLP key (generated and wrapped in one pipe; only the wrapped form is stored) |
| `gemma` | Gemma 2 9B-IT from Model Garden on one L4 (accepts the Gemma terms; set `DEPLOY_GEMMA=0` to skip) |
| `memory` | Agent Engine instance for Vertex AI Memory Bank |
| `services` | Cloud Run `yukdhis-api` (public; Firebase Auth enforced in-app) and `yukdhis-action-engine` (private) with direct VPC egress |
| `pubsub` | Approved-actions topic + authenticated push subscription to the action engine |
| `eventarc` | GCS finalize trigger → `/api/events/gcs`; Scheduler poll every 15 min |
| `web` | Builds the React app with `VITE_API_BASE` = Cloud Run URL, deploys Firebase Hosting (`/api/**` rewritten to Cloud Run) |

After `web`: in the Firebase console, enable Authentication → Google provider and add the hosting domain.
Put the web app config into `web/.env.production` (`VITE_FIREBASE_*`; these are public identifiers) and run
`deploy.sh web` again. Optionally set `ALLOWED_EMAIL_DOMAIN` to limit sign-in to the bank's Workspace domain.

## Cost notes

Spanner Graph needs the Enterprise edition (100 processing units is the minimum). The Gemma endpoint bills an L4 GPU
around the clock, and the API service keeps `--min-instances=1` with CPU always allocated so background runs finish.
Tear down after the demo:

```bash
gcloud ai endpoints list --region=us-central1
```

Undeploy the Gemma model listed there, then delete the Spanner instance and Cloud SQL instance.

## Runtime configuration

`deploy.sh services` sets every variable from `.env.example`: `YUKDHIS_BACKEND=gcp`, `AGENT_MODE=agentic`,
`AUTH_MODE=firebase`, plus resource names. `DLP_WRAPPED_KEY` comes from Secret Manager. There are no passwords for
the runtime databases, because Cloud SQL IAM authentication is used. Rollback is `gcloud run services update-traffic
yukdhis-api --to-revisions=<previous>=100`.

## CI

`cloudbuild.yaml` runs the tests, builds the image and pushes it to Artifact Registry. Connect it to a Cloud Build trigger on
`main`, then roll out with `deploy.sh services`.
