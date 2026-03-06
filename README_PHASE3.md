# Clara Pipeline — Phase 3
## n8n Orchestration Layer

**Version:** 3.1 (fixed)
**Depends on:** Phase 2 (`clara_pipeline_fixed/`) — scripts must be present
**Orchestrator:** n8n (self-hosted via Docker, free)
**LLM Backend:** Ollama (local, zero-cost)
**Status:** Phase 3 complete — 3 workflows + Docker setup + import scripts

---

## What Phase 3 Adds

Phase 1 defined schemas and prompts.
Phase 2 built Python scripts (Script 1, 2, 3, run_all.py).
**Phase 3 wires everything into a visual, webhook-driven workflow** using n8n running locally in Docker.

Each pipeline becomes a live HTTP endpoint:

| Endpoint | Workflow | What it does |
|----------|----------|--------------|
| `POST /webhook/clara/demo` | Pipeline A | Demo transcript → v1 memo + Retell spec + task tracker item |
| `POST /webhook/clara/onboarding` | Pipeline B | Onboarding transcript → v2 + changelog |
| `POST /webhook/clara/batch` | Pipeline BATCH | Run all 10 transcripts end-to-end |

---

## Architecture and Data Flow

```
                        ┌──────────────────────────────────────────────┐
                        │              n8n (Docker, port 5678)         │
                        │                                              │
  POST /clara/demo ───► │  ┌─────────────────────────────────────────┐ │
                        │  │  Pipeline A: Demo → v1                  │ │
                        │  │                                         │ │
                        │  │  Webhook → Validate → Script1 (Exec)   │ │
                        │  │        → Parse → Script2 (Exec)        │ │
                        │  │        → Parse → Read Files             │ │
                        │  │        → Create Task Tracker Item       │ │
                        │  │        → Log → Respond                  │ │
                        │  │  [On Workflow Error] → Error Handler    │ │
                        │  │        → Error Response                 │ │
                        │  └──────────────┬──────────────────────────┘ │
                        │                 │                            │
POST /clara/onboarding ►│  ┌──────────────▼──────────────────────────┐ │
                        │  │  Pipeline B: Onboarding → v2            │ │
                        │  │                                         │ │
                        │  │  Webhook → Validate (v1 check)          │ │
                        │  │        → Idempotency check              │ │
                        │  │        → Script3 (Exec)                 │ │
                        │  │        → Parse → Read Changelog         │ │
                        │  │        → Log → Respond                  │ │
                        │  │  [On Workflow Error] → Error Handler    │ │
                        │  └──────────────┬──────────────────────────┘ │
                        │                 │                            │
  POST /clara/batch ───►│  ┌──────────────▼──────────────────────────┐ │
                        │  │  Pipeline BATCH                         │ │
                        │  │                                         │ │
                        │  │  Webhook → Validate → Ollama Health     │ │
                        │  │        → run_all.py (Exec)              │ │
                        │  │        → Parse → Log → Respond          │ │
                        │  │  [On Workflow Error] → Error Handler    │ │
                        │  └─────────────────────────────────────────┘ │
                        └──────────────────────────────────────────────┘
                                         │
                                 ┌───────▼────────┐
                                 │   Ollama       │  ← runs on host
                                 │   (port 11434) │
                                 └───────┬────────┘
                                         │
                          ┌──────────────▼──────────────┐
                          │   clara_pipeline/ (scripts) │
                          │   /app/clara_pipeline/      │
                          │   (mounted into n8n container)│
                          │                             │
                          │   outputs/accounts/         │
                          │     CLARA-2026-001/         │
                          │       v1/                   │
                          │         account_memo.json   │
                          │         retell_agent_spec.json│
                          │       v2/                   │
                          │         account_memo.json   │
                          │         retell_agent_spec.json│
                          │         changes.md          │
                          │         changes.json        │
                          │   outputs/tasks.json        │  ← task tracker
                          │   outputs/pipeline_log.json │
                          └─────────────────────────────┘
```

---

## Directory Structure

```
phase3_n8n/
├── README_PHASE3.md                        ← This file
├── docker-compose.yml                      ← n8n Docker setup
├── setup.sh                                ← One-command setup (recommended)
├── .env.example                            ← Environment variable template
├── workflows/
│   ├── pipeline_A_demo_to_v1.json          ← n8n workflow: Pipeline A
│   ├── pipeline_B_onboarding_to_v2.json    ← n8n workflow: Pipeline B
│   └── pipeline_BATCH_run_all.json         ← n8n workflow: Batch runner
├── scripts/
│   ├── import_workflows.sh                 ← Auto-importer script
│   └── verify_setup.sh                     ← Setup verification script
└── clara_pipeline/                         ← COPY of Phase 2 output
    (contents of clara_pipeline_fixed/ go here)
```

