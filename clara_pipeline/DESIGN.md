# Clara Pipeline — Schema & Prompt Design Doc
# Version: 1.2
## Hours 1-2 Deliverables (v1.2 — loopholes and edge cases resolved)

---

## What Was Built

### 1. `account_memo.schema.json`
The master data structure. Key design decisions:

**Explicit null over omission.** Every field that could be missing has `"type": ["X", "null"]`. The pipeline never has ambiguity about whether a field was extracted or just forgotten.

**`questions_or_unknowns` and `changelog` are now in `required` array.** Previously documented as mandatory in this file but absent from the schema's required array — now fixed. Pipeline script will reject any memo missing these keys, even if they are empty arrays.

**Changelog is append-only.** v1 → v2 never overwrites — it appends a new entry. `minItems: 1` enforced in schema.

**Version uses regex pattern, not enum.** Changed from `["v1", "v2", "v3"]` to `"^v[0-9]+$"`. No upper cap on versions.

**`primary_contact` removed — `escalation_chain[order=1]` is the single source of truth.** Previously, `primary_contact` and `escalation_chain[0]` duplicated the same contact. Any phone number update would need to be made in two places, causing inconsistency. Now `order=1` in the chain is always the primary. The spec generator is instructed to derive `EMERGENCY_PRIMARY_PHONE/NAME/ROLE` from `escalation_chain[order=1]`.

**`non_emergency_routing.during_hours_action` is now a structured object.** Previously a free-text string ("Transfer to office main line") with no actionable phone number. Now has `action_type`, `phone`, and `instructions` fields so the agent has an actual number to call.

**`callback_assurance_window` is now a named field.** Moved from a hardcoded "15 minutes" default in the template to an explicit field in `emergency_routing`. Must be confirmed on the onboarding call. If null → flagged in `questions_or_unknowns`. Never defaulted silently.

**`transfer_fail_notification_action` is now a named field.** Previously referenced in the agent template as `{{TRANSFER_FAIL_NOTIFICATION_ACTION}}` but had no corresponding source field and no mapping in the variable table. Now it lives at `emergency_routing.transfer_fail_notification_action` and is fully mapped.

**`confidence_score_breakdown` replaces LLM self-assessment.** The LLM was previously asked to self-score confidence using vague thresholds ("7+ major fields"). This was inconsistent. Now the LLM populates 8 boolean flags (`confidence_score_breakdown`) and the pipeline script computes `extraction_confidence` deterministically: 7-8 true = high, 4-6 = medium, 0-3 = low.

**`additional_locations` stub added.** Some fire protection companies have multiple branches. The schema now has an `additional_locations` array under `company` so multi-location data isn't silently dropped if it appears in onboarding.

**`tone_label` renamed from `tone`.** The field is an internal human-readable descriptor only. It does NOT map to any Retell API parameter. Renamed and documented to prevent the spec generator from treating it as a Retell config value. The actual voice is controlled by `retell_config.voice_id`.

**`holidays_observed` remains free-text** — acknowledged as a known limitation. The agent cannot programmatically reason about holidays from free text. A structured holiday list is a Phase 3+ improvement. Flagged in README known limitations.

---

### 2. `extraction_prompt.md`
The LLM instruction set for reading transcripts. Key design decisions:

**Stage injection.** The prompt changes behavior based on `{STAGE}`. Demo call = extract what's there, flag gaps. Onboarding call = patch the prior memo, only touch what was addressed.

**Explicit non-hallucination rules at the top.** Rule 1 and Rule 2 are prominent. The LLM sees these before any schema guidance.

**List merge rules added (Rule 5).** Previously, the onboarding patch logic said "only modify explicitly addressed fields" with no definition of what to do when lists partially overlap. Now there are four named strategies: REPLACE, APPEND, PARTIAL OVERRIDE, AMBIGUOUS (defaults to append). Each has signal words and a required changelog note.

**`callback_assurance_window` rule added (Rule 6).** Explicit prohibition on defaulting to "15 minutes". Must be confirmed by client or left null.

**`transfer_fail_notification_action` extraction guidance added (Rule 7).** Tells the LLM what phrases to look for and where to store the result.

**Confidence scoring moved to pipeline script.** LLM now populates `confidence_score_breakdown` (8 boolean fields). Pipeline computes the label.

**Field-by-field guidance updated** for: escalation_chain (no more primary_contact), during_hours_action (structured object), callback_assurance_window, transfer_fail_notification_action.

