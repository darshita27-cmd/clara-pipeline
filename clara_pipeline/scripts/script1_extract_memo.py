#!/usr/bin/env python3
"""
CLARA PIPELINE — Script 1: Transcript → Account Memo JSON
Phase 2 | Version: 1.1

Usage:
    python script1_extract_memo.py --transcript <path> --stage demo_call
    python script1_extract_memo.py --transcript <path> --stage onboarding_call \\
        --account-id CLARA-2026-001 --prior-memo <path>
    python script1_extract_memo.py --batch --input-dir <dir> --stage demo_call

Ollama must be running locally: ollama serve
Recommended model: mistral or llama3

Dependencies: pip install python-docx   (only needed for .docx transcripts)
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

# R-01 fix: fcntl is Unix-only and crashes on Windows with ImportError.
# Strategy: try the standard filelock library first (pip install filelock),
# then fall back to fcntl on Unix, then degrade to a no-op lock on Windows
# with a clear warning so the user knows concurrent runs are not safe.
try:
    from filelock import FileLock as _FileLock  # cross-platform (preferred)
    _LOCK_BACKEND = "filelock"
except ImportError:
    try:
        import fcntl as _fcntl                  # Unix built-in
        _LOCK_BACKEND = "fcntl"
    except ImportError:
        _fcntl = None                            # Windows, no filelock installed
        _LOCK_BACKEND = "noop"
        import warnings
        warnings.warn(
            "Neither 'filelock' nor 'fcntl' is available. "
            "File locking is disabled — do NOT run concurrent batch jobs on this machine. "
            "Install 'filelock' to enable safe locking: pip install filelock",
            RuntimeWarning,
            stacklevel=2,
        )
from pathlib import Path

# ── PATH BOOTSTRAP ─────────────────────────────────────────────────────────────
# Allow running from any working directory.
SCRIPTS_DIR  = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPTS_DIR.parent.resolve()
sys.path.insert(0, str(SCRIPTS_DIR))

from utils import (
    call_ollama,
    check_ollama_health,
    extract_json_from_response,
    load_transcript,
    truncate_transcript,
    IANA_ZONES,
    IANA_PATTERN,
    DEFAULT_OLLAMA_URL,
    DEFAULT_OLLAMA_MODEL,
)

# ── CONFIG ─────────────────────────────────────────────────────────────────────
OLLAMA_URL   = os.environ.get("OLLAMA_URL",   DEFAULT_OLLAMA_URL)
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)

STATE_FILE   = PROJECT_ROOT / "outputs" / "state.json"
OUTPUTS_DIR  = PROJECT_ROOT / "outputs" / "accounts"
PROMPTS_DIR  = PROJECT_ROOT / "prompts"
SCHEMA_FILE  = PROJECT_ROOT / "account_memo.schema.json"

# 8 fields scored for extraction_confidence (computed here, not by LLM)
CONFIDENCE_FIELDS = [
    "company_name",
    "business_hours_schedule",
    "business_hours_timezone",
    "emergency_triggers",
    "escalation_chain_with_phone",
    "transfer_timeout",
    "transfer_fail_action",
    "after_hours_action",
]


# ── STATE / ACCOUNT ID MANAGEMENT ──────────────────────────────────────────────

def _read_state() -> dict:
    """Read state.json, creating it if absent."""
    if not STATE_FILE.exists():
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        return {"last_account_number": 0, "last_updated": ""}
    with open(STATE_FILE) as f:
        return json.load(f)


def _write_state(state: dict) -> None:
    """Atomically write state.json via rename (crash-safe)."""
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def _get_lock_path() -> Path:
    lock_path = STATE_FILE.with_suffix(".lock")
    lock_path.touch(exist_ok=True)
    return lock_path


def _acquire_lock(lock_path: Path):
    """
    Return a context manager that holds an exclusive lock on lock_path.

    Priority:
      1. filelock library  — cross-platform, works on Windows & Unix
      2. fcntl             — Unix built-in
      3. no-op             — Windows without filelock (unsafe for concurrency)
    """
    import contextlib

    if _LOCK_BACKEND == "filelock":
        return _FileLock(str(lock_path))

    if _LOCK_BACKEND == "fcntl":
        @contextlib.contextmanager
        def _fcntl_lock():
            with open(lock_path, "w") as lf:
                try:
                    _fcntl.flock(lf, _fcntl.LOCK_EX)
                    yield
                finally:
                    _fcntl.flock(lf, _fcntl.LOCK_UN)
        return _fcntl_lock()

    # no-op fallback (warning already emitted at import time)
    @contextlib.contextmanager
    def _noop_lock():
        yield
    return _noop_lock()


def allocate_account_id() -> str:
    """Allocate the next account ID atomically using a cross-platform file lock."""
    lock_path = _get_lock_path()
    with _acquire_lock(lock_path):
        state = _read_state()
        state["last_account_number"] += 1
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        _write_state(state)
        year = datetime.now().year
        return f"CLARA-{year}-{state['last_account_number']:03d}"


def allocate_batch_ids(n: int) -> list[str]:
    """
    Allocate n account IDs in a single lock acquisition.
    Must be called upfront before any LLM calls to avoid ID collisions.
    """
    lock_path = _get_lock_path()
    with _acquire_lock(lock_path):
        state = _read_state()
        ids   = []
        year  = datetime.now().year
        for _ in range(n):
            state["last_account_number"] += 1
            ids.append(f"CLARA-{year}-{state['last_account_number']:03d}")
        state["last_updated"] = datetime.now(timezone.utc).isoformat()
        _write_state(state)
        return ids


# ── PROMPT LOADING ──────────────────────────────────────────────────────────────

def _require_prompt_file(filename: str) -> Path:
    """
    Return the path to a required prompt file.
    Raises FileNotFoundError with a clear message if missing — no silent fallback.
    The prompts/ directory is a hard dependency; running without it produces
    weaker, rule-incomplete extractions.
    """
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Required prompt file not found: {path}\n"
            f"Copy the phase1_v1.2 prompt files into: {PROMPTS_DIR}\n"
            f"Expected files: extraction_prompt.md, agent_spec_generator_prompt.md, "
            f"agent_prompt_template.md"
        )
    return path


def load_extraction_prompt() -> str:
    return _require_prompt_file("extraction_prompt.md").read_text(encoding="utf-8")


def build_prompt(
    transcript: str,
    stage: str,
    account_id: str,
    prior_memo: dict | None = None,
) -> tuple[str, str]:
    """
    Build (system_prompt, user_prompt) for the extraction LLM call.

    The full extraction_prompt.md from phase1_v1.2 is used — all 9 Rules
    are present.  The transcript is truncated with a visible warning if it
    exceeds MAX_PROMPT_CHARS (logged by truncate_transcript).
    """
    template = load_extraction_prompt()

    # Split on USER PROMPT section marker
    if "## USER PROMPT" in template:
        parts          = template.split("## USER PROMPT", 1)
        system_section = parts[0]
        # user_section not used below — we build user prompt ourselves
    else:
        system_section = template

    # Clean up system section: remove the file header comment lines
    system_lines = []
    for line in system_section.splitlines():
        stripped = line.strip()
        if stripped.startswith("# CLARA") or stripped.startswith("# Version") or \
                stripped.startswith("# Stage") or stripped.startswith("# Account") or \
                stripped.startswith("# Prior"):
            continue
        if stripped == "## SYSTEM PROMPT":
            continue
        system_lines.append(line)
    system_prompt = "\n".join(system_lines).strip()

    # Truncate transcript with a logged warning if needed
    transcript_safe = truncate_transcript(transcript)

    prior_json_str = json.dumps(prior_memo, indent=2) if prior_memo else "null"
    version        = "v2" if prior_memo else "v1"
    ts             = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    user_prompt = f"""Stage: {stage}