---

## Prerequisites

### 1. Docker + Docker Compose
```bash
# macOS
brew install docker
# Or: https://docs.docker.com/get-docker/

docker --version          # should show 20.x+
docker compose version    # should show 2.x+
```

### 2. Ollama (running on your host machine)
```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# Pull the model
ollama pull mistral

# Start Ollama (keep running in background)
ollama serve
```

Verify Ollama is running:
```bash
curl http://localhost:11434/api/tags
# Should return JSON with model list
```

---

## Setup Instructions (Step by Step)

### Option A — One-command setup (recommended)

```bash
# 1. Place clara_pipeline_fixed/ contents at ./clara_pipeline/
cp -r /path/to/clara_pipeline_fixed ./clara_pipeline

# 2. Run setup
chmod +x setup.sh
./setup.sh
```

`setup.sh` will: validate prerequisites → check `clara_pipeline/` → start n8n → import workflows.
It will **fail loudly** if `clara_pipeline/` is missing, with a clear message on how to fix it.

### Option B — Manual step-by-step

#### Step 1 — Prepare the project directory

```bash
# Your structure should look like:
phase3_n8n/
├── docker-compose.yml
├── workflows/
├── scripts/
└── clara_pipeline/    ← copy Phase 2 contents here

cp -r /path/to/clara_pipeline_fixed ./clara_pipeline
```

#### Step 2 — Start n8n

```bash
cd phase3_n8n

docker compose up -d

# Verify it started:
docker compose ps
# Should show: clara-n8n   Up   0.0.0.0:5678->5678/tcp
```

#### Step 3 — Import the workflows

```bash
chmod +x scripts/import_workflows.sh
./scripts/import_workflows.sh

# Expected output:
# ✓ n8n is ready
# ✓ Imported: pipeline_A_demo_to_v1.json
# ✓ Imported: pipeline_B_onboarding_to_v2.json
# ✓ Imported: pipeline_BATCH_run_all.json
```

#### Step 4 — Activate workflows in n8n UI

1. Open http://localhost:5678
2. Click on each workflow
3. Toggle the **Active** switch (top right) to ON for all three

#### Step 5 — Set environment variables (optional override)

```bash
# In docker-compose.yml, update these if needed:
- OLLAMA_URL=http://host.docker.internal:11434   # default
- OLLAMA_MODEL=mistral                            # default
```

---

## Running the Pipeline

### Pipeline A — Single Demo Call

```bash
curl -X POST http://localhost:5678/webhook/clara/demo \
  -H "Content-Type: application/json" \
  -d '{
    "transcript_path": "/app/clara_pipeline/data/demo/client_a.txt",
    "stage": "demo_call"
  }'
```

**Response:**
```json
{
  "status": "ok",
  "pipeline": "A",
  "account_id": "CLARA-2026-001",
  "company_name": "Acme Fire Protection",
  "agent_name": "Clara - Acme Fire Protection",
  "extraction_confidence": "medium",
  "spec_status": "draft",
  "go_live_ready": false,
  "unknowns_count": 3,
  "task_tracker": {
    "task_id": "TASK-CLARA-2026-001",
    "status": "pending_onboarding",
    "file": "/app/clara_pipeline/outputs/tasks.json"
  },
  "outputs": {
    "memo": "/app/clara_pipeline/outputs/accounts/CLARA-2026-001/v1/account_memo.json",
    "spec": "/app/clara_pipeline/outputs/accounts/CLARA-2026-001/v1/retell_agent_spec.draft.json"
  },
  "next_step": "Resolve unknowns via onboarding call"
}
```

### Pipeline B — Single Onboarding Call

```bash
curl -X POST http://localhost:5678/webhook/clara/onboarding \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "CLARA-2026-001",
    "transcript_path": "/app/clara_pipeline/data/onboarding/client_a.txt"
  }'
```

### Pipeline BATCH — All 10 Transcripts

```bash
# Auto-match by filename stem
curl -X POST http://localhost:5678/webhook/clara/batch \
  -H "Content-Type: application/json" \
  -d '{
    "demo_dir": "/app/clara_pipeline/data/demo",
    "onboarding_dir": "/app/clara_pipeline/data/onboarding"
  }'

# Using explicit pairs file
curl -X POST http://localhost:5678/webhook/clara/batch \
  -H "Content-Type: application/json" \
  -d '{
    "pairs_file": "/app/clara_pipeline/data/pairs.json"
  }'

# Dry run (verify pairing without Ollama calls)
curl -X POST http://localhost:5678/webhook/clara/batch \
  -H "Content-Type: application/json" \
  -d '{
    "demo_dir": "/app/clara_pipeline/data/demo",
    "onboarding_dir": "/app/clara_pipeline/data/onboarding",
    "dry_run": true
  }'
```

