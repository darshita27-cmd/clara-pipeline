# Clara Pipeline — Phase 2
## Python Scripts Engine

**Version:** 2.1  
**LLM Backend:** Ollama (local, zero-cost)  
**Status:** Phase 2 complete — Script 1, 2, 3, run_all.py + shared utils.py

---

## Architecture & Data Flow

```
Transcript (.txt / .md / .docx)
        │
        ▼
┌──────────────────────────────────┐
│  Script 1: script1_extract_memo  │  ← Ollama  (prompts/extraction_prompt.md)
│  Transcript → Account Memo JSON  │
└───────────────┬──────────────────┘
                │ account_memo.json (v1)
                ▼
┌──────────────────────────────────┐
│  Script 2: script2_generate_spec │  ← Ollama  (prompts/agent_spec_generator_prompt.md
│  Account Memo → Retell Agent     │              prompts/agent_prompt_template.md)
└───────────────┬──────────────────┘
                │ retell_agent_spec.json (v1)  or  .draft.json if unresolved fields remain
                ▼
        outputs/accounts/{id}/v1/
                │
                │  [onboarding transcript arrives]
                ▼
┌──────────────────────────────────┐
│  Script 3: script3_onboarding    │  → calls Script 1 (patch mode)
│  v1 + Onboarding → v2 + Diff    │  → calls Script 2 (regenerate)
└───────────────┬──────────────────┘  → diff engine → changelog
                │
                ▼
        outputs/accounts/{id}/v2/
             account_memo.json
             retell_agent_spec.json   (or .draft.json)
             changes.md
             changes.json
```

---

## Directory Structure

```
clara_pipeline/
├── README.md
├── run_all.py                          ← Full 10-transcript batch runner
├── DESIGN.md                           ← Architecture decisions (from phase1_v1.2)
├── account_memo.schema.json            ← JSON Schema (from phase1_v1.2)
├── retell_agent_spec.schema.json       ← JSON Schema (from phase1_v1.2)
├── example_memo_v1.json                ← Sample extracted memo (from phase1_v1.2)
├── data/
│   ├── demo/                           ← Drop 5 demo transcripts here
│   └── onboarding/                     ← Drop 5 onboarding transcripts here
│                                          (filenames MUST match demo filenames)
├── scripts/
│   ├── utils.py                        ← Shared helpers (Ollama, JSON parsing, etc.)
│   ├── script1_extract_memo.py         ← Transcript → Account Memo JSON
│   ├── script2_generate_spec.py        ← Account Memo → Retell Agent Spec
│   └── script3_onboarding_update.py    ← v1 + onboarding → v2 + changelog
├── prompts/                            ← Phase 1 v1.2 prompt templates (required)
│   ├── extraction_prompt.md            ← 9-rule extraction LLM instruction
│   ├── agent_spec_generator_prompt.md  ← Spec generation LLM instruction
│   └── agent_prompt_template.md        ← Handlebars agent prompt template
└── outputs/
    ├── state.json                      ← Atomic account ID counter
    ├── run_summary.json                ← Last batch run summary + input pairs
    └── accounts/
        └── CLARA-2026-001/
            ├── v1/
            │   ├── account_memo.json
            │   └── retell_agent_spec.json   (or .draft.json)
            └── v2/
                ├── account_memo.json
                ├── retell_agent_spec.json
                ├── changes.md
                └── changes.json
```

---

## Prerequisites

### 1. Install Ollama (free, local)

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows
# Download from https://ollama.com/download
# Note: file locking uses Python's fcntl module which is Unix-only.
# On Windows, use WSL2 to run this pipeline.
```

### 2. Pull a model

```bash
# Recommended — fast, reliable JSON output
ollama pull mistral

# Higher extraction quality, slower
ollama pull llama3

# Fastest, acceptable for testing
ollama pull phi3
```

### 3. Start Ollama

```bash
ollama serve
```

### 4. Python dependencies

```bash
pip install python-docx    # Only needed for .docx transcripts
# No other external dependencies
```

---

## Project Setup (First Time)

```bash
# 1. Clone or extract the project
cd clara_pipeline

# 2. Ensure the prompts directory contains the phase1_v1.2 files:
ls prompts/
# Should show:
#   extraction_prompt.md
#   agent_spec_generator_prompt.md
#   agent_prompt_template.md
# These files are REQUIRED — the pipeline will hard-fail if they are missing.