Account ID: {account_id}
Prior Memo (v1, if updating):
```json
{prior_json_str}
```

Transcript:
```
{transcript_safe}
```

Extract a complete Account Memo JSON.
Set version to "{version}", extracted_at to "{ts}", extraction_confidence to null (pipeline computes it).
Return ONLY valid JSON. No markdown fences. No explanation. Start with {{ and end with }}.
"""
    return system_prompt, user_prompt


# ── CONFIDENCE SCORING ──────────────────────────────────────────────────────────

def compute_confidence(memo: dict) -> tuple[str, dict]:
    """
    Compute extraction_confidence deterministically from memo content.
    The LLM is never asked to self-assess confidence.

    Returns (label, breakdown_dict).
    """
    def truthy(val) -> bool:
        if val is None:
            return False
        if isinstance(val, (list, dict)):
            return len(val) > 0
        if isinstance(val, str):
            return val.strip() != ""
        return bool(val)

    # Bug 4 fix: use `or []` so an explicit null doesn't cause TypeError in any()
    chain = memo.get("emergency_routing", {}).get("escalation_chain") or []
    sched = memo.get("business_hours", {}).get("schedule") or []

    breakdown = {
        "company_name": truthy(
            memo.get("company", {}).get("name")
        ),
        "business_hours_schedule": any(
            s.get("open") and s.get("close") for s in sched
        ),
        "business_hours_timezone": truthy(
            memo.get("business_hours", {}).get("timezone")
        ),
        "emergency_triggers": truthy(
            memo.get("emergency_definition", {}).get("triggers")
        ),
        "escalation_chain_with_phone": any(
            e.get("phone") for e in chain if isinstance(e, dict)
        ),
        "transfer_timeout": truthy(
            memo.get("emergency_routing", {}).get("transfer_timeout_seconds")
        ),
        "transfer_fail_action": truthy(
            memo.get("emergency_routing", {}).get("transfer_fail_action")
        ),
        "after_hours_action": truthy(
            memo.get("non_emergency_routing", {}).get("after_hours_action")
        ),
    }

    score = sum(1 for v in breakdown.values() if v)
    label = "high" if score >= 7 else ("medium" if score >= 4 else "low")
    return label, breakdown


# ── VALIDATION ──────────────────────────────────────────────────────────────────

def validate_memo(memo: dict) -> list[str]:
    """
    Structural validation without the jsonschema library.
    Returns a list of error strings (empty list = passed).
    """
    errors: list[str] = []

    # Required top-level fields (mirrors schema required[])
    required = [
        "account_id", "version", "source_stage",
        "extracted_at", "questions_or_unknowns", "changelog",
    ]
    for field in required:
        if field not in memo:
            errors.append(f"Missing required field: '{field}'")

    # account_id format
    if "account_id" in memo:
        if not re.match(r"^CLARA-[0-9]{4}-[0-9]{3}$", str(memo["account_id"])):
            errors.append(
                f"Invalid account_id: '{memo['account_id']}' "
                "(expected CLARA-YYYY-NNN)"
            )

    # version format
    if "version" in memo:
        if not re.match(r"^v[0-9]+$", str(memo["version"])):
            errors.append(f"Invalid version: '{memo['version']}' (expected v1, v2, …)")

    # changelog must be a non-empty list (schema: minItems=1)
    if "changelog" in memo:
        if not isinstance(memo["changelog"], list) or len(memo["changelog"]) == 0:
            errors.append("'changelog' must be a non-empty list")

    # Timezone IANA format
    tz = memo.get("business_hours", {}).get("timezone")
    if tz is not None:                          # null is valid (not yet known)
        if not IANA_PATTERN.match(str(tz)):
            errors.append(
                f"Invalid timezone: '{tz}'. Must be IANA format "
                "(e.g. America/New_York). Rule 9 in extraction_prompt."
            )

    # Bug 5 fix: if timezone is null, ensure it is flagged in questions_or_unknowns
    if tz is None:
        unknowns = memo.get("questions_or_unknowns", [])
        flagged  = any(
            q.get("field") == "business_hours.timezone" for q in unknowns
        )
        if not flagged:
            errors.append(
                "business_hours.timezone is null but is NOT flagged in "
                "questions_or_unknowns. Add an entry for 'business_hours.timezone'."
            )

    return errors


def check_account_id_mismatch(account_id: str, prior_memo: dict | None) -> dict | None:
    """Return an error dict if the pipeline account_id doesn't match the prior memo."""
    if prior_memo is None:
        return None
    prior_id = prior_memo.get("account_id", "")
    if prior_id and prior_id != account_id:
        return {
            "error":       "account_id_mismatch",
            "injected_id": account_id,
            "memo_id":     prior_id,
            "action":      "pipeline must halt and alert operator",
        }
    return None