### Force Re-run (Override Idempotency)

```bash
curl -X POST http://localhost:5678/webhook/clara/onboarding \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "CLARA-2026-001",
    "transcript_path": "/app/clara_pipeline/data/onboarding/client_a.txt",
    "force": true
  }'
```

---

## Task Tracker

Pipeline A automatically creates a tracking item in `outputs/tasks.json` after each demo call. This is a zero-cost local alternative to Asana/Trello that fulfills the PDF requirement.

**Task file location:** `./clara_pipeline/outputs/tasks.json`

**Example task entry:**
```json
{
  "task_id": "TASK-CLARA-2026-001",
  "account_id": "CLARA-2026-001",
  "company_name": "Acme Fire Protection",
  "agent_name": "Clara - Acme Fire Protection",
  "status": "pending_onboarding",
  "go_live_ready": false,
  "unknowns_count": 3,
  "next_action": "Schedule onboarding call — 3 unknown(s) to resolve",
  "created_at": "2026-01-15T10:30:00Z",
  "updated_at": "2026-01-15T10:30:00Z"
}
```

**Task statuses:**
- `pending_onboarding` — demo processed, onboarding call still needed
- `ready_for_retell_import` — v1 spec is complete, ready for Retell UI import

**To upgrade to Asana or Trello:** Replace the "Create Task Tracker Item" node in Pipeline A with n8n's native Asana or Trello node. The node's output JSON (`account_id`, `company_name`, `next_action`, `spec_status`) maps directly to task fields.

---

## Retell Manual Import Instructions

After Pipeline A (or B) completes and `go_live_ready: true` is returned, follow these steps to configure the agent in Retell:

### Step 1 — Locate the Agent Spec File

The generated spec is at:
```
./clara_pipeline/outputs/accounts/<ACCOUNT_ID>/v1/retell_agent_spec.json
# or for v2 after onboarding:
./clara_pipeline/outputs/accounts/<ACCOUNT_ID>/v2/retell_agent_spec.json
```

Open the file and note these fields: `system_prompt`, `agent_name`, `voice_style`, `key_variables`, `call_transfer_protocol`, `fallback_protocol`.

### Step 2 — Create a Retell Account

