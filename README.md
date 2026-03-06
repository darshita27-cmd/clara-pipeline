# Clara Pipeline

An end-to-end automation pipeline that converts demo and onboarding call transcripts into structured account memos and Retell voice agent configurations — zero cost, fully local.

---

## What It Does

**Pipeline A** — Takes a demo call transcript and produces:
- A structured Account Memo JSON (v1)
- A Retell Agent Spec JSON (v1 draft)
- A task tracker entry in `outputs/tasks.json`

**Pipeline B** — Takes an onboarding call transcript and produces:
- An updated Account Memo JSON (v2)
- A regenerated Retell Agent Spec JSON (v2)
- A human-readable changelog (`changes.md`)
- A machine-readable diff (`changes.json`)

---

## Architecture

```
Transcript (.txt / .docx)
        │
        ▼
┌─────────────────────────┐
│  Script 1               │  ← Ollama (mistral)
│  Transcript → Memo JSON │     prompts/extraction_prompt.md
└────────────┬────────────┘
             │ account_memo.json (v1)
             ▼
┌─────────────────────────┐
│  Script 2               │  ← Ollama (mistral)
│  Memo → Retell Spec     │     prompts/agent_spec_generator_prompt.md
└────────────┬────────────┘
             │ retell_agent_spec.json (v1)
             ▼
     outputs/accounts/{id}/v1/

             │  [onboarding transcript arrives]
             ▼
┌─────────────────────────┐
│  Script 3               │  → calls Script 1 (patch mode)
│  v1 + Onboarding → v2   │  → calls Script 2 (regenerate)
└────────────┬────────────┘  → diff engine → changelog
             ▼
     outputs/accounts/{id}/v2/
          account_memo.json
          retell_agent_spec.json
          changes.md
          changes.json

n8n (Docker) orchestrates all scripts via HTTP webhooks
```

---

## Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) running locally with `mistral` pulled
- Docker + Docker Compose (for n8n orchestration)
- `pip install python-docx filelock` (for .docx transcripts and safe file locking)

---

## Setup

```bash
# 1. Clone the repo
git clone https://github.com/darshita27-cmd/clara-pipeline
cd clara-pipeline

# 2. Create and activate virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac/Linux

# 3. Install dependencies
pip install python-docx filelock

# 4. Pull the Ollama model
ollama pull mistral

# 5. Start Ollama (keep running in background)
ollama serve
```

---

## Running the Pipeline

### Option A — Full batch via Python (recommended for testing)

```bash
cd clara_pipeline

# Create pairs file
# (already included at data/pairs.json)

# Run all 5 pairs
python run_all.py --pairs data/pairs.json
```

### Option B — Single transcript (demo call)

```bash
cd clara_pipeline
python scripts/script1_extract_memo.py --transcript data/demo/apex_fire_demo.txt --stage demo_call
python scripts/script2_generate_spec.py --memo outputs/accounts/CLARA-2026-001/v1/account_memo.json
```

### Option C — Single onboarding update (v1 → v2)

```bash
cd clara_pipeline
python scripts/script3_onboarding_update.py \
  --account-id CLARA-2026-001 \
  --transcript data/onboarding/apex_fire_onboarding.txt
```

### Option D — Via n8n webhooks (after Docker setup)

```bash
# Start n8n
docker compose up -d

# Import workflows
chmod +x scripts/import_workflows.sh
./scripts/import_workflows.sh

# Trigger Pipeline A
curl -X POST http://localhost:5678/webhook/clara/demo \
  -H "Content-Type: application/json" \
  -d '{"transcript_path": "/app/clara_pipeline/data/demo/apex_fire_demo.txt", "stage": "demo_call"}'

# Trigger Pipeline B
curl -X POST http://localhost:5678/webhook/clara/onboarding \
  -H "Content-Type: application/json" \
  -d '{"account_id": "CLARA-2026-001", "transcript_path": "/app/clara_pipeline/data/onboarding/apex_fire_onboarding.txt"}'

# Trigger full batch
curl -X POST http://localhost:5678/webhook/clara/batch \
  -H "Content-Type: application/json" \
  -d '{"pairs_file": "/app/clara_pipeline/data/pairs.json"}'
```

---

## Plugging In Your Dataset