---

### 3. `agent_prompt_template.md`
The voice agent conversation script. Key design decisions:

**Rendering method documented at top.** The template uses `{{#if}}` and `{{#each}}` (Handlebars-style syntax). Current decision: the LLM in `agent_spec_generator_prompt` renders these blocks. If you switch to code-side rendering (Jinja2, handlebars npm), update the template syntax accordingly.

**Variable Reference Table is now complete.** All 21 variables used in the template have a row with a source field. Previously `{{TRANSFER_FAIL_NOTIFICATION_ACTION}}` and `{{CALLBACK_ASSURANCE_WINDOW}}` had no mappings — now added.

**`EMERGENCY_PRIMARY_*` variables derive from `escalation_chain[order=1]`.** Documented in the table. No separate `primary_contact` field.

**`CALLBACK_ASSURANCE_WINDOW` has no default.** Renders as `[CONFIRM WITH CLIENT — callback time]` if null. Not "15 minutes".

**`tone_label` usage clarified.** Documented in the spec generator prompt: `tone_label` affects writing style of the system_prompt, not any Retell API parameter.

---

### 4. `agent_spec_generator_prompt.md`
The second LLM call that converts a filled memo → rendered agent spec. Key design decisions:

**Two-stage LLM architecture.** Stage 1: extract structured data from messy transcript. Stage 2: generate polished agent spec from clean structured data. Combining these into one call reduces reliability.

**`[CONFIRM WITH CLIENT]` placeholders.** When a variable is null, the rendered prompt gets a visible placeholder. Makes incomplete configs obvious before deployment.

**`account_id` generation logic documented.** The prompt now includes a note (for the pipeline engineer, not the LLM) explaining that account_ids are assigned by the pipeline script using an atomic counter in `state.json`. Batch processing: generate all IDs upfront before any LLM calls.

**`tone_label` usage documented.** Instructs the spec generator to use `tone_label` for writing style only, not as a Retell API parameter.

**Handlebars rendering rules documented.** Explicit instructions for how to resolve `{{#if}}` and `{{#each}}` blocks during rendering.

**`CALLBACK_ASSURANCE_WINDOW` rendering rule.** Never default. If null → `[CONFIRM WITH CLIENT — callback window]`.

**`TRANSFER_FAIL_NOTIFICATION_ACTION` rendering rule.** Silent system action — instructed as an internal agent action, never spoken aloud.

---

### 5. `retell_agent_spec.schema.json`
**Fully rebuilt as a real JSON Schema.** Previously the file contained only a top-level `"example"` key with no `properties`, no `required` array, and no validation rules. The schema now has:
- Full `properties` definitions for all fields
- `required` array with 13 mandatory fields
- Enum on `tool_placeholders[].tool_name` to prevent hallucinated tool names
- `minItems: 1` on `tool_placeholders` and `call_transfer_protocol.emergency_transfer_chain`
- Version pattern `^v[0-9]+$` consistent with account memo schema

---

## Rendering Decision Log

| Decision | Choice | Rationale |
|---|---|---|
| Template syntax | Handlebars-style (`{{#if}}`, `{{#each}}`) | Readable, widely understood |
| Renderer | LLM (agent_spec_generator_prompt) for Phase 1-2 | Zero-dependency for initial build |
| Phase 3+ option | Python Jinja2 or handlebars npm | More deterministic, faster, no LLM call needed for rendering |
| version enum → pattern | `^v[0-9]+$` | Removes v4+ cap |
| primary_contact | Removed — use escalation_chain[order=1] | Eliminates dual-update inconsistency |
| confidence scoring | Pipeline script computes from 8 boolean fields | Eliminates LLM self-assessment variance |
| callback_assurance_window | Required field, no default. Rule mirrored in both extraction_prompt (Rule 6) and spec generator (Rule 6) | High-stakes caller promise — must be client-confirmed. Mirrored to prevent drift if either file is edited in isolation. |
| holidays_observed | Free text, known limitation | Structured list deferred to Phase 3+ |
| timezone format | IANA pattern enforced in schema + Rule 9 in extraction_prompt | Display names like "Eastern Time" fail the `^(Africa\|America\|...)` pattern — catches LLM format errors before data is stored |
| timezone null at runtime | Agent template includes TIMEZONE NULL GUARD block | If timezone is null, agent defaults to after-hours routing rather than silently misrouting callers |
| account_id mismatch | Rule 8 in extraction_prompt returns a structured error object | Prevents onboarding data from patching the wrong client's memo |
| onboarding_form stage | Rule 4 updated, same patch logic as onboarding_call | Form fields not present = not mentioned; prior memo fields are preserved |
| system_prompt unresolved variables | `not: { pattern }` constraint in retell spec schema | Schema validation rejects any prompt that still contains `{{VARIABLE}}` tokens after rendering |
| go-live gate | Documented in retell spec schema description + enforced by pipeline script | Schema cannot block v1 drafts (which legitimately have open items); pipeline script enforces at deployment-check step |