# ── OUTPUT ──────────────────────────────────────────────────────────────────────

def save_memo(memo: dict, account_id: str, version: str, outputs_dir: Path) -> Path:
    """Save memo JSON to outputs/accounts/{id}/{version}/account_memo.json
    and a stable .pipeline_meta.json for n8n to read (avoids stdout scraping)."""
    out_dir  = outputs_dir / account_id / version
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "account_memo.json"
    with open(out_path, "w") as f:
        json.dump(memo, f, indent=2)

    # R-2 fix: write a tiny stable JSON summary that n8n Code nodes can read
    # directly — much more robust than regex-scraping print() output.
    meta = {
        "account_id":            account_id,
        "version":               version,
        "memo_path":             str(out_path),
        "extraction_confidence": memo.get("extraction_confidence"),
        "unknowns_count":        len(memo.get("questions_or_unknowns", [])),
        "company_name":          memo.get("company", {}).get("name", ""),
    }
    meta_path = out_dir / ".pipeline_meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    return out_path


# ── MAIN PIPELINE ───────────────────────────────────────────────────────────────

def run_extraction(
    transcript_path: str,
    stage: str,
    account_id: str | None = None,
    prior_memo_path: str | None = None,
    *,
    ollama_url:   str  = OLLAMA_URL,
    ollama_model: str  = OLLAMA_MODEL,
    outputs_dir:  Path = OUTPUTS_DIR,
) -> dict:
    """
    Full extraction pipeline for one transcript.

    Args:
        transcript_path:  Path to .txt / .md / .docx transcript file.
        stage:            'demo_call', 'onboarding_call', or 'onboarding_form'.
        account_id:       Pre-allocated ID. Auto-allocated if None (demo only).
        prior_memo_path:  Path to existing v1 account_memo.json (onboarding).
        ollama_url:       Ollama server URL (no module-global mutation).
        ollama_model:     Ollama model name (no module-global mutation).
        outputs_dir:      Root outputs/accounts/ path (injected for testability).

    Returns:
        The final validated memo dict.
    """
    print(f"\n{'='*60}")
    print(f"  SCRIPT 1: TRANSCRIPT → ACCOUNT MEMO")
    print(f"  Stage:      {stage}")
    print(f"  Transcript: {transcript_path}")
    print(f"{'='*60}")

    # 1. Load transcript
    print("[1/6] Loading transcript...")
    transcript = load_transcript(transcript_path)
    print(f"      Loaded {len(transcript):,} chars")

    # 2. Load prior memo
    prior_memo: dict | None = None
    if prior_memo_path:
        print(f"[2/6] Loading prior memo: {prior_memo_path}")
        with open(prior_memo_path) as f:
            prior_memo = json.load(f)
    else:
        print("[2/6] No prior memo (demo_call or first extraction)")

    # 3. Assign or validate account_id
    if account_id is None:
        account_id = allocate_account_id()
        print(f"[3/6] Allocated account ID: {account_id}")
    else:
        print(f"[3/6] Using provided account ID: {account_id}")

    # 4. account_id mismatch guard (Rule 8)
    mismatch = check_account_id_mismatch(account_id, prior_memo)
    if mismatch:
        print(f"\n[ERROR] Account ID mismatch detected!")
        print(f"        Injected: {mismatch['injected_id']}")
        print(f"        In memo:  {mismatch['memo_id']}")
        print("        PIPELINE HALTED. Correct the account_id and retry.")
        raise SystemExit(1)

    # 5. Build prompt and call Ollama
    print("[4/6] Building extraction prompt...")
    system_prompt, user_prompt = build_prompt(
        transcript, stage, account_id, prior_memo
    )
    print(
        f"      System: {len(system_prompt):,} chars | "
        f"User: {len(user_prompt):,} chars"
    )

    print(f"[5/6] Calling Ollama ({ollama_model})... (may take 30–120s)")
    raw_response = call_ollama(
        system_prompt,
        user_prompt,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        temperature=0.1,
    )
    print(f"      Received {len(raw_response):,} chars")

    # 6. Parse, post-process, validate
    print("[6/6] Parsing and validating...")
    try:
        memo = extract_json_from_response(raw_response)
    except ValueError as exc:
        debug_dir  = outputs_dir / (account_id or "unknown")
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_path = debug_dir / "debug_raw_response_s1.txt"
        debug_path.write_text(raw_response)
        print(f"\n[ERROR] JSON parse failed: {exc}")
        print(f"        Raw response saved to: {debug_path}")
        raise

    # Loophole 1/2 fix: log if LLM hallucinated a different account_id, then enforce ours
    llm_account_id = memo.get("account_id")
    if llm_account_id and llm_account_id != account_id:
        print(
            f"  [WARN] LLM returned account_id='{llm_account_id}' "
            f"(expected '{account_id}'). Overwriting with pipeline-assigned ID."
        )
    memo["account_id"] = account_id

    # Enforce version
    version       = "v2" if prior_memo else "v1"
    memo["version"] = version

    # Compute confidence (pipeline, not LLM)
    confidence_label, breakdown = compute_confidence(memo)
    memo["extraction_confidence"]       = confidence_label
    memo["confidence_score_breakdown"]  = breakdown
    score = sum(breakdown.values())
    print(
        f"      Confidence: {confidence_label} "
        f"({score}/8 fields populated)"
    )

    # Ensure required arrays exist (guard against LLM omitting them)
    if not isinstance(memo.get("questions_or_unknowns"), list):
        memo["questions_or_unknowns"] = []
    if not isinstance(memo.get("changelog"), list):
        memo["changelog"] = []

    # Ensure at least one changelog entry
    if len(memo["changelog"]) == 0:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        memo["changelog"].append({
            "version":        version,
            "timestamp":      ts,
            "changed_fields": [],
            "summary":        f"Extracted from {stage}.",
            "source":         stage,
        })

    # Validate
    errors = validate_memo(memo)
    if errors:
        print(f"\n  [WARN] {len(errors)} validation issue(s):")
        for err in errors:
            print(f"         - {err}")
    else:
        print("      Validation: PASSED")

    # Save
    out_path = save_memo(memo, account_id, version, outputs_dir)
    print(f"\n  ✓ Memo saved:  {out_path}")
    print(f"  Account ID:   {account_id}")
    print(f"  Version:      {version}")
    print(f"  Confidence:   {confidence_label}")
    print(f"  Unknowns:     {len(memo.get('questions_or_unknowns', []))}")

    return memo