1. Drop demo transcripts into `clara_pipeline/data/demo/`
2. Drop onboarding transcripts into `clara_pipeline/data/onboarding/`
3. Filenames must match between folders (e.g. `client_a.txt` in both), OR create a `pairs.json`:

```json
[
  {"demo": "data/demo/client_a.txt", "onboarding": "data/onboarding/client_a.txt"},
  {"demo": "data/demo/client_b.txt", "onboarding": "data/onboarding/client_b.txt"}
]
```

4. Run: `python run_all.py --pairs data/pairs.json`

Supported transcript formats: `.txt`, `.md`, `.docx`

---

## Output Structure

```
clara_pipeline/outputs/
├── accounts/
│   ├── CLARA-2026-001/
│   │   ├── v1/
│   │   │   ├── account_memo.json
│   │   │   └── retell_agent_spec.json
│   │   └── v2/
│   │       ├── account_memo.json
│   │       ├── retell_agent_spec.json
│   │       ├── changes.md
│   │       └── changes.json
│   ├── CLARA-2026-002/
│   │   └── ...
├── tasks.json           ← task tracker (one entry per account)
├── pipeline_log.json    ← full run log
└── run_summary.json     ← last batch summary
```

---

## n8n Workflows

| Endpoint | Workflow File | Description |
|----------|--------------|-------------|
| `POST /webhook/clara/demo` | `pipeline_A_demo_to_v1.json` | Demo transcript → v1 memo + Retell spec |
| `POST /webhook/clara/onboarding` | `pipeline_B_onboarding_to_v2.json` | Onboarding → v2 + changelog |
| `POST /webhook/clara/batch` | `pipeline_BATCH_run_all.json` | Run all pairs end-to-end |

Import via n8n UI: Workflows → New → Import from file → select JSON from `workflows/`

Or run `./scripts/import_workflows.sh` after n8n is running.

---

## Dataset

This repo includes 5 demo + 5 onboarding transcripts across the following accounts:

| Account | Industry |
|---------|----------|
| Ben's Electric Solutions | Electrical contractor |
| Apex Fire Protection | Fire suppression + sprinkler |
| Summit HVAC Services | HVAC + climate control |
| Guardian Alarm Systems | Commercial alarm + access control |
| Coastal Sprinkler Co | Wet/dry sprinkler systems |

4 of the 5 pairs are synthetic transcripts generated to demonstrate the pipeline across different trade verticals. The Ben's Electric pair uses a real demo call transcript provided as part of the assignment dataset.

---

## Known Limitations

- `fcntl` file locking is Unix/macOS only — on Windows, install `filelock`: `pip install filelock`
- Extraction confidence is `low` on some accounts — mistral occasionally outputs field names that differ slightly from the schema. A prompt patch targeting mistral's output format would resolve this.
- Company names not always extracted at v1 stage (demo calls are exploratory and often don't state the company name explicitly early in the call)
- `retell_agent_spec.draft.json` means the spec has unresolved fields — these must be reviewed before Retell import
- Windows only: use PowerShell or WSL2; `setup.sh` and `import_workflows.sh` require bash (WSL2 or Git Bash)
- Ollama calls take 30–120s each — batch of 10 transcripts takes ~20–30 minutes

---

## What Would Improve with Production Access

- **Retell API integration** — direct agent creation via `POST /v2/create-agent` instead of manual paste
- **Asana/Linear task creation** — replace local `tasks.json` with real project management via n8n native nodes
- **Supabase storage** — replace file-based state with atomic DB counters and structured log queries
- **Parallel Ollama processing** — run multiple accounts simultaneously instead of sequentially
- **Fine-tuned extraction prompt for mistral** — higher confidence scores and consistent field naming
- **Streaming Ollama output** — real-time extraction progress in the terminal
- **Diff viewer UI** — simple web page showing v1 → v2 changes side by side
- **Whisper transcription step** — auto-transcribe audio files before extraction so the pipeline accepts raw recordings

---

## LLM Usage — Zero Cost

This pipeline uses [Ollama](https://ollama.com) running entirely locally. No API keys, no cloud LLM calls, no spend. The default model is `mistral` (4.4GB). Alternative: `llama3` (4.7GB).

```bash
ollama pull mistral   # recommended
ollama pull llama3    # alternative
```

Set model via environment variable: `$env:OLLAMA_MODEL="mistral"`