---

## Go-Live Gate

A spec is NOT deployment-ready if `questions_carried_forward` is non-empty. The schema documents this rule; the pipeline script enforces it mechanically.

**Pipeline script must implement this check (Phase 2 responsibility):**
```python
def is_deployment_ready(spec: dict) -> bool:
    """Returns True only if the spec has no unresolved fields."""
    unresolved = spec.get("questions_carried_forward", [])
    if unresolved:
        print(f"[BLOCKED] {spec['account_id']} {spec['version']} has {len(unresolved)} unresolved field(s):")
        for item in unresolved:
            print(f"  - {item}")
        return False
    return True
```

This check runs after every spec generation. A spec that fails this check is written to `/outputs/accounts/{id}/{version}/` with a `.draft` suffix and is never passed to Retell.

---



```
Transcript (raw text)
    ↓
[extraction_prompt.md + LLM]
    ↓
account_memo.json (v1) — validated against account_memo.schema.json
    ↓
[pipeline script computes extraction_confidence from confidence_score_breakdown]
    ↓
[agent_spec_generator_prompt.md + LLM]
    ↓
retell_agent_spec.json (v1) — validated against retell_agent_spec.schema.json
    ↓
[store in /outputs/accounts/{id}/v1/]
    ↓
[onboarding transcript arrives]
    ↓
[extraction_prompt.md + prior memo + LLM — list merge rules applied]
    ↓
account_memo.json (v2) + changelog entry
    ↓
[pipeline script recomputes extraction_confidence]
    ↓
[agent_spec_generator_prompt.md + LLM]
    ↓
retell_agent_spec.json (v2)
    ↓
[store in /outputs/accounts/{id}/v2/]
```

---

## account_id Generation (Pipeline Script Responsibility)

```
/outputs/state.json
{
  "last_account_number": 3,
  "last_updated": "2025-01-15T14:00:00Z"
}
```

Logic:
1. Read state.json
2. Increment `last_account_number`
3. Write back atomically (use file lock or SQLite for concurrent safety)
4. Construct ID: `CLARA-{current_year}-{zero_padded_3_digits}`

For batch processing: generate all 5 account_ids before spawning any LLM calls.

---

## Known Limitations (for README)

- `holidays_observed` is free text — agent cannot programmatically skip holidays. Structured holiday list deferred to Phase 3+.
- Multi-location routing is stub only — single routing tree per account in v1/v2
- Retell voice IDs may change — `eleven_labs_rachel` should be verified against current Retell docs before go-live
- `max_call_duration_ms` is hardcoded at 600000 (10 min) — may need tuning per client
- Timezone null causes after-hours fallback (TIMEZONE NULL GUARD) — expected behavior, not a silent failure. Agent cannot offer accurate office-hours routing until timezone is confirmed on the onboarding call.
- Go-live gate is enforced by pipeline script (Phase 2), not JSON Schema alone — schema blocks unrendered `{{}}` variables, but `questions_carried_forward` emptiness is a runtime check only.

---

## What's Next (Hours 3-4)

- [ ] Build the Python pipeline script that chains these prompts together
- [ ] Implement account_id counter in state.json (with atomic write + file lock for concurrent safety)
- [ ] Implement programmatic confidence scoring from confidence_score_breakdown
- [ ] Implement JSON schema validation step (jsonschema library) — catches IANA timezone violations and unrendered {{}} variables automatically
- [ ] Implement go-live gate check: `is_deployment_ready()` — blocks specs with non-empty `questions_carried_forward` from reaching Retell
- [ ] Implement account_id mismatch guard at pipeline ingestion level (double-enforces Rule 8 in extraction_prompt)
- [ ] Wire to a free LLM (Gemini free tier, Groq free tier, or local Ollama)
- [ ] Set up file I/O: read transcripts → write JSON outputs
- [ ] Test on sample transcript data
- [ ] Build n8n workflow JSON that orchestrates the pipeline