1. Go to [app.retell.ai](https://app.retell.ai) and sign up for a free account.
2. Verify your email and log in.

### Step 3 — Create a New Agent

1. In the Retell dashboard, click **Agents** in the left sidebar.
2. Click **Create Agent** (top right).
3. Select **Custom LLM** or **Retell LLM** as the agent type.

### Step 4 — Paste the System Prompt

1. In the agent editor, locate the **System Prompt** field.
2. Open your `retell_agent_spec.json` and copy the value of `system_prompt`.
3. Paste it into the System Prompt field in Retell.

### Step 5 — Configure Voice and Language

From the spec's `voice_style` field:
- Set **Voice** to the recommended voice (e.g., `elevenlabs:Rachel` or `openai:alloy`)
- Set **Language** to `en-US` (or as specified in the spec)
- Set **Greeting** using the `greeting_message` field from the spec

### Step 6 — Set Agent Variables (Key Variables)

In the **Variables** section of the agent editor, add each entry from `key_variables`:

| Variable name | Value source |
|---------------|--------------|
| `timezone` | `business_hours.timezone` from account memo |
| `business_hours_start` | `business_hours.start` |
| `business_hours_end` | `business_hours.end` |
| `business_days` | `business_hours.days` |
| `office_address` | `office_address` |
| `emergency_transfer_number` | `emergency_routing_rules[0].phone` |
| `fallback_transfer_number` | `call_transfer_protocol.fallback_number` |

### Step 7 — Configure Call Transfer

In the **Transfer** settings:
1. Add the primary transfer number from `call_transfer_protocol.primary_number`.
2. Set transfer timeout per `call_transfer_protocol.timeout_seconds` (typically 60s).
3. Add the fallback number from `call_transfer_protocol.fallback_number`.
4. Set the message to play if transfer fails, from `fallback_protocol.message`.

### Step 8 — Test the Agent

1. Click **Test Call** in the Retell dashboard.
2. Verify the agent follows the business hours flow and after-hours flow from the prompt.
3. Test the transfer by letting it reach the transfer step.

### Step 9 — Activate

Once testing passes, click **Activate** to make the agent live.

---

## Where Outputs Are Stored

| Output | Path (inside container) | Path (on host) |
|--------|------------------------|----------------|
| Account memo v1 | `/app/clara_pipeline/outputs/accounts/{id}/v1/account_memo.json` | `./clara_pipeline/outputs/accounts/...` |
| Retell spec v1 | `/app/clara_pipeline/outputs/accounts/{id}/v1/retell_agent_spec.json` | same |
| Account memo v2 | `/app/clara_pipeline/outputs/accounts/{id}/v2/account_memo.json` | same |
| Retell spec v2 | `/app/clara_pipeline/outputs/accounts/{id}/v2/retell_agent_spec.json` | same |
| Changelog MD | `/app/clara_pipeline/outputs/accounts/{id}/v2/changes.md` | same |
| Changelog JSON | `/app/clara_pipeline/outputs/accounts/{id}/v2/changes.json` | same |
| Task tracker | `/app/clara_pipeline/outputs/tasks.json` | same |
| Pipeline log | `/app/clara_pipeline/outputs/pipeline_log.json` | same |
| Run summary | `/app/clara_pipeline/outputs/run_summary.json` | same |

Since `./clara_pipeline` is **bind-mounted** into the container, all outputs appear on your host filesystem immediately.

---

## Environment Variables

| Variable | Default | Where Set | Description |
|----------|---------|-----------|-------------|
| `OLLAMA_URL` | `http://host.docker.internal:11434` | docker-compose.yml | Ollama server URL |
| `OLLAMA_MODEL` | `mistral` | docker-compose.yml | Default model |

Per-request overrides: pass `model` and `ollama_url` in the webhook JSON body.

---

## Viewing Execution Logs in n8n UI

1. Open http://localhost:5678
2. Click **Executions** (left sidebar)
3. Select any run to see node-by-node output
4. Each node shows input/output JSON and stdout from script runs

---

## Error Handling

All three workflows include a connected **On Workflow Error** trigger node. If any Code node or Execute Command node throws an unhandled error, n8n routes the execution to the **Error Handler** → **Error Response** chain, which returns a structured JSON:

```json
{
  "status": "error",
  "pipeline": "A",
  "message": "Script 1 failed (exit 1): ...",
  "timestamp": "2026-01-15T10:30:00Z"
}
```

This means errors never result in a silent 500 with an empty body.

---

## Known Limitations

- **Long execution times**: Ollama LLM calls take 30–120s each. For batch runs, use `curl` or Postman with longer timeout settings rather than a browser.
- **Sequential processing**: The batch workflow runs pairs sequentially. Parallel processing would require a `SplitInBatches` node with multiple Ollama instances.
- **No persistent n8n auth**: Basic auth is disabled for local dev. Enable `N8N_BASIC_AUTH_ACTIVE=true` for shared machines.
- **Windows**: `host.docker.internal` works on Docker Desktop for Windows. If using WSL2 + Docker Engine, replace with the WSL2 host IP.
- **Transcript paths**: Paths in webhook payloads must be container paths (`/app/clara_pipeline/data/...`), not host paths.
- **Task tracker**: Local `tasks.json` is used as the task tracking backend. It is not a hosted project management tool. Upgrade path: n8n native Asana/Trello nodes.

---

## What Would Improve with Production Access

- **Asana/Linear task creation** — n8n has native Asana nodes; replace the "Create Task Tracker Item" Code node with n8n's Asana node to auto-create onboarding tasks with real project tracking
- **Retell API node** — direct agent creation via `POST /v2/create-agent`; add after Pipeline B go-live gate passes
- **n8n Cloud** — managed hosting, no Docker required, auto-HTTPS webhooks
- **Async execution + callbacks** — trigger batch via webhook, receive results via callback URL
- **Supabase storage** — replace file-based `pipeline_log.json` with a real DB
- **Slack/email notifications** — n8n native nodes for alerting on errors or go-live readiness
- **Diff viewer UI** — simple HTML page reading `pipeline_log.json` and `changes.json` to display v1→v2 diffs visually

---

## Troubleshooting

### n8n container won't start
```bash
docker compose logs n8n
# Common: port 5678 already in use
# Fix: change "5678:5678" to "5679:5678" in docker-compose.yml
```

### Ollama not reachable from container
```bash
# Test from inside container:
docker exec clara-n8n curl http://host.docker.internal:11434/api/tags

# If that fails on Linux (non-Docker Desktop):
# Find host IP: ip route | grep default | awk '{print $3}'
# Update OLLAMA_URL in docker-compose.yml to use that IP
```

### Script 1/2/3 not found
```bash
# Verify the mount:
docker exec clara-n8n ls /app/clara_pipeline/scripts/
# Should list: script1_extract_memo.py, script2_generate_spec.py, etc.

# If missing, check that ./clara_pipeline/ directory exists
# and run setup.sh again
```

### Workflow import fails
```bash
# Import manually via n8n UI:
# Workflows → New → Import from file → select JSON from workflows/
```

### Re-import after workflow edits
```bash
# Delete existing workflow in n8n UI first, then re-run:
./scripts/import_workflows.sh
```
