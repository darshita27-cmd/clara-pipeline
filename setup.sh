#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Clara Pipeline Phase 3 — Setup Script
# Validates prerequisites, then starts n8n and imports workflows.
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║     Clara Pipeline Phase 3 — Setup                      ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# ── 1. Check clara_pipeline/ directory ───────────────────────────────────────
echo "── Checking required directories ────────────────────────────"

if [ ! -d "./clara_pipeline" ]; then
  echo ""
  echo "✗ ERROR: ./clara_pipeline directory not found."
  echo ""
  echo "  This directory must contain the Phase 2 scripts (clara_pipeline_fixed/)."
  echo ""
  echo "  Fix: copy the Phase 2 output here:"
  echo "    cp -r /path/to/clara_pipeline_fixed ./clara_pipeline"
  echo ""
  echo "  Or if you have the full project:"
  echo "    cp -r ../clara_pipeline_fixed ./clara_pipeline"
  echo ""
  exit 1
fi

REQUIRED_FILES=(
  "clara_pipeline/scripts/script1_extract_memo.py"
  "clara_pipeline/scripts/script2_generate_spec.py"
  "clara_pipeline/scripts/script3_onboarding_update.py"
  "clara_pipeline/run_all.py"
  "clara_pipeline/prompts/extraction_prompt.md"
  "clara_pipeline/prompts/agent_prompt_template.md"
)

MISSING=0
for f in "${REQUIRED_FILES[@]}"; do
  if [ ! -f "./$f" ]; then
    echo "  ✗ Missing: $f"
    MISSING=$((MISSING+1))
  fi
done

if [ $MISSING -gt 0 ]; then
  echo ""
  echo "  ✗ $MISSING required file(s) missing from ./clara_pipeline"
  echo "  Ensure you copied the full clara_pipeline_fixed/ contents."
  exit 1
fi

echo "  ✓ clara_pipeline/ directory present with required scripts"

# ── 2. Create outputs directory ───────────────────────────────────────────────
mkdir -p ./clara_pipeline/outputs
echo "  ✓ clara_pipeline/outputs/ ready"
echo ""

# ── 3. Check Docker ───────────────────────────────────────────────────────────
echo "── Checking Docker ──────────────────────────────────────────"
if ! docker --version > /dev/null 2>&1; then
  echo "  ✗ Docker not installed. Install from https://docs.docker.com/get-docker/"
  exit 1
fi
echo "  ✓ Docker: $(docker --version)"

if ! docker compose version > /dev/null 2>&1; then
  echo "  ✗ Docker Compose v2 not found."
  exit 1
fi
echo "  ✓ Docker Compose: $(docker compose version)"
echo ""

# ── 4. Check Ollama ───────────────────────────────────────────────────────────
echo "── Checking Ollama ──────────────────────────────────────────"
OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
if curl -sf "${OLLAMA_URL}/api/tags" > /dev/null 2>&1; then
  echo "  ✓ Ollama reachable at ${OLLAMA_URL}"
  if curl -sf "${OLLAMA_URL}/api/tags" | grep -q 'mistral'; then
    echo "  ✓ mistral model available"
  else
    echo "  ⚠ mistral model not found. Pulling now..."
    ollama pull mistral || echo "  ⚠ Pull failed — run: ollama pull mistral"
  fi
else
  echo "  ⚠ Ollama not reachable at ${OLLAMA_URL}"
  echo "    Start it with: ollama serve"
  echo "    Then pull the model: ollama pull mistral"
  echo "    (Continuing setup — Ollama must be running before pipeline calls)"
fi
echo ""

# ── 5. Start n8n ─────────────────────────────────────────────────────────────
echo "── Starting n8n ─────────────────────────────────────────────"
docker compose up -d
echo ""

# ── 6. Import workflows ───────────────────────────────────────────────────────
echo "── Importing workflows ───────────────────────────────────────"
chmod +x scripts/import_workflows.sh
./scripts/import_workflows.sh
echo ""

# ── 7. Done ───────────────────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  ✅ Setup complete!                                       ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "  NEXT STEP: Open http://localhost:5678"
echo "  Activate all three workflows (toggle the Active switch ON)"
echo ""
echo "  Then test with:"
echo "    curl -X POST http://localhost:5678/webhook/clara/demo \\"
echo "      -H 'Content-Type: application/json' \\"
echo "      -d '{\"transcript_path\": \"/app/clara_pipeline/data/demo/client_a.txt\"}'"
echo ""
echo "  See README_PHASE3.md for full usage."
echo ""
