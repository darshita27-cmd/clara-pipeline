#!/usr/bin/env python3
"""
CLARA PIPELINE — Script 2: Account Memo JSON → Retell Agent Spec
Phase 2 | Version: 1.1

Usage:
    python script2_generate_spec.py --memo <path/to/account_memo.json>
    python script2_generate_spec.py --batch --accounts-dir <outputs/accounts>
    python script2_generate_spec.py --batch --accounts-dir <outputs/accounts> --version v1

Ollama must be running: ollama serve
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── PATH BOOTSTRAP ──────────────────────────────────────────────────────────────
SCRIPTS_DIR  = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPTS_DIR.parent.resolve()
sys.path.insert(0, str(SCRIPTS_DIR))

from utils import (
    call_ollama,
    check_ollama_health,
    extract_json_from_response,
    IANA_DISPLAY,
    DEFAULT_OLLAMA_URL,
    DEFAULT_OLLAMA_MODEL,
)

# ── CONFIG ──────────────────────────────────────────────────────────────────────
OLLAMA_URL   = os.environ.get("OLLAMA_URL",   DEFAULT_OLLAMA_URL)
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)

OUTPUTS_DIR = PROJECT_ROOT / "outputs" / "accounts"
PROMPTS_DIR = PROJECT_ROOT / "prompts"


# ── PROMPT LOADING ───────────────────────────────────────────────────────────────

def _require_prompt_file(filename: str) -> Path:
    """Hard-fail if a required prompt file is missing — no silent fallback."""
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Required prompt file not found: {path}\n"
            f"Copy the phase1_v1.2 prompt files into: {PROMPTS_DIR}"
        )
    return path


def load_generator_prompt() -> str:
    return _require_prompt_file("agent_spec_generator_prompt.md").read_text(encoding="utf-8")


def load_agent_template() -> str:
    return _require_prompt_file("agent_prompt_template.md").read_text(encoding="utf-8")


def build_spec_prompt(memo: dict, version: str) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for spec generation."""
    generator = load_generator_prompt()
    template  = load_agent_template()

    # Extract system section (between ## SYSTEM PROMPT and ## USER PROMPT)
    if "## SYSTEM PROMPT" in generator:
        after_sys = generator.split("## SYSTEM PROMPT", 1)[1]
        system_raw = after_sys.split("## USER PROMPT")[0] if "## USER PROMPT" in after_sys else after_sys
    else:
        system_raw = generator

    system_prompt = system_raw.strip()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    user_prompt = f"""Account Memo:
```json
{json.dumps(memo, indent=2)}
```

Version: {version}

Agent Prompt Template (Handlebars-style — render all {{{{variables}}}} by substituting from memo):
```
{template[:4000]}
```

Generate the Retell Agent Spec JSON.
Timestamp for generated_at: {ts}

RENDERING RULES REMINDER:
- Fill every {{{{VARIABLE}}}} from the memo. Null values → [CONFIRM WITH CLIENT — field.path]
- callback_assurance_window: NEVER default. If null → [CONFIRM WITH CLIENT — callback window]
- transfer_fail_notification_action: silent system action, never spoken to caller
- tone_label affects writing style ONLY, not Retell API parameters
- Business hours: render as natural language (e.g. "Monday through Friday, 8:00 AM to 5:00 PM Central Time")
- escalation_chain[order=1] IS the primary contact for all EMERGENCY_PRIMARY_* variables
- questions_carried_forward: list ALL fields still null/unknown from the memo
- If questions_carried_forward is non-empty: version_notes must include "NOT READY FOR GO-LIVE"

Return ONLY valid JSON. No markdown fences. No explanation. Start with {{ and end with }}.
"""
    return system_prompt, user_prompt


# ── VALIDATION ──────────────────────────────────────────────────────────────────

def validate_spec(spec: dict) -> list[str]:
    """
    Structural validation of the generated spec.
    Returns a list of error strings (empty = passed).
    """
    errors: list[str] = []

    required = [
        "account_id", "version", "generated_at", "source_memo_version",
        "agent_name", "retell_config", "system_prompt", "key_variables",
        "tool_placeholders", "call_transfer_protocol", "fallback_protocol",
        "data_collection_fields", "version_notes", "questions_carried_forward",
    ]
    for field in required:
        if field not in spec:
            errors.append(f"Missing required field: '{field}'")

    # Bug 6 fix: case-insensitive regex catches {{company_name}} etc.
    if "system_prompt" in spec:
        unrendered = re.findall(r"\{\{\w+\}\}", spec["system_prompt"])
        if unrendered:
            errors.append(
                f"Unrendered template variables in system_prompt: {unrendered}. "
                "All {{VARIABLES}} must be substituted before deployment."
            )

    # Version pattern
    if "version" in spec:
        if not re.match(r"^v[0-9]+$", str(spec["version"])):
            errors.append(f"Invalid version: '{spec['version']}' (expected v1, v2, …)")

    return errors


