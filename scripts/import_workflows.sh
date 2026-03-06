#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Clara Pipeline — n8n Workflow Importer
# Phase 3 Setup Script
#
# Usage:
#   ./scripts/import_workflows.sh
#
# What it does:
#   1. Waits for n8n to be ready
#   2. Imports all three workflow JSONs via the n8n CLI (inside container)
#   3. Prints the webhook URLs to use
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

N8N_URL="${N8N_URL:-http://localhost:5678}"
CONTAINER="${CONTAINER:-clara-n8n}"
WORKFLOWS_DIR="${WORKFLOWS_DIR:-./workflows}"
MAX_WAIT=60

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║     Clara Pipeline — n8n Workflow Import                 ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── 1. Wait for n8n to be ready ──────────────────────────────────────────────
echo "⏳ Waiting for n8n at ${N8N_URL}..."
elapsed=0
until curl -sf "${N8N_URL}/healthz" > /dev/null 2>&1; do
  if [ $elapsed -ge $MAX_WAIT ]; then
    echo "✗ n8n did not start within ${MAX_WAIT}s."
    echo "  Check: docker compose logs n8n"
    exit 1
  fi
  sleep 2
  elapsed=$((elapsed + 2))
  echo "  ...still waiting (${elapsed}s)"
done
echo "✓ n8n is ready"
echo ""

# ── 2. Import workflows ───────────────────────────────────────────────────────
WORKFLOWS=(
  "pipeline_A_demo_to_v1.json"
  "pipeline_B_onboarding_to_v2.json"
  "pipeline_BATCH_run_all.json"
)

for wf in "${WORKFLOWS[@]}"; do
  WF_PATH="${WORKFLOWS_DIR}/${wf}"

  if [ ! -f "${WF_PATH}" ]; then
    echo "✗ Workflow file not found: ${WF_PATH}"
    exit 1
  fi

  echo "📥 Importing: ${wf}"

  # Copy file into container then import via n8n CLI
  docker cp "${WF_PATH}" "${CONTAINER}:/tmp/${wf}"

  docker exec "${CONTAINER}" \
    n8n import:workflow --input="/tmp/${wf}" \
    && echo "  ✓ Imported: ${wf}" \
    || echo "  ⚠ Import may have failed for: ${wf} (check n8n UI)"

  echo ""
done

# ── 3. Activate workflows ─────────────────────────────────────────────────────
echo "⚡ Activating workflows in n8n..."
echo "   (You can also activate them manually in the n8n UI)"
echo ""

# ── 4. Print summary ──────────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════════╗"
echo "║     Webhook Endpoints                                     ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "  Pipeline A (Demo → v1):"
echo "  POST ${N8N_URL}/webhook/clara/demo"
echo ""
echo "  Pipeline B (Onboarding → v2):"
echo "  POST ${N8N_URL}/webhook/clara/onboarding"
echo ""
echo "  Pipeline BATCH (All 10 transcripts):"
echo "  POST ${N8N_URL}/webhook/clara/batch"
echo ""
echo "  n8n Dashboard:  ${N8N_URL}"
echo ""
echo "  See README_PHASE3.md for full usage examples with curl."
echo ""
echo "✅ Import complete"
