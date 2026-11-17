# Deploying Agent Replay

Target: Google Cloud Run. One container serves the API and the built frontend, scales to zero, and costs very little for a demo.

**Status: nothing has been deployed.** The Dockerfile, compose file and both GitHub Actions workflows are written. No cloud resource has been created, and the image has not been built yet (the build environment had no Docker daemon). The commands below are the ones to run, in order. Each one that creates or changes a cloud resource is marked.

## How the data works on Cloud Run

- The image contains three read-only things: the frozen experiment results (`results/frozen/`), the seed database with the experiment's runs (`demo/seed.db`), and the recorded responses for offline mode (`demo/recordings/`).
- On start, the app copies the seed database to `/tmp/agent_replay.db` and writes visitor runs there. `/tmp` on Cloud Run is memory, and it is lost when the instance stops. That is intended: visitor runs are disposable, the experiment is in the image.
- **Max instances is 1.** SQLite has a single writer, and the limit of two concurrent runs is kept in the instance's memory. A second instance would have its own database and its own counter.
- **Min instances is 0 normally and 1 on showcase day**, so the first visitor does not wait for a cold start. Set the repository variable `PROD_MIN_INSTANCES` to `1` the day before, and back to `0` after.
- The trade-off: one instance means no redundancy, and a restart clears visitor runs. For a showcase demo that is the right side of the trade. A real multi-user service would move to Postgres.

## Run locally with Docker

```bash
cp .env.example .env
```

Fill in `OPENAI_API_KEY`, `OPENAI_MODEL` and `JUDGE_MODEL` in `.env`, then:

```bash
docker compose up --build
```

Open http://localhost:8000. To run with no network, using recorded responses:

```bash
OFFLINE=1 docker compose up --build
```

## One-time cloud setup

Set these in your shell first. Replace the values.

```bash
export PROJECT=your-gcp-project-id
export REGION=us-central1
export REPO=rajendergugulothu/agent-replay
```

1. Enable the services. **Changes the project.**

```bash
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com iamcredentials.googleapis.com --project "$PROJECT"
```

2. Create the image repository. **Creates a resource.**

```bash
gcloud artifacts repositories create agent-replay --repository-format=docker --location="$REGION" --project "$PROJECT"
```

3. Store the OpenAI key in Secret Manager. **Creates a resource.** The command reads the key from your terminal, so it does not land in your shell history.

```bash
read -s OPENAI_API_KEY && printf '%s' "$OPENAI_API_KEY" | gcloud secrets create openai-api-key --data-file=- --project "$PROJECT"; unset OPENAI_API_KEY
```

4. Create the runtime service account. It can read that one secret and nothing else. **Creates resources.**

```bash
gcloud iam service-accounts create agent-replay-run --project "$PROJECT"
```

```bash
gcloud secrets add-iam-policy-binding openai-api-key --member="serviceAccount:agent-replay-run@$PROJECT.iam.gserviceaccount.com" --role=roles/secretmanager.secretAccessor --project "$PROJECT"
```

5. Create the deploy service account used by GitHub Actions. **Creates resources.**

```bash
gcloud iam service-accounts create agent-replay-deploy --project "$PROJECT"
```

```bash
for role in roles/run.admin roles/artifactregistry.writer; do gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:agent-replay-deploy@$PROJECT.iam.gserviceaccount.com" --role="$role"; done
```

```bash
gcloud iam service-accounts add-iam-policy-binding "agent-replay-run@$PROJECT.iam.gserviceaccount.com" --member="serviceAccount:agent-replay-deploy@$PROJECT.iam.gserviceaccount.com" --role=roles/iam.serviceAccountUser --project "$PROJECT"
```

6. Set up Workload Identity Federation, so GitHub Actions can deploy without a stored key file. **Creates resources.**

```bash
gcloud iam workload-identity-pools create github --location=global --project "$PROJECT"
```

```bash
gcloud iam workload-identity-pools providers create-oidc github --location=global --workload-identity-pool=github --issuer-uri=https://token.actions.githubusercontent.com --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" --attribute-condition="assertion.repository=='$REPO'" --project "$PROJECT"
```

```bash
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
```

```bash
gcloud iam service-accounts add-iam-policy-binding "agent-replay-deploy@$PROJECT.iam.gserviceaccount.com" --role=roles/iam.workloadIdentityUser --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$REPO" --project "$PROJECT"
```

7. Add repository variables in GitHub (Settings, Secrets and variables, Actions, Variables). None of these is a secret.

| Variable | Value |
|---|---|
| `GCP_PROJECT_ID` | your project id |
| `GCP_REGION` | for example `us-central1` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/<project number>/locations/global/workloadIdentityPools/github/providers/github` |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | `agent-replay-deploy@<project id>.iam.gserviceaccount.com` |
| `OPENAI_MODEL`, `JUDGE_MODEL` | the model ids used in the experiment |
| `DEMO_SCENARIOS` | the three demo scenario ids, comma separated |
| `DAILY_TOKEN_LIMIT` | optional, default 2000000 |
| `PROD_MIN_INSTANCES` | `0`, or `1` on showcase day |

The deploy workflow does nothing until `GCP_PROJECT_ID` is set.

## Cost safety

1. Set a monthly spending limit on the OpenAI account itself. This is the only limit that cannot be bypassed by a bug in this app.
2. Create a billing budget with alerts on the Google Cloud project. **Creates a resource.**

```bash
gcloud billing budgets create --billing-account=YOUR_BILLING_ACCOUNT_ID --display-name="agent-replay" --budget-amount=20USD --threshold-rule=percent=0.5 --threshold-rule=percent=0.9
```

3. `DEMO_MODE=1` is the default in the image and in the deploy workflow. In demo mode only the scenarios in `DEMO_SCENARIOS` can be started.

## How a deploy happens

- Every pull request runs `ci.yml`: lint (ruff, eslint), type checks (mypy, tsc), backend and frontend tests, the browser test against the fake model, `pip-audit`, `npm audit`, gitleaks, and an image build.
- A merge to `main` runs `deploy.yml`: build the image, push it to Artifact Registry, deploy it to the staging service `agent-replay-staging`, run the live smoke test against staging, and only then deploy the same image to the production service `agent-replay`. The last step prints the production URL.

The smoke test starts one real run on staging, so each deploy spends a few thousand tokens.

## Roll back

List revisions:

```bash
gcloud run revisions list --service agent-replay --region "$REGION" --project "$PROJECT"
```

Send all traffic to an earlier one:

```bash
gcloud run services update-traffic agent-replay --to-revisions=REVISION_NAME=100 --region "$REGION" --project "$PROJECT"
```

## Rotate the API key

Create the new key in the OpenAI dashboard, then add it as a new secret version:

```bash
read -s OPENAI_API_KEY && printf '%s' "$OPENAI_API_KEY" | gcloud secrets versions add openai-api-key --data-file=- --project "$PROJECT"; unset OPENAI_API_KEY
```

Start a new revision so the service picks it up:

```bash
gcloud run services update agent-replay --update-secrets=OPENAI_API_KEY=openai-api-key:latest --region "$REGION" --project "$PROJECT"
```

Then revoke the old key in the OpenAI dashboard.

## What goes into the image, and when

The image is only as current as the three data folders at build time. After the final experiment:

```bash
make export ID=nov-final
```

```bash
make freeze ID=nov-final VERSION=v1
```

```bash
make record
```

Commit `results/frozen/`, `demo/seed.db` and `demo/recordings/`, then merge. `make preflight` checks that all three are in place.