def is_deployment_ready(spec: dict) -> bool:
    """
    Go-live gate: returns True only if questions_carried_forward is empty.
    Specs that fail this check are saved with a .draft suffix.
    Defined in DESIGN.md §Go-Live Gate.
    """
    unresolved = spec.get("questions_carried_forward", [])
    if unresolved:
        print(
            f"  [BLOCKED] {spec.get('account_id')} {spec.get('version')} — "
            f"{len(unresolved)} unresolved field(s):"
        )
        for item in unresolved:
            print(f"    - {item}")
        return False
    return True


# ── POST-PROCESS / PATCH ─────────────────────────────────────────────────────────

def patch_spec_from_memo(spec: dict, memo: dict) -> dict:
    """
    Enforce memo-derived values onto the spec as a safety layer.
    Catches cases where the LLM may have mis-assigned or omitted fields.
    Called IMMEDIATELY after JSON parsing (before any logging) to prevent
    a hallucinated account_id from being written anywhere.
    """
    spec["account_id"]          = memo["account_id"]
    spec["version"]             = memo["version"]
    spec["source_memo_version"] = memo["version"]

    company_name    = memo.get("company", {}).get("name") or "[COMPANY NAME]"
    spec["agent_name"] = f"Clara - {company_name}"

    # Retell config defaults
    rc = spec.setdefault("retell_config", {})
    rc.setdefault("voice_id",                 "eleven_labs_rachel")
    rc.setdefault("voice_speed",              1.0)
    rc.setdefault("voice_temperature",        0.7)
    rc.setdefault("responsiveness",           1.0)
    rc.setdefault("interruption_sensitivity", 0.8)
    rc.setdefault("enable_backchannel",       True)
    rc.setdefault("language",                 "en-US")
    rc.setdefault("max_call_duration_ms",     600000)

    # Minimum tool_placeholders
    if not spec.get("tool_placeholders"):
        chain         = memo.get("emergency_routing", {}).get("escalation_chain") or []
        primary_phone = next(
            (e["phone"] for e in chain if isinstance(e, dict) and e.get("order") == 1 and e.get("phone")),
            "[UNCONFIRMED]",
        )
        spec["tool_placeholders"] = [
            {
                "tool_name": "transfer_call",
                "trigger":   "emergency confirmed and caller info collected",
                "target":    primary_phone,
                "note":      "Never mention to caller",
            },
            {
                "tool_name": "end_call",
                "trigger":   "caller confirms nothing else needed",
            },
            {
                "tool_name": "log_call_details",
                "trigger":   "any call where transfer fails or non-emergency after-hours",
                "note":      "Silent background logging",
            },
        ]

    spec.setdefault("data_collection_fields", {
        "emergency":                ["caller_name", "callback_number", "property_address", "issue_description"],
        "non_emergency_after_hours":["caller_name", "callback_number", "service_description"],
        "business_hours":           ["caller_name", "callback_number"],
    })

    # Build questions_carried_forward from memo if LLM didn't populate it
    if not spec.get("questions_carried_forward"):
        spec["questions_carried_forward"] = [
            q["field"] for q in memo.get("questions_or_unknowns", [])
        ]

    return spec


# ── OUTPUT ──────────────────────────────────────────────────────────────────────

def save_spec(
    spec:       dict,
    account_id: str,
    version:    str,
    is_draft:   bool = False,
    outputs_dir: Path = OUTPUTS_DIR,
) -> Path:
    """Save spec to outputs/accounts/{id}/{version}/retell_agent_spec[.draft].json"""
    out_dir  = outputs_dir / account_id / version
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix   = ".draft" if is_draft else ""
    out_path = out_dir / f"retell_agent_spec{suffix}.json"
    with open(out_path, "w") as f:
        json.dump(spec, f, indent=2)
    return out_path


# ── MAIN ─────────────────────────────────────────────────────────────────────────

