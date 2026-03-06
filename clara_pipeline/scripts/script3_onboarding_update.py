#!/usr/bin/env python3
"""
CLARA PIPELINE — Script 3: v1 + Onboarding Transcript → v2 + Changelog
Phase 2 | Version: 1.1

Orchestrates the full onboarding update cycle:
  1. Loads v1 account_memo.json + onboarding transcript
  2. Calls Script 1 logic → patches → v2 account_memo.json
  3. Calls Script 2 logic → regenerates → v2 retell_agent_spec.json
  4. Computes diff between v1 and v2
  5. Writes changes.md (human-readable) + changes.json (machine-readable)

Usage:
    python script3_onboarding_update.py --account-id CLARA-2026-001 --transcript <path>
    python script3_onboarding_update.py --batch --pairs-file pairs.json [--force]

pairs.json format:
    [
      {"account_id": "CLARA-2026-001", "transcript": "data/onboarding/client_a.docx"},
      ...
    ]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── PATH BOOTSTRAP ──────────────────────────────────────────────────────────────
SCRIPTS_DIR  = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPTS_DIR.parent.resolve()
sys.path.insert(0, str(SCRIPTS_DIR))

from utils import DEFAULT_OLLAMA_URL, DEFAULT_OLLAMA_MODEL

# ── CONFIG ──────────────────────────────────────────────────────────────────────
OLLAMA_URL   = os.environ.get("OLLAMA_URL",   DEFAULT_OLLAMA_URL)
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)

OUTPUTS_DIR = PROJECT_ROOT / "outputs" / "accounts"


# ── SIBLING SCRIPT IMPORTS ───────────────────────────────────────────────────────

def _import_script(name: str):
    """
    Import a sibling script as a module.

    Bug fix (Loophole 5): registers the module in sys.modules so repeated
    calls return the cached version rather than re-executing the file each time.
    This prevents repeated top-level side effects and ensures batch runs use a
    consistent module state.
    """
    import importlib.util

    cache_key = name.replace(".py", "")
    if cache_key in sys.modules:
        return sys.modules[cache_key]

    spec_path  = SCRIPTS_DIR / name
    spec       = importlib.util.spec_from_file_location(cache_key, spec_path)
    mod        = importlib.util.module_from_spec(spec)
    sys.modules[cache_key] = mod          # register BEFORE exec to handle circular refs
    spec.loader.exec_module(mod)
    return mod


# ── DIFF ENGINE ──────────────────────────────────────────────────────────────────

def _flatten(obj, prefix: str = "") -> dict:
    """Flatten a nested dict/list to dot-notation keys."""
    result: dict = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_key = f"{prefix}.{k}" if prefix else k
            result.update(_flatten(v, new_key))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            result.update(_flatten(item, f"{prefix}[{i}]"))
    else:
        result[prefix] = obj
    return result


def compute_diff(v1: dict, v2: dict) -> dict:
    """
    Compute a structured diff between v1 and v2 account memos.

    Bug 8 fix: null→value transitions are classified as 'added' (newly
    confirmed), not 'changed', so the changelog labels them correctly.

    Returns:
        {
          added:            {field: new_value},     # null in v1, non-null in v2
          removed:          {field: old_value},     # non-null in v1, null/missing in v2
          changed:          {field: {old, new}},    # both non-null but different
          unchanged_count:  int,
          summary_fields:   [top-level keys that had any change]
        }
    """
    # Meta-fields that always differ between versions — exclude from diff
    skip_prefixes = {
        "version", "extracted_at", "changelog",
        "extraction_confidence", "confidence_score_breakdown", "source_stage",
    }

    def should_skip(key: str) -> bool:
        return any(key == s or key.startswith(s + ".") or key.startswith(s + "[")
                   for s in skip_prefixes)

    flat1 = {k: v for k, v in _flatten(v1).items() if not should_skip(k)}
    flat2 = {k: v for k, v in _flatten(v2).items() if not should_skip(k)}

    all_keys      = set(flat1) | set(flat2)
    added:   dict = {}
    removed: dict = {}
    changed: dict = {}
    unchanged_count = 0

    for key in all_keys:
        val1 = flat1.get(key)       # None if key absent
        val2 = flat2.get(key)
        in1  = key in flat1
        in2  = key in flat2

        if in1 and in2:
            if val1 == val2:
                unchanged_count += 1
            elif val1 is None and val2 is not None:
                # Bug 8 fix: null → value = newly confirmed, not "changed"
                added[key] = val2
            elif val1 is not None and val2 is None:
                removed[key] = val1
            else:
                changed[key] = {"old": val1, "new": val2}
        elif in2 and not in1:
            if val2 is not None:
                added[key] = val2
        elif in1 and not in2:
            if val1 is not None:
                removed[key] = val1

    # Top-level section keys that changed
    changed_top: set[str] = set()
    for key in list(added) + list(removed) + list(changed):
        top = key.split(".")[0].split("[")[0]
        changed_top.add(top)

    return {
        "added":           added,
        "removed":         removed,
        "changed":         changed,
        "unchanged_count": unchanged_count,
        "summary_fields":  sorted(changed_top),
    }


# ── CHANGELOG GENERATION ─────────────────────────────────────────────────────────

def _safe_md(value) -> str:
    """
    Edge case 3 fix: escape markdown special characters in LLM-derived values
    so hallucinated content (**, #, backticks) doesn't break the changelog
    structure.
    """
    s = json.dumps(value) if not isinstance(value, str) else value
    # Escape backticks and leading # which could create headers
    s = s.replace("`", "\\`").replace("\n", " ")
    return s


def generate_changelog_md(diff: dict, v1_memo: dict, v2_memo: dict) -> str:
    """Generate human-readable changes.md content."""
    account_id = v2_memo.get("account_id", "UNKNOWN")
    company    = _safe_md(v2_memo.get("company", {}).get("name", "Unknown Company"))
    ts         = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Clara Pipeline — Changelog",
        "",
        f"**Account:** {account_id}  ",
        f"**Company:** {company}  ",
        "**Change:** v1 → v2  ",
        f"**Generated:** {ts}  ",
        "**Source:** onboarding_call  ",
        "",
        "---",
        "",
        "## Summary",
        "",
        f"- **{len(diff['changed'])}** field(s) updated",
        f"- **{len(diff['added'])}** field(s) newly confirmed",
        f"- **{len(diff['removed'])}** field(s) removed/cleared",
        f"- **{diff['unchanged_count']}** field(s) unchanged",
        "",
    ]

    if diff["summary_fields"]:
        lines.append(f"**Top-level sections affected:** {', '.join(diff['summary_fields'])}")
        lines.append("")

    lines += ["---", ""]

    # Changed fields (both values non-null, but different)
    if diff["changed"]:
        lines += ["## Fields Updated", ""]
        for field, vals in sorted(diff["changed"].items()):
            lines += [
                f"### `{field}`",
                f"- **Before:** `{_safe_md(vals['old'])}`",
                f"- **After:**  `{_safe_md(vals['new'])}`",
                "",
            ]

    # Newly populated (null → value)
    if diff["added"]:
        lines += ["## Fields Newly Confirmed", ""]
        for field, val in sorted(diff["added"].items()):
            lines.append(f"- **`{field}`** → `{_safe_md(val)}`")
        lines.append("")

    # Resolved vs remaining unknowns
    v1_unknown_fields = {q["field"] for q in v1_memo.get("questions_or_unknowns", [])}
    v2_unknown_fields = {q["field"] for q in v2_memo.get("questions_or_unknowns", [])}
    resolved  = v1_unknown_fields - v2_unknown_fields
    remaining = v2_unknown_fields

    if resolved:
        lines += [f"## Resolved Unknowns ({len(resolved)})", ""]
        for field in sorted(resolved):
            lines.append(f"- ✓ `{field}`")
        lines.append("")

    if remaining:
        lines += [f"## Still Unresolved ({len(remaining)})", ""]
        for q in v2_memo.get("questions_or_unknowns", []):
            lines.append(f"- ⚠️  `{q['field']}` — {q.get('reason', '')}")
            if q.get("suggested_question"):
                lines.append(f"     → *{_safe_md(q['suggested_question'])}*")
        lines.append("")

    # Go-live readiness
    lines += ["---", "", "## Go-Live Readiness", ""]
    if remaining:
        lines.append(f"❌ **NOT READY FOR GO-LIVE** — {len(remaining)} unresolved field(s)")
    else:
        lines.append("✅ **READY FOR GO-LIVE** — All required fields confirmed")
    lines.append("")

    # LLM-generated changelog notes (from memo)
    v2_changelog = v2_memo.get("changelog", [])
    if len(v2_changelog) > 1:
        lines += ["---", "", "## LLM-Generated Changelog Notes", ""]
        for entry in v2_changelog:
            lines.append(
                f"**{entry.get('version', '?')}** "
                f"({entry.get('timestamp', '?')}) — {entry.get('source', '?')}"
            )
            lines.append(f"> {_safe_md(entry.get('summary', ''))}")
            if entry.get("changed_fields"):
                lines.append(f"> Fields: {', '.join(entry['changed_fields'])}")
            lines.append("")

    return "\n".join(lines)


def generate_changelog_json(diff: dict, v1_memo: dict, v2_memo: dict) -> dict:
    """Generate machine-readable changes.json."""
    account_id = v2_memo.get("account_id", "UNKNOWN")
    ts         = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    v1_fields = {q["field"] for q in v1_memo.get("questions_or_unknowns", [])}
    v2_fields = {q["field"] for q in v2_memo.get("questions_or_unknowns", [])}
    resolved  = sorted(v1_fields - v2_fields)
    remaining = sorted(v2_fields)

    return {
        "account_id":  account_id,
        "transition":  "v1_to_v2",
        "generated_at": ts,
        "source":       "onboarding_call",
        "go_live_ready": len(remaining) == 0,
        "stats": {
            "fields_changed":          len(diff["changed"]),
            "fields_newly_confirmed":  len(diff["added"]),
            "fields_removed":          len(diff["removed"]),
            "unknowns_resolved":       len(resolved),
            "unknowns_remaining":      len(remaining),
        },
        "fields_changed":           diff["changed"],
        "fields_newly_confirmed":   diff["added"],
        "fields_removed":           diff["removed"],
        "unknowns_resolved":        resolved,
        "unknowns_remaining":       remaining,
        "summary_sections_affected": diff["summary_fields"],
    }


def save_changelog(
    account_id:   str,
    changes_md:   str,
    changes_json: dict,
    outputs_dir:  Path = OUTPUTS_DIR,
) -> tuple[Path, Path]:
    """Save changes.md and changes.json to outputs/accounts/{id}/v2/"""
    out_dir = outputs_dir / account_id / "v2"
    out_dir.mkdir(parents=True, exist_ok=True)

    md_path   = out_dir / "changes.md"
    json_path = out_dir / "changes.json"

    md_path.write_text(changes_md, encoding="utf-8")
    with open(json_path, "w") as f:
        json.dump(changes_json, f, indent=2)

    return md_path, json_path


# ── IDEMPOTENCY CHECK ─────────────────────────────────────────────────────────────

def check_idempotent(account_id: str, outputs_dir: Path = OUTPUTS_DIR) -> bool:
    """
    Return True (and skip) if v2 already exists for this account.
    Running the pipeline twice on the same account must not create chaos.
    """
    v2_memo = outputs_dir / account_id / "v2" / "account_memo.json"
    if v2_memo.exists():
        print(f"  [IDEMPOTENT] v2 already exists for {account_id}. Skipping.")
        print(f"               Delete {v2_memo.parent} to force a re-run.")
        return True
    return False


# ── MAIN ORCHESTRATOR ─────────────────────────────────────────────────────────────

def run_onboarding_update(
    account_id:      str,
    transcript_path: str,
    force:           bool = False,
    *,
    ollama_url:   str  = OLLAMA_URL,
    ollama_model: str  = OLLAMA_MODEL,
    outputs_dir:  Path = OUTPUTS_DIR,
) -> dict:
    """
    Full onboarding update: v1 → v2 with changelog.

    Args:
        account_id:      CLARA-YYYY-NNN identifier.
        transcript_path: Path to onboarding transcript.
        force:           Skip idempotency check and re-run.
        ollama_url:      Ollama server URL (passed through to scripts 1 & 2).
        ollama_model:    Ollama model (passed through to scripts 1 & 2).
        outputs_dir:     Root outputs/accounts path.

    Returns:
        Summary dict with paths and status.
    """
    print(f"\n{'='*60}")
    print(f"  SCRIPT 3: ONBOARDING UPDATE — v1 → v2")
    print(f"  Account:    {account_id}")
    print(f"  Transcript: {transcript_path}")
    print(f"{'='*60}")

    if not force and check_idempotent(account_id, outputs_dir):
        return {
            "account_id": account_id,
            "status":     "skipped_idempotent",
            "v2_dir":     str(outputs_dir / account_id / "v2"),
        }

    # Verify v1 memo exists
    v1_memo_path = outputs_dir / account_id / "v1" / "account_memo.json"
    if not v1_memo_path.exists():
        raise FileNotFoundError(
            f"v1 account memo not found: {v1_memo_path}\n"
            f"Run Script 1 (demo_call stage) for {account_id} first."
        )

    print(f"\n[STEP 1/4] Loading v1 memo...")
    with open(v1_memo_path) as f:
        v1_memo = json.load(f)
    print(f"  Company:     {v1_memo.get('company', {}).get('name', '?')}")
    print(f"  v1 unknowns: {len(v1_memo.get('questions_or_unknowns', []))}")

    # Import script modules (cached after first call — Loophole 5 fix)
    s1 = _import_script("script1_extract_memo.py")
    s2 = _import_script("script2_generate_spec.py")

    # STEP 2: Script 1 — onboarding extraction → v2 memo
    print(f"\n[STEP 2/4] Running Script 1 (onboarding extraction)...")
    v2_memo = s1.run_extraction(
        transcript_path=transcript_path,
        stage="onboarding_call",
        account_id=account_id,
        prior_memo_path=str(v1_memo_path),
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        outputs_dir=outputs_dir,       # Bug 2 fix: passed as arg, not module-global mutation
    )

    # STEP 3: Script 2 — spec regeneration → v2 spec
    print(f"\n[STEP 3/4] Running Script 2 (spec regeneration)...")
    v2_memo_path = outputs_dir / account_id / "v2" / "account_memo.json"
    v2_spec = s2.run_spec_generation(
        str(v2_memo_path),
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        outputs_dir=outputs_dir,       # Bug 2 fix: passed as arg, not module-global mutation
    )

    # STEP 4: Diff and changelog
    print(f"\n[STEP 4/4] Computing diff and generating changelog...")
    with open(v2_memo_path) as f:
        v2_memo_final = json.load(f)

    diff         = compute_diff(v1_memo, v2_memo_final)
    changes_md   = generate_changelog_md(diff, v1_memo, v2_memo_final)
    changes_json = generate_changelog_json(diff, v1_memo, v2_memo_final)
    md_path, json_path = save_changelog(account_id, changes_md, changes_json, outputs_dir)

    # Summary
    print(f"\n{'='*60}")
    print(f"  ONBOARDING UPDATE COMPLETE")
    print(f"  Account:            {account_id}")
    print(f"  Fields updated:     {len(diff['changed'])}")
    print(f"  Newly confirmed:    {len(diff['added'])}")
    print(f"  Unknowns resolved:  {len(changes_json['unknowns_resolved'])}")
    print(f"  Unknowns remaining: {len(changes_json['unknowns_remaining'])}")
    print(f"  Go-live ready:      {'✅ YES' if changes_json['go_live_ready'] else '❌ NO (see changes.md)'}")
    print(f"\n  Outputs:")
    print(f"    v2 memo:    {v2_memo_path}")
    v2_spec_path  = outputs_dir / account_id / "v2" / "retell_agent_spec.json"
    v2_draft_path = outputs_dir / account_id / "v2" / "retell_agent_spec.draft.json"
    if v2_spec_path.exists():
        print(f"    v2 spec:    {v2_spec_path}")
    elif v2_draft_path.exists():
        print(f"    v2 spec:    {v2_draft_path} [DRAFT]")
    print(f"    changelog:  {md_path}")
    print(f"    diff JSON:  {json_path}")
    print(f"{'='*60}")

    return {
        "account_id":          account_id,
        "status":              "ok",
        "go_live_ready":       changes_json["go_live_ready"],
        "fields_changed":      len(diff["changed"]),
        "fields_confirmed":    len(diff["added"]),
        "unknowns_resolved":   len(changes_json["unknowns_resolved"]),
        "unknowns_remaining":  len(changes_json["unknowns_remaining"]),
        "v2_memo":             str(v2_memo_path),
        "changelog_md":        str(md_path),
        "changelog_json":      str(json_path),
    }


# ── BATCH MODE ──────────────────────────────────────────────────────────────────

def run_batch(
    pairs: list[dict],
    force: bool = False,
    *,
    ollama_url:   str  = OLLAMA_URL,
    ollama_model: str  = OLLAMA_MODEL,
    outputs_dir:  Path = OUTPUTS_DIR,
) -> list[dict]:
    """Batch-process multiple onboarding updates."""
    print(f"\nBatch onboarding update: {len(pairs)} pair(s)")
    results = []

    for i, pair in enumerate(pairs):
        account_id = pair.get("account_id")
        transcript = pair.get("transcript")
        print(f"\n[{i+1}/{len(pairs)}] {account_id} ← {transcript}")

        if not account_id or not transcript:
            print(f"  [ERROR] Missing account_id or transcript in pair: {pair}")
            results.append({"account_id": account_id, "status": "error: missing fields"})
            continue

        try:
            result = run_onboarding_update(
                account_id,
                transcript,
                force=force,
                ollama_url=ollama_url,
                ollama_model=ollama_model,
                outputs_dir=outputs_dir,
            )
            results.append(result)
        except Exception as exc:
            print(f"  [ERROR] {exc}")
            results.append({"account_id": account_id, "status": f"error: {exc}"})

    ok      = sum(1 for r in results if r.get("status") == "ok")
    skipped = sum(1 for r in results if r.get("status") == "skipped_idempotent")
    errors  = len(results) - ok - skipped
    print(f"\n{'='*60}")
    print(f"BATCH COMPLETE: {len(pairs)} pair(s)")
    print(f"  Success:  {ok}")
    print(f"  Skipped:  {skipped} (already have v2)")
    print(f"  Errors:   {errors}")
    print(f"{'='*60}")

    return results


# ── CLI ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clara Pipeline Script 3: v1 + Onboarding Transcript → v2 + Changelog"
    )
    parser.add_argument("--account-id",  help="Account ID (e.g. CLARA-2026-001)")
    parser.add_argument("--transcript",  help="Path to onboarding transcript")
    parser.add_argument("--batch",       action="store_true", help="Batch mode")
    parser.add_argument("--pairs-file",  help="JSON file: [{account_id, transcript}, ...]")
    parser.add_argument("--force",       action="store_true", help="Re-run even if v2 exists")
    parser.add_argument("--model",       default=OLLAMA_MODEL)
    parser.add_argument("--ollama-url",  default=OLLAMA_URL)

    args  = parser.parse_args()
    url   = args.ollama_url
    model = args.model

    if args.batch:
        if not args.pairs_file:
            parser.error("--batch requires --pairs-file")
        with open(args.pairs_file) as f:
            pairs = json.load(f)
        run_batch(pairs, force=args.force, ollama_url=url, ollama_model=model)
    else:
        if not args.account_id or not args.transcript:
            parser.error("--account-id and --transcript are required (or use --batch)")
        run_onboarding_update(
            args.account_id, args.transcript,
            force=args.force, ollama_url=url, ollama_model=model,
        )


if __name__ == "__main__":
    main()