# ── BATCH MODE ──────────────────────────────────────────────────────────────────

def run_batch(
    input_dir: str,
    stage: str,
    prior_memo_dir: str | None = None,
    *,
    ollama_url:   str  = OLLAMA_URL,
    ollama_model: str  = OLLAMA_MODEL,
    outputs_dir:  Path = OUTPUTS_DIR,
) -> None:
    """
    Batch-process all transcripts in input_dir.
    All account IDs are allocated upfront (batch-safe, no collisions).
    """
    supported        = {".txt", ".md", ".docx"}
    input_path       = Path(input_dir)
    transcript_files = sorted([
        f for f in input_path.iterdir()
        if f.is_file() and f.suffix.lower() in supported
    ])

    if not transcript_files:
        print(f"[ERROR] No transcript files found in: {input_dir}")
        sys.exit(1)

    n = len(transcript_files)
    print(f"\nBatch mode: {n} transcript(s) in {input_dir}")

    # Allocate all IDs upfront
    if stage == "demo_call":
        ids = allocate_batch_ids(n)
        print(f"Allocated {n} account IDs upfront: {ids[0]} → {ids[-1]}")
    else:
        ids = [None] * n   # resolved from prior memo during loop

    results = []
    for i, transcript_file in enumerate(transcript_files):
        print(f"\n[{i+1}/{n}] Processing: {transcript_file.name}")

        prior_memo_path = None
        account_id      = ids[i] if stage == "demo_call" else None

        if prior_memo_dir and stage != "demo_call":
            stem    = transcript_file.stem
            pm_path = Path(prior_memo_dir) / stem / "v1" / "account_memo.json"
            if pm_path.exists():
                prior_memo_path = str(pm_path)
                with open(pm_path) as f:
                    pm = json.load(f)
                account_id = pm.get("account_id")
                print(f"  Found prior memo: {account_id}")
            else:
                # Loophole 3 fix: halt on missing prior memo for onboarding
                print(
                    f"  [ERROR] No prior memo found at {pm_path} for "
                    f"onboarding transcript '{transcript_file.name}'.\n"
                    f"  Halting — cannot process an onboarding call without a v1 memo.\n"
                    f"  Run the demo_call stage first, or supply --prior-memo-dir "
                    f"pointing to a directory whose subdirectory names match the "
                    f"transcript filename stems."
                )
                results.append({
                    "file":       transcript_file.name,
                    "account_id": "?",
                    "status":     "error: prior memo not found — halted",
                })
                continue

        try:
            memo = run_extraction(
                transcript_path=str(transcript_file),
                stage=stage,
                account_id=account_id,
                prior_memo_path=prior_memo_path,
                ollama_url=ollama_url,
                ollama_model=ollama_model,
                outputs_dir=outputs_dir,
            )
            results.append({
                "file":       transcript_file.name,
                "account_id": memo["account_id"],
                "status":     "ok",
            })
        except Exception as exc:
            print(f"  [ERROR] {exc}")
            results.append({
                "file":       transcript_file.name,
                "account_id": account_id or "?",
                "status":     f"error: {exc}",
            })

    # Summary
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\n{'='*60}")
    print(f"BATCH COMPLETE: {n} files processed | {ok}/{n} succeeded")
    for r in results:
        icon = "✓" if r["status"] == "ok" else "✗"
        print(f"  {icon} {r['file']} → {r['account_id']} [{r['status']}]")


