#!/usr/bin/env python3
"""
CLARA PIPELINE — Shared Utilities
Phase 2 | Version: 1.1

Centralises helpers used by script1, script2, and script3 so logic is
defined once and tested once.  Import like:

    from utils import call_ollama, extract_json_from_response, check_ollama_health
"""

import json
import re
import time
import urllib.request
import urllib.error

# ── DEFAULTS (overridden at call site via keyword args) ───────────────────────
DEFAULT_OLLAMA_URL   = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "mistral"


# ── OLLAMA ────────────────────────────────────────────────────────────────────

def call_ollama(
    system_prompt: str,
    user_prompt: str,
    *,
    ollama_url: str   = DEFAULT_OLLAMA_URL,
    ollama_model: str = DEFAULT_OLLAMA_MODEL,
    temperature: float = 0.1,
    max_retries: int   = 3,
) -> str:
    """
    Call the local Ollama /api/chat endpoint.

    Args:
        system_prompt: LLM system instruction text.
        user_prompt:   LLM user message text.
        ollama_url:    Base URL of the Ollama server.
        ollama_model:  Model name to use.
        temperature:   Sampling temperature (0.1 for extraction, 0.2 for spec gen).
        max_retries:   Number of retry attempts on network errors.

    Returns:
        Raw text content of the assistant's reply.

    Raises:
        RuntimeError: If Ollama is unreachable after all retries.
    """
    payload = {
        "model": ollama_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": 16384,
            "top_p": 0.9,
        },
    }
    url  = f"{ollama_url}/api/chat"
    data = json.dumps(payload).encode("utf-8")

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["message"]["content"]

        except urllib.error.URLError as exc:
            print(f"  [WARN] Ollama attempt {attempt}/{max_retries} failed: {exc}")
            if attempt < max_retries:
                time.sleep(5 * attempt)
            else:
                raise RuntimeError(
                    f"Ollama unreachable at {ollama_url} after {max_retries} attempts. "
                    "Is it running?  Try: ollama serve"
                ) from exc

        except KeyError as exc:
            raise RuntimeError(
                f"Unexpected Ollama response format — missing key: {exc}"
            ) from exc


def check_ollama_health(
    *,
    ollama_url:   str = DEFAULT_OLLAMA_URL,
    ollama_model: str = DEFAULT_OLLAMA_MODEL,
) -> "tuple[bool, str]":
    """
    Return (healthy: bool, resolved_model: str).

    resolved_model is the model that should actually be used:
    - If the requested model is available, resolved_model == ollama_model.
    - If the requested model is missing but another model exists, resolved_model
      is set to that first available model AND a clear warning is printed so the
      operator knows a substitution occurred.
    - If no models are available at all, returns (False, ollama_model).

    Previously this function returned plain bool and silently returned True even
    when the requested model was absent, causing downstream call_ollama() to use
    the wrong (missing) model.  The fix: callers must use the returned
    resolved_model when calling call_ollama().
    """
    try:
        req = urllib.request.Request(f"{ollama_url}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data   = json.loads(resp.read())
            models = [m["name"].split(":")[0] for m in data.get("models", [])]

            if ollama_model not in models:
                print(f"[WARN] Model '{ollama_model}' not found in Ollama.")
                print(f"       Available: {models}")
                print(f"       Pull it:   ollama pull {ollama_model}")
                if models:
                    resolved = models[0]
                    print(
                        f"[WARN] Substituting '{ollama_model}' → '{resolved}'. "
                        "All LLM calls will use this model instead."
                    )
                    return True, resolved
                # No models at all — cannot proceed
                print("[ERROR] No models available in Ollama. Pull a model first.")
                return False, ollama_model

            return True, ollama_model

    except Exception as exc:
        print(f"[ERROR] Cannot connect to Ollama at {ollama_url}: {exc}")
        print("        Start Ollama: ollama serve")
        return False, ollama_model


# ── JSON PARSING ──────────────────────────────────────────────────────────────

def extract_json_from_response(raw: str) -> dict:
    """
    Parse a JSON object from an LLM response that may be wrapped in markdown
    code fences or contain leading/trailing prose.

    Attempts (in order):
      1. Direct json.loads on the stripped text.
      2. Strip ``` fences then parse.
      3. Find the outermost { … } block and parse that.

    Raises:
        ValueError: If no valid JSON object can be extracted.
    """
    raw = raw.strip()

    # 1. Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown code fences
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$",          "", cleaned, flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. Outermost { … }
    start = raw.find("{")
    end   = raw.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Could not parse JSON from LLM response.\n"
                f"Error: {exc}\n"
                f"Raw (first 500 chars): {raw[:500]}"
            ) from exc

    raise ValueError(
        f"No JSON object found in LLM response:\n{raw[:500]}"
    )


