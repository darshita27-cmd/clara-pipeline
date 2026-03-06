#!/usr/bin/env python3
"""
CLARA PIPELINE — run_all.py
Runs the full pipeline end-to-end on all demo + onboarding transcript pairs.

Usage:
    python run_all.py --demo-dir data/demo --onboarding-dir data/onboarding
    python run_all.py --pairs data/pairs.json
    python run_all.py --demo-dir data/demo --onboarding-dir data/onboarding --dry-run

File layout (auto-matching by filename stem):
    data/
      demo/
        client_a.txt
        client_b.docx
      onboarding/
        client_a.txt        ← stem must match demo file
        client_b.docx

Or use an explicit pairs JSON for full control:
    [
      {"demo": "data/demo/client_a.txt", "onboarding": "data/onboarding/client_a.txt"},
      ...
    ]
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── PATH BOOTSTRAP ──────────────────────────────────────────────────────────────
# Bug 1 fix: single authoritative SCRIPTS_DIR used everywhere in this file.
PROJECT_ROOT = Path(__file__).parent.resolve()
SCRIPTS_DIR  = PROJECT_ROOT / "scripts"

# Both run_all.py and script3's _import_script() use SCRIPTS_DIR, so the
# module cache in sys.modules is always keyed to the same physical path.
sys.path.insert(0, str(SCRIPTS_DIR))

from utils import DEFAULT_OLLAMA_URL, DEFAULT_OLLAMA_MODEL, check_ollama_health

OLLAMA_URL   = os.environ.get("OLLAMA_URL",   DEFAULT_OLLAMA_URL)
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)

OUTPUTS_DIR = PROJECT_ROOT / "outputs" / "accounts"


# ── PAIR DISCOVERY ───────────────────────────────────────────────────────────────

def find_pairs(demo_dir: str, onboarding_dir: str) -> list[dict]:
    """
    Auto-match demo and onboarding transcripts by filename stem.
    Unmatched files are reported as warnings (not errors).
    """
    supported = {".txt", ".md", ".docx"}

    def index(directory: str) -> dict[str, Path]:
        return {
            f.stem: f
            for f in Path(directory).iterdir()
            if f.is_file() and f.suffix.lower() in supported
        }

    demo_files        = index(demo_dir)
    onboarding_files  = index(onboarding_dir)

    pairs             = []
    unmatched_demo    = []
    unmatched_onboard = []

    for stem, demo_path in sorted(demo_files.items()):
        if stem in onboarding_files:
            pairs.append({
                "demo":       str(demo_path),
                "onboarding": str(onboarding_files[stem]),
            })
        else:
            unmatched_demo.append(str(demo_path))

    for stem, ob_path in sorted(onboarding_files.items()):
        if stem not in demo_files:
            unmatched_onboard.append(str(ob_path))

    if unmatched_demo:
        print(f"[WARN] Demo file(s) with no matching onboarding: {unmatched_demo}")
    if unmatched_onboard:
        print(f"[WARN] Onboarding file(s) with no matching demo: {unmatched_onboard}")

    return pairs


# ── SINGLE PAIR RUNNER ───────────────────────────────────────────────────────────

def run_pipeline_pair(
    pair:        dict,
    account_id:  str,
    dry_run:     bool = False,
    *,
    ollama_url:   str  = OLLAMA_URL,
    ollama_model: str  = OLLAMA_MODEL,
    outputs_dir:  Path = OUTPUTS_DIR,
) -> dict:
    """
    Run Phase A (demo → v1 memo + spec) and Phase B (onboarding → v2 + changelog)
    for a single pair.

    Bug 2 fix: ollama_url, ollama_model, and outputs_dir are passed as arguments
    to each sub-script call rather than mutating module-level globals.  This keeps
    batch runs safe when multiple pairs are processed sequentially.
    """
    demo_path       = pair["demo"]
    onboarding_path = pair.get("onboarding")

    print(f"\n{'#'*60}")
    print(f"# ACCOUNT: {account_id}")
    print(f"# Demo:         {demo_path}")
    print(f"# Onboarding:   {onboarding_path or 'N/A'}")
    print(f"{'#'*60}")

    if dry_run:
        print("[DRY RUN] Skipping Ollama calls")
        return {"account_id": account_id, "demo": demo_path, "status": "dry_run"}

    results = {"account_id": account_id, "demo": demo_path}

    # ── Phase A: Demo → v1 memo ──────────────────────────────────────────────
    try:
        import script1_extract_memo as s1

        print(f"\n→ Phase A1: Demo call extraction (v1)")
        v1_memo = s1.run_extraction(
            transcript_path=demo_path,
            stage="demo_call",
            account_id=account_id,
            ollama_url=ollama_url,
            ollama_model=ollama_model,
            outputs_dir=outputs_dir,
        )
        results["v1_memo"]  = "ok"
        results["company"]  = v1_memo.get("company", {}).get("name", "?")

        # ── Phase A2: v1 memo → v1 spec ─────────────────────────────────────
        import script2_generate_spec as s2

        v1_memo_path = outputs_dir / account_id / "v1" / "account_memo.json"
        print(f"\n→ Phase A2: Generate v1 Retell spec")
        s2.run_spec_generation(
            str(v1_memo_path),
            ollama_url=ollama_url,
            ollama_model=ollama_model,
            outputs_dir=outputs_dir,
        )
        results["v1_spec"] = "ok"

    except Exception as exc:
        print(f"[ERROR] Phase A failed: {exc}")
        results["v1_memo"] = f"error: {exc}"
        results["v1_spec"] = "skipped"
        return results

    # ── Phase B: Onboarding → v2 + changelog ────────────────────────────────
    if onboarding_path:
        try:
            import script3_onboarding_update as s3

            print(f"\n→ Phase B: Onboarding update (v2)")
            update_result = s3.run_onboarding_update(
                account_id,
                onboarding_path,
                ollama_url=ollama_url,
                ollama_model=ollama_model,
                outputs_dir=outputs_dir,
            )
            results.update({
                "v2_memo":            "ok",
                "v2_spec":            "ok",
                "changelog":          "ok",
                "go_live_ready":      update_result.get("go_live_ready",      False),
                "unknowns_resolved":  update_result.get("unknowns_resolved",  0),
                "unknowns_remaining": update_result.get("unknowns_remaining", 0),
            })
        except Exception as exc:
            print(f"[ERROR] Phase B failed: {exc}")
            results["v2_memo"] = f"error: {exc}"
    else:
        results["v2_memo"] = "skipped (no onboarding transcript)"
        print("[INFO] No onboarding transcript — skipping Phase B")

    return results


# ── MAIN ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clara Pipeline — Run all transcripts end-to-end"
    )
    parser.add_argument("--demo-dir",       help="Directory of demo transcripts")
    parser.add_argument("--onboarding-dir", help="Directory of onboarding transcripts")
    parser.add_argument("--pairs",          help="JSON file with explicit demo+onboarding pairs")
    parser.add_argument("--dry-run",        action="store_true", help="Print plan without Ollama calls")
    parser.add_argument("--model",          default=OLLAMA_MODEL)
    parser.add_argument("--ollama-url",     default=OLLAMA_URL)
    parser.add_argument("--force",          action="store_true", help="Re-run even if outputs exist")

    args  = parser.parse_args()
    url   = args.ollama_url
    model = args.model

    # ── Load pairs ────────────────────────────────────────────────────────────
    if args.pairs:
        with open(args.pairs) as f:
            pairs = json.load(f)
    elif args.demo_dir:
        onboarding_dir = args.onboarding_dir or args.demo_dir
        pairs          = find_pairs(args.demo_dir, onboarding_dir)
        if not pairs:
            print(f"[ERROR] No matched pairs found in {args.demo_dir}")
            sys.exit(1)
    else:
        parser.error("Provide --demo-dir or --pairs")

    print(f"\n{'='*60}")
    print(f"  CLARA PIPELINE — FULL RUN")
    print(f"  Model:  {model}")
    print(f"  Ollama: {url}")
    print(f"  Pairs:  {len(pairs)}")
    print(f"{'='*60}")

    # ── Ollama health check ───────────────────────────────────────────────────
    if not args.dry_run:
        ok, model = check_ollama_health(ollama_url=url, ollama_model=model)
        if not ok:
            sys.exit(1)

    # ── Pre-allocate all account IDs upfront (batch safety) ──────────────────
    if not args.dry_run:
        import script1_extract_memo as s1
        account_ids = s1.allocate_batch_ids(len(pairs))
        print(f"\n✓ Pre-allocated {len(account_ids)} account IDs: "
              f"{account_ids[0]} → {account_ids[-1]}")
    else:
        year        = datetime.now().year
        account_ids = [f"CLARA-{year}-{i+1:03d}" for i in range(len(pairs))]
        print(f"[DRY RUN] Would allocate: {account_ids[0]} → {account_ids[-1]}")

    # ── Run each pair ─────────────────────────────────────────────────────────
    all_results = []
    start_time  = time.time()

    for i, pair in enumerate(pairs):
        result = run_pipeline_pair(
            pair,
            account_ids[i],
            dry_run=args.dry_run,
            ollama_url=url,
            ollama_model=model,
            outputs_dir=OUTPUTS_DIR,
        )
        all_results.append(result)

    elapsed = time.time() - start_time

    # ── Final summary table ───────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  ALL DONE — {len(pairs)} account(s) | {elapsed:.1f}s")
    print(f"{'='*60}")
    print(f"\n{'Account ID':<20} {'Company':<30} {'v1':<6} {'v2':<6} {'Go-Live'}")
    print("-" * 80)
    for r in all_results:
        company = (r.get("company") or "?")[:28]
        v1      = "✓" if r.get("v1_spec") == "ok" else "✗"
        v2      = "✓" if r.get("v2_memo") == "ok" else "-"
        live    = ("✅" if r.get("go_live_ready")
                   else ("❌" if r.get("v2_memo") == "ok" else "-"))
        print(f"{r['account_id']:<20} {company:<30} {v1:<6} {v2:<6} {live}")

    # ── Save run summary (Issue 3 fix: include input pairs) ──────────────────
    summary_path = PROJECT_ROOT / "outputs" / "run_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump(
            {
                "run_at":         datetime.now(timezone.utc).isoformat(),
                "model":          model,
                "ollama_url":     url,
                "total_accounts": len(pairs),
                "elapsed_seconds": round(elapsed, 1),
                "input_pairs":    pairs,   # Issue 3 fix: persist for reproducibility
                "results":        all_results,
            },
            f,
            indent=2,
        )
    print(f"\n  Run summary saved: {summary_path}")


if __name__ == "__main__":
    main()