def run_spec_generation(
    memo_path:   str,
    *,
    ollama_url:   str  = OLLAMA_URL,
    ollama_model: str  = OLLAMA_MODEL,
    outputs_dir:  Path = OUTPUTS_DIR,
) -> dict:
    """Full spec generation pipeline for one memo."""
    print(f"\n{'='*60}")
    print(f"  SCRIPT 2: ACCOUNT MEMO → RETELL AGENT SPEC")
    print(f"  Memo: {memo_path}")
    print(f"{'='*60}")

    # 1. Load memo
    print("[1/5] Loading account memo...")
    with open(memo_path) as f:
        memo = json.load(f)

    account_id = memo["account_id"]
    version    = memo["version"]
    company    = memo.get("company", {}).get("name", "[Unknown]")
    print(f"      Account: {account_id} | Version: {version} | Company: {company}")

    # 2. Build prompt
    print("[2/5] Building spec generation prompt...")
    system_prompt, user_prompt = build_spec_prompt(memo, version)
    print(f"      Total: {len(system_prompt) + len(user_prompt):,} chars")

    # 3. Call Ollama
    print(f"[3/5] Calling Ollama ({ollama_model})... (30–120s)")
    raw = call_ollama(
        system_prompt,
        user_prompt,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        temperature=0.2,    # slightly higher for prompt-writing creativity
    )
    print(f"      Received {len(raw):,} chars")

    # 4. Parse — then immediately patch before any logging (Loophole 1 fix)
    print("[4/5] Parsing and post-processing...")
    try:
        spec = extract_json_from_response(raw)
    except ValueError as exc:
        debug_dir  = outputs_dir / account_id / version
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_path = debug_dir / "debug_spec_raw_response.txt"
        debug_path.write_text(raw)
        print(f"  [ERROR] {exc}")
        print(f"          Raw response saved to: {debug_path}")
        raise

    # Enforce memo-derived fields immediately (before any downstream use)
    spec = patch_spec_from_memo(spec, memo)

    # Validate
    errors = validate_spec(spec)
    if errors:
        print(f"  [WARN] {len(errors)} validation issue(s):")
        for err in errors:
            print(f"         - {err}")
    else:
        print("  Validation: PASSED")

    # Go-live gate
    is_draft = not is_deployment_ready(spec)

    # 5. Save
    print("[5/5] Saving spec...")
    out_path = save_spec(spec, account_id, version, is_draft=is_draft, outputs_dir=outputs_dir)
    status   = "DRAFT (not go-live ready)" if is_draft else "READY"
    print(f"\n  ✓ Spec saved: {out_path}")
    print(f"  Status:       {status}")
    print(f"  Questions:    {len(spec.get('questions_carried_forward', []))}")

    return spec


# ── BATCH MODE ──────────────────────────────────────────────────────────────────

def run_batch(
    accounts_dir:   str,
    version_filter: str | None = None,
    *,
    ollama_url:   str  = OLLAMA_URL,
    ollama_model: str  = OLLAMA_MODEL,
    outputs_dir:  Path = OUTPUTS_DIR,
) -> None:
    """Process all unprocessed account_memo.json files in accounts_dir."""
    base       = Path(accounts_dir)
    memo_files = []

    for account_dir in sorted(base.iterdir()):
        if not account_dir.is_dir():
            continue
        for version_dir in sorted(account_dir.iterdir()):
            if not version_dir.is_dir():
                continue
            if version_filter and version_dir.name != version_filter:
                continue
            memo_path  = version_dir / "account_memo.json"
            spec_path  = version_dir / "retell_agent_spec.json"
            # Bug 7 fix: also skip if draft already exists
            draft_path = version_dir / "retell_agent_spec.draft.json"

            if spec_path.exists() or draft_path.exists():
                existing = spec_path if spec_path.exists() else draft_path
                print(f"  [SKIP] Spec already exists: {existing}")
                continue
            if memo_path.exists():
                memo_files.append(memo_path)

    if not memo_files:
        print(f"[INFO] No unprocessed memos found in: {accounts_dir}")
        return

    print(f"\nBatch spec generation: {len(memo_files)} memo(s)")
    results = []
    for memo_path in memo_files:
        try:
            spec = run_spec_generation(
                str(memo_path),
                ollama_url=ollama_url,
                ollama_model=ollama_model,
                outputs_dir=outputs_dir,
            )
            results.append({"memo": str(memo_path), "status": "ok", "account_id": spec["account_id"]})
        except Exception as exc:
            print(f"  [ERROR] {memo_path}: {exc}")
            results.append({"memo": str(memo_path), "status": f"error: {exc}"})

    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"\nBatch complete: {ok}/{len(results)} succeeded")


# ── CLI ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clara Pipeline Script 2: Account Memo → Retell Agent Spec (via Ollama)"
    )
    parser.add_argument("--memo",         help="Path to account_memo.json")
    parser.add_argument("--batch",        action="store_true", help="Process all memos in --accounts-dir")
    parser.add_argument("--accounts-dir", help="Path to outputs/accounts directory")
    parser.add_argument("--version",      help="Filter to specific version in batch mode (e.g. v1)")
    parser.add_argument("--model",        default=OLLAMA_MODEL)
    parser.add_argument("--ollama-url",   default=OLLAMA_URL)

    args  = parser.parse_args()
    url   = args.ollama_url
    model = args.model

    if args.batch:
        target = args.accounts_dir or str(OUTPUTS_DIR)
        run_batch(target, args.version, ollama_url=url, ollama_model=model)
    else:
        if not args.memo:
            parser.error("--memo is required (or use --batch)")
        run_spec_generation(args.memo, ollama_url=url, ollama_model=model)


if __name__ == "__main__":
    main()