# ── FILE SIZE GUARD ───────────────────────────────────────────────────────────

MAX_TRANSCRIPT_BYTES = 10 * 1024 * 1024  # 10 MB — sanity guard before loading

def check_file_size(path: str) -> None:
    """
    Raise ValueError if the file is unreasonably large (likely not a text
    transcript).  Call this before read_text() to avoid loading huge files.
    """
    from pathlib import Path
    size = Path(path).stat().st_size
    if size > MAX_TRANSCRIPT_BYTES:
        raise ValueError(
            f"File is {size / 1_048_576:.1f} MB — too large to be a transcript. "
            f"Maximum allowed: {MAX_TRANSCRIPT_BYTES // 1_048_576} MB.  "
            f"Path: {path}"
        )


# ── TRANSCRIPT LOADING ────────────────────────────────────────────────────────

MAX_PROMPT_CHARS = 32_000   # chars sent to LLM — raised from 12_000 (R-02 fix)


def load_transcript(path: str) -> str:
    """
    Load and return the plain-text content of a transcript file.
    Supports .txt, .md, and .docx.

    Raises:
        FileNotFoundError: Path does not exist.
        ValueError:        File is too large or format is unsupported.
        ImportError:       python-docx is required for .docx but not installed.
    """
    from pathlib import Path
    p = Path(path)

    if not p.exists():
        raise FileNotFoundError(f"Transcript not found: {path}")

    check_file_size(path)

    suffix = p.suffix.lower()

    if suffix in (".txt", ".md"):
        return p.read_text(encoding="utf-8")

    if suffix == ".docx":
        try:
            from docx import Document
        except ImportError:
            raise ImportError(
                "python-docx is required for .docx transcripts. "
                "Install it: pip install python-docx"
            )
        doc   = Document(str(p))
        lines = [para.text for para in doc.paragraphs if para.text.strip()]
        return "\n".join(lines)

    # Last-resort: try reading as UTF-8 text
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        raise ValueError(
            f"Unsupported transcript format: '{suffix}'. "
            "Supported formats: .txt, .md, .docx"
        )


def truncate_transcript(text: str, max_chars: int = MAX_PROMPT_CHARS) -> str:
    """
    Truncate transcript to max_chars for the LLM prompt.
    Logs a WARNING if truncation occurs so operators know data was cut.
    """
    if len(text) <= max_chars:
        return text
    print(
        f"  [WARN] Transcript truncated: {len(text):,} chars → {max_chars:,} chars. "
        "Content beyond this point will NOT be extracted. "
        "Consider splitting long transcripts or raising MAX_PROMPT_CHARS in utils.py."
    )
    return text[:max_chars]


# ── IANA TIMEZONE HELPERS ─────────────────────────────────────────────────────

IANA_ZONES: dict[str, str] = {
    "eastern":  "America/New_York",
    "et":       "America/New_York",
    "est":      "America/New_York",
    "edt":      "America/New_York",
    "central":  "America/Chicago",
    "ct":       "America/Chicago",
    "cst":      "America/Chicago",
    "cdt":      "America/Chicago",
    "mountain": "America/Denver",
    "mt":       "America/Denver",
    "mst":      "America/Denver",
    "mdt":      "America/Denver",
    "pacific":  "America/Los_Angeles",
    "pt":       "America/Los_Angeles",
    "pst":      "America/Los_Angeles",
    "pdt":      "America/Los_Angeles",
}

IANA_DISPLAY: dict[str, str] = {
    "America/New_York":   "Eastern Time",
    "America/Chicago":    "Central Time",
    "America/Denver":     "Mountain Time",
    "America/Los_Angeles":"Pacific Time",
    "America/Phoenix":    "Mountain Standard Time (no DST)",
    "America/Anchorage":  "Alaska Time",
    "Pacific/Honolulu":   "Hawaii Time",
    "America/Toronto":    "Eastern Time",
    "America/Vancouver":  "Pacific Time",
}

IANA_PATTERN = re.compile(
    r"^(Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|"
    r"Europe|Indian|Pacific|Etc)/[A-Za-z0-9_/]+$"
)
