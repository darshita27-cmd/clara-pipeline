#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Clara Pipeline Phase 3 — Quick Verification Script
# Runs a dry-run batch to confirm the full stack is wired correctly.
#
# Usage:
#   ./scripts/verify_setup.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

N8N_URL="${N8N_URL:-http://localhost:5678}"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║     Clara Pipeline Phase 3 — Setup Verification          ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

PASS=0
FAIL=0

check() {
  local desc="$1"
  local cmd="$2"
  if eval "$cmd" > /dev/null 2>&1; then
    echo "  ✓ $desc"
    PASS=$((PASS+1))
  else
    echo "  ✗ $desc"
    FAIL=$((FAIL+1))
  fi
}

echo "── Docker ───────────────────────────────────────────────────"
check "Docker is installed"         "docker --version"
check "Docker Compose v2 available" "docker compose version"
check "n8n container is running"    "docker ps | grep clara-n8n"
echo ""

echo "── Ollama ───────────────────────────────────────────────────"
check "Ollama is reachable at ${OLLAMA_URL}" \
  "curl -sf ${OLLAMA_URL}/api/tags"
check "mistral model available" \
  "curl -sf ${OLLAMA_URL}/api/tags | grep -q mistral"
echo ""

echo "── n8n ──────────────────────────────────────────────────────"
check "n8n health endpoint responds" \
  "curl -sf ${N8N_URL}/healthz"
check "Pipeline A webhook exists" \
  "curl -sf -o /dev/null -w '%{http_code}' -X POST ${N8N_URL}/webhook/clara/demo -H 'Content-Type: application/json' -d '{\"bad\":true}' | grep -qv 404"
check "Pipeline B webhook exists" \
  "curl -sf -o /dev/null -w '%{http_code}' -X POST ${N8N_URL}/webhook/clara/onboarding -H 'Content-Type: application/json' -d '{\"bad\":true}' | grep -qv 404"
check "Pipeline BATCH webhook exists" \
  "curl -sf -o /dev/null -w '%{http_code}' -X POST ${N8N_URL}/webhook/clara/batch -H 'Content-Type: application/json' -d '{\"bad\":true}' | grep -qv 404"
echo ""

echo "── Clara Pipeline Files ─────────────────────────────────────"
check "clara_pipeline/ directory exists"     "test -d ./clara_pipeline"
check "script1_extract_memo.py present"      "test -f ./clara_pipeline/scripts/script1_extract_memo.py"
check "script2_generate_spec.py present"     "test -f ./clara_pipeline/scripts/script2_generate_spec.py"
check "script3_onboarding_update.py present" "test -f ./clara_pipeline/scripts/script3_onboarding_update.py"
check "run_all.py present"                   "test -f ./clara_pipeline/run_all.py"
check "prompts/ directory present"           "test -d ./clara_pipeline/prompts"
check "extraction_prompt.md present"         "test -f ./clara_pipeline/prompts/extraction_prompt.md"
check "agent_prompt_template.md present"     "test -f ./clara_pipeline/prompts/agent_prompt_template.md"
check "outputs/ directory exists"            "test -d ./clara_pipeline/outputs || mkdir -p ./clara_pipeline/outputs && test -d ./clara_pipeline/outputs"
echo ""

echo "── Dry Run (Batch) ──────────────────────────────────────────"
if [ -d "./clara_pipeline/data/demo" ] && [ -n "$(ls ./clara_pipeline/data/demo 2>/dev/null)" ]; then
  echo "  Running dry-run batch test..."
  RESPONSE=$(curl -sf -X POST "${N8N_URL}/webhook/clara/batch" \
    -H "Content-Type: application/json" \
    -d '{
      "demo_dir": "/app/clara_pipeline/data/demo",
      "onboarding_dir": "/app/clara_pipeline/data/onboarding",
      "dry_run": true
    }' 2>&1 || echo "CURL_FAILED")

  if echo "$RESPONSE" | grep -q '"status":"ok"'; then
    echo "  ✓ Dry-run batch responded OK"
    PASS=$((PASS+1))
    ACCOUNTS=$(echo "$RESPONSE" | grep -o '"accounts_processed":[0-9]*' | grep -o '[0-9]*' || echo "?")
    echo "    Pairs discovered: ${ACCOUNTS}"
  else
    echo "  ✗ Dry-run batch failed: ${RESPONSE:0:200}"
    FAIL=$((FAIL+1))
  fi
else
  echo "  ⊘ Skipped (no data/demo transcripts found yet)"
  echo "    Drop transcripts into ./clara_pipeline/data/demo/ to test"
fi
echo ""

echo "══════════════════════════════════════════════════════════════"
echo "  Results: ${PASS} passed / $((PASS+FAIL)) checks"
if [ $FAIL -eq 0 ]; then
  echo "  ✅ All checks passed — pipeline is ready"
else
  echo "  ❌ ${FAIL} check(s) failed — see README_PHASE3.md troubleshooting"
fi
echo ""
echo "  Webhook endpoints:"
echo "    Pipeline A:     POST ${N8N_URL}/webhook/clara/demo"
echo "    Pipeline B:     POST ${N8N_URL}/webhook/clara/onboarding"
echo "    Pipeline BATCH: POST ${N8N_URL}/webhook/clara/batch"
echo "    n8n UI:         ${N8N_URL}"
echo "══════════════════════════════════════════════════════════════"
echo ""