# 3. Create the outputs directory structure
mkdir -p outputs/accounts
```

---

## Running the Pipeline

### Single transcript — demo call

```bash
python scripts/script1_extract_memo.py \
  --transcript data/demo/client_a.txt \
  --stage demo_call
```

Output: `outputs/accounts/CLARA-2026-001/v1/account_memo.json`

### Generate Retell spec from memo

```bash
python scripts/script2_generate_spec.py \
  --memo outputs/accounts/CLARA-2026-001/v1/account_memo.json
```

Output: `outputs/accounts/CLARA-2026-001/v1/retell_agent_spec.json`

> If the spec has unresolved fields it is saved as `retell_agent_spec.draft.json`
> and is **not** go-live ready.

### Onboarding update — v1 → v2

```bash
python scripts/script3_onboarding_update.py \
  --account-id CLARA-2026-001 \
  --transcript data/onboarding/client_a.txt
```

Outputs:
- `outputs/accounts/CLARA-2026-001/v2/account_memo.json`
- `outputs/accounts/CLARA-2026-001/v2/retell_agent_spec.json`
- `outputs/accounts/CLARA-2026-001/v2/changes.md`
- `outputs/accounts/CLARA-2026-001/v2/changes.json`

### Run all 10 transcripts (full batch)

```bash
python run_all.py \
  --demo-dir data/demo \
  --onboarding-dir data/onboarding
```

The runner auto-matches files by filename stem — `client_a.txt` in demo pairs with
`client_a.txt` in onboarding.

**Or use an explicit pairs JSON:**

```json
// data/pairs.json
[
  {"demo": "data/demo/client_a.txt",  "onboarding": "data/onboarding/client_a.txt"},
  {"demo": "data/demo/client_b.docx", "onboarding": "data/onboarding/client_b.docx"}
]
```

```bash
python run_all.py --pairs data/pairs.json
```

**Dry run (verify pairing without Ollama):**

```bash
python run_all.py --demo-dir data/demo --onboarding-dir data/onboarding --dry-run
```

---

## Model Selection

```bash
# Per-command override
python scripts/script1_extract_memo.py \
  --transcript data/demo/client_a.txt --stage demo_call \
  --model llama3

# Or set for all scripts at once
export OLLAMA_MODEL=llama3
python run_all.py --demo-dir data/demo --onboarding-dir data/onboarding
```

---

## Idempotency

Running the pipeline twice is safe. If `v2/` already exists for an account,
Script 3 skips it. To force a re-run:

```bash
python scripts/script3_onboarding_update.py \
  --account-id CLARA-2026-001 --transcript data/onboarding/client_a.txt \
  --force
# or delete the v2/ directory manually
```

---

## Output File Reference

| File | Location |
|------|----------|
| Account Memo v1 | `outputs/accounts/{id}/v1/account_memo.json` |
| Retell Spec v1 | `outputs/accounts/{id}/v1/retell_agent_spec.json` |
| Account Memo v2 | `outputs/accounts/{id}/v2/account_memo.json` |
| Retell Spec v2 | `outputs/accounts/{id}/v2/retell_agent_spec.json` |
| Human changelog | `outputs/accounts/{id}/v2/changes.md` |
| Machine changelog | `outputs/accounts/{id}/v2/changes.json` |
| Run summary | `outputs/run_summary.json` |
| Account ID state | `outputs/state.json` |

**Draft specs:** Any spec with `questions_carried_forward` non-empty is saved as
`retell_agent_spec.draft.json` and must not be deployed to Retell.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `mistral` | Model to use |

---

## How Each Script Works

### Script 1 — Transcript → Account Memo JSON

1. Validates file size before loading (guards against non-transcript files)
2. Loads transcript (.txt / .md / .docx)
3. Loads prior memo if onboarding stage
4. Allocates account ID atomically (file-locked `state.json`)
5. Checks account_id mismatch (Rule 8) — **halts on mismatch**
6. Loads `prompts/extraction_prompt.md` — **hard failure if missing**
7. Truncates transcript at `MAX_PROMPT_CHARS` with a visible `[WARN]` log
8. Calls Ollama (temperature 0.1) for structured extraction
9. Parses JSON — handles markdown fences and partial wrapping
10. Logs a warning if LLM hallucinated a different account_id, then enforces ours
11. Computes confidence deterministically from 8 boolean fields
12. Validates required fields, IANA timezone, version pattern
13. Validates that null timezone is flagged in `questions_or_unknowns`
14. Saves to `outputs/accounts/{id}/{version}/account_memo.json`

### Script 2 — Account Memo → Retell Agent Spec

1. Loads account memo JSON
2. Loads `prompts/agent_spec_generator_prompt.md` + `agent_prompt_template.md` — **hard failure if missing**
3. Calls Ollama (temperature 0.2)
4. **Immediately** patches spec with memo-derived values after parsing (prevents hallucinated account_id propagating anywhere)
5. Validates: no unrendered `{{variables}}` (case-insensitive), required fields, version format
6. Go-live gate: `questions_carried_forward` non-empty → saves as `.draft.json`
7. Batch mode skips if either `.json` or `.draft.json` already exists

### Script 3 — Onboarding Update (v1 → v2 + Changelog)

1. Idempotency check — skips if v2 already exists (override with `--force`)
2. Verifies v1 memo exists — halts with a clear error if not
3. Imports Script 1 and Script 2 via `sys.modules` cache (no repeated file execution)
4. Calls Script 1 in `onboarding_call` mode — passes `outputs_dir` as argument, not module global
5. Calls Script 2 on the new v2 memo — same argument-passing pattern
6. Computes flat diff: null→value classified as "newly confirmed", not "changed"
7. Markdown changelog sanitises LLM-derived values (escapes `` ` `` and `#`)
8. Writes `changes.md` and `changes.json`