# ── CLI ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clara Pipeline Script 1: Transcript → Account Memo JSON (via Ollama)"
    )
    parser.add_argument("--transcript", help="Path to transcript (.txt, .md, .docx)")
    parser.add_argument(
        "--stage",
        required=True,
        choices=["demo_call", "onboarding_call", "onboarding_form"],
    )
    parser.add_argument("--account-id",     help="Account ID (auto-allocated for demo_call if omitted)")
    parser.add_argument("--prior-memo",     help="Path to prior v1 account_memo.json (onboarding)")
    parser.add_argument("--batch",          action="store_true", help="Batch mode")
    parser.add_argument("--input-dir",      help="Directory of transcripts (batch mode)")
    parser.add_argument("--prior-memo-dir", help="Base dir for prior memos (batch onboarding)")
    parser.add_argument("--model",          default=OLLAMA_MODEL, help="Ollama model name")
    parser.add_argument("--ollama-url",     default=OLLAMA_URL,   help="Ollama server URL")
    parser.add_argument("--check-health",   action="store_true",  help="Health-check Ollama and exit")

    args = parser.parse_args()
    url   = args.ollama_url
    model = args.model

    if args.check_health:
        ok, _ = check_ollama_health(ollama_url=url, ollama_model=model)
        sys.exit(0 if ok else 1)

    ok, model = check_ollama_health(ollama_url=url, ollama_model=model)
    if not ok:
        sys.exit(1)

    if args.batch:
        if not args.input_dir:
            parser.error("--batch requires --input-dir")
        run_batch(
            args.input_dir, args.stage, args.prior_memo_dir,
            ollama_url=url, ollama_model=model,
        )
    else:
        if not args.transcript:
            parser.error("--transcript is required (or use --batch)")
        if args.stage in ("onboarding_call", "onboarding_form") and not args.prior_memo:
            print("[WARN] Onboarding stage without --prior-memo. Prior memo strongly recommended.")

        memo = run_extraction(
            transcript_path=args.transcript,
            stage=args.stage,
            account_id=args.account_id,
            prior_memo_path=args.prior_memo,
            ollama_url=url,
            ollama_model=model,
        )
        print(f"\nSummary:")
        print(f"  company:    {memo.get('company', {}).get('name', '[not extracted]')}")
        print(f"  confidence: {memo.get('extraction_confidence')}")
        print(f"  unknowns:   {len(memo.get('questions_or_unknowns', []))}")


if __name__ == "__main__":
    main()