### run_all.py — Full Batch Runner

1. Loads pairs from `--pairs` JSON or auto-matches by filename stem
2. Health-checks Ollama before starting
3. Pre-allocates **all** account IDs upfront in a single lock acquisition
4. Runs each pair sequentially (Phase A: demo→v1, Phase B: onboarding→v2)
5. All Ollama/path config passed as arguments — no module-global mutation
6. Saves `run_summary.json` including the input pairs list (for reproducibility)

---

## Known Limitations

- `fcntl` file locking is Unix/macOS only — use WSL2 on Windows
- `holidays_observed` is free text — agent cannot programmatically skip holidays (Phase 3+)
- Multi-location routing is a stub — single routing tree per account
- Retell voice ID `eleven_labs_rachel` should be verified against current Retell docs before go-live
- `max_call_duration_ms` is hardcoded at 600 000 ms (10 min) — may need tuning per client
- Timezone null causes after-hours fallback (TIMEZONE NULL GUARD in agent template) — expected, not silent
- Go-live gate is enforced by pipeline script only, not JSON Schema alone
- `.docx` transcripts require `python-docx` installed
- **Webhook security** — `N8N_BASIC_AUTH_ACTIVE=false` is correct for closed local dev. Enable `N8N_BASIC_AUTH_ACTIVE=true` for any shared or networked machine. **Do not expose port 5678 externally** (e.g. via `ngrok` or port-forwarding) without enabling authentication — the webhook endpoints accept any POST request and will execute pipeline commands without auth. If external access is required, place the n8n instance behind a reverse proxy with authentication.

---

## What Would Improve with Production Access

- **Retell API integration** — direct agent creation via `POST /v2/create-agent` instead of manual paste
- **Windows-compatible locking** — replace `fcntl` with the `filelock` library for cross-platform support
- **Supabase storage** — replace file-based `state.json` with atomic DB counters
- **Asana / Linear task creation** — auto-create onboarding tasks per account
- **Structured holiday list** — replace free-text `holidays_observed` with a typed date array
- **Streaming Ollama output** — real-time extraction progress display
- **Ollama JSON mode** — `format: "json"` for more reliable structured output
- **n8n workflow** — webhook-triggered orchestration for production deployment
- **Diff viewer UI** — web page showing v1→v2 changes side-by-side

---

## Retell Manual Import Instructions

Since Retell's free tier may not allow programmatic agent creation:

1. Open Retell dashboard → **Create Agent**
2. **Agent Name** — copy from `retell_agent_spec.json` → `agent_name`
3. **System Prompt** — copy from `retell_agent_spec.json` → `system_prompt`
4. **Voice** — `eleven_labs_rachel` (or match `retell_config.voice_id`)
5. **Language** — `en-US`
6. **Transfer Number** — from `call_transfer_protocol.emergency_transfer_chain[0].target`
7. Review `questions_carried_forward` — any `[CONFIRM WITH CLIENT]` placeholder **must** be resolved before go-live

> If the spec was saved as `retell_agent_spec.draft.json`, the agent is NOT ready.
> Resolve all items in `questions_carried_forward` first (run the onboarding call stage).
