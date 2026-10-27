from __future__ import annotations

import importlib.util
import json
import logging
import mimetypes
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"
# Create a first-run configuration file from the safe template. The API key is always blank
# in the distributed template and must be supplied by the user.
if not ENV_FILE.exists():
    example_file = PROJECT_ROOT / ".env.example"
    if example_file.exists():
        ENV_FILE.write_text(example_file.read_text(encoding="utf-8"), encoding="utf-8")
# The project-local .env is the source of truth for this standalone app. This deliberately
# overrides stale AI_PROVIDER values inherited from a different Windows terminal/session.
load_dotenv(ENV_FILE, override=True)
DATA_DIR = Path(os.getenv("ECHOVAULT_DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser().resolve()
AUDIO_DIR = DATA_DIR / "audio"
WHISPER_CACHE_DIR = DATA_DIR / "models" / "whisper"
DB_PATH = DATA_DIR / "echovault.db"
MAX_UPLOAD_MB = int(os.getenv("ECHOVAULT_MAX_UPLOAD_MB", "100"))
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm", ".mp4", ".mpeg", ".mpga", ".wma", ".aac"}
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")
AI_PROVIDER = os.getenv("AI_PROVIDER", "openrouter").strip().lower()
if AI_PROVIDER not in {"ollama", "openrouter"}:
    # Never silently change to local Ollama because of a typo in the config.
    AI_PROVIDER = "openrouter"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
# Verified in OpenRouter's current model catalog; free endpoints can still be rate-limited.
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "google/gemma-4-26b-a4b-it:free").strip()
# Keep this list to free-only model IDs unless you explicitly intend to incur charges.
# OpenRouter tries these models in order if the preceding model is unavailable or rate-limited.
OPENROUTER_FALLBACK_MODELS = [
    item.strip()
    for item in os.getenv(
        "OPENROUTER_FALLBACK_MODELS",
        "google/gemma-4-31b-it:free,openrouter/free",
    ).split(",")
    if item.strip()
]
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "http://localhost:8000").strip()
OPENROUTER_APP_NAME = os.getenv("OPENROUTER_APP_NAME", "EchoVault").strip()
WHISPER_MODEL_NAME = os.getenv("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8" if WHISPER_DEVICE == "cpu" else "float16")

for directory in (DATA_DIR, AUDIO_DIR, WHISPER_CACHE_DIR):
    directory.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="EchoVault",
    description="A local-first archive for recorded stories and memories.",
    version="1.0.0",
)
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "app" / "static")), name="static")
logger = logging.getLogger("echovault.ai")


class AIProviderError(RuntimeError):
    """An AI provider was selected but its request could not be completed."""


_whisper_model: Any | None = None
_whisper_lock = threading.Lock()


class MemoryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    transcript: str = Field(min_length=5, max_length=500_000)
    audio_file_id: str | None = None
    source_filename: str | None = Field(default=None, max_length=255)


class MemoryUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    transcript: str | None = Field(default=None, min_length=5, max_length=500_000)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                transcript TEXT NOT NULL,
                audio_file_id TEXT,
                source_filename TEXT,
                people_json TEXT NOT NULL DEFAULT '[]',
                places_json TEXT NOT NULL DEFAULT '[]',
                dates_json TEXT NOT NULL DEFAULT '[]',
                themes_json TEXT NOT NULL DEFAULT '[]',
                quotes_json TEXT NOT NULL DEFAULT '[]',
                summary_source TEXT NOT NULL DEFAULT 'local-extractive',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at DESC)")


init_db()


def _json_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:240] for item in value if isinstance(item, (str, int, float)) and str(item).strip()][:20]


def memory_from_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for field in ("people", "places", "dates", "themes", "quotes"):
        item[field] = json.loads(item.pop(f"{field}_json", "[]") or "[]")
    return item


def _local_summary(transcript: str) -> str:
    """A clearly labelled, deterministic fallback when the local LLM is unavailable."""
    text = re.sub(r"\s+", " ", transcript).strip()
    if not text:
        return "No transcript text was provided."
    sentences = re.split(r"(?<=[.!?])\s+", text)
    picked = " ".join(sentences[:3]).strip()
    if len(picked) > 420:
        picked = picked[:417].rsplit(" ", 1)[0] + "…"
    if len(picked) < 80 and len(text) > len(picked):
        picked = text[:417].rsplit(" ", 1)[0] + ("…" if len(text) > 417 else "")
    return picked


def ollama_info() -> dict[str, Any]:
    try:
        with httpx.Client(timeout=1.6, trust_env=False) as client:
            response = client.get(f"{OLLAMA_BASE_URL}/api/tags")
            response.raise_for_status()
            payload = response.json()
        models = payload.get("models", []) if isinstance(payload, dict) else []
        names = [str(item.get("name", "")) for item in models if isinstance(item, dict)]
        installed = OLLAMA_MODEL in names or f"{OLLAMA_MODEL}:latest" in names
        return {"available": True, "model_installed": installed, "model": OLLAMA_MODEL, "models": names}
    except Exception:
        return {"available": False, "model_installed": False, "model": OLLAMA_MODEL, "models": []}


def _extract_json(response_text: str) -> dict[str, Any]:
    response_text = response_text.strip()
    response_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", response_text, flags=re.IGNORECASE)
    try:
        parsed = json.loads(response_text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start, end = response_text.find("{"), response_text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(response_text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    raise ValueError("The model response did not contain valid JSON.")


def _generation_prompt(transcript: str) -> str:
    return f'''You are a careful archivist helping preserve a person's story. Analyze only the transcript below.
Never invent names, events, relationships, locations, dates, or quotations. If a field is not clearly present, return an empty array. Keep the summary grounded in what was actually said. Do not impersonate or speak as the person in the recording.
Return ONLY valid JSON with exactly these keys:
{{"summary":"2-4 sentence factual summary","people":["names explicitly spoken"],"places":["places explicitly spoken"],"dates":["dates/time references explicitly spoken"],"themes":["short themes grounded in the transcript"],"quotes":["short exact quotations copied verbatim from the transcript"]}}

TRANSCRIPT BEGIN
{transcript[:24000]}
TRANSCRIPT END'''


def _fields_from_model_response(response_text: str, model_source: str) -> dict[str, Any]:
    parsed = _extract_json(response_text)
    summary = str(parsed.get("summary", "")).strip()
    if not summary:
        raise ValueError("The model returned no summary.")
    return {
        "summary": summary[:1800],
        "people": _json_list(parsed.get("people")),
        "places": _json_list(parsed.get("places")),
        "dates": _json_list(parsed.get("dates")),
        "themes": _json_list(parsed.get("themes")),
        "quotes": _json_list(parsed.get("quotes")),
        "summary_source": model_source,
    }


def _generate_with_ollama(prompt: str) -> dict[str, Any]:
    # trust_env=False prevents HTTP proxy environment variables redirecting local traffic.
    with httpx.Client(timeout=httpx.Timeout(180.0, connect=2.0), trust_env=False) as client:
        response = client.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.15, "num_ctx": 8192},
            },
        )
        response.raise_for_status()
        payload = response.json()
    return _fields_from_model_response(str(payload.get("response", "")), OLLAMA_MODEL)


def _openrouter_error_message(exc: Exception) -> str:
    """Convert common OpenRouter failures into actionable, key-safe diagnostics."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        provider_message = ""
        try:
            payload = exc.response.json()
            error_obj = payload.get("error", {}) if isinstance(payload, dict) else {}
            if isinstance(error_obj, dict):
                provider_message = str(error_obj.get("message", "")).strip()
        except Exception:
            pass
        provider_message = provider_message[:220]
        if status in (401, 403):
            return "OpenRouter rejected the API key or model access (HTTP %s). Recheck OPENROUTER_API_KEY at https://openrouter.ai/keys and confirm the selected model is accessible." % status
        if status == 402:
            return "OpenRouter reports insufficient credits or account balance (HTTP 402). Check your OpenRouter account and use a model marked free if you do not want paid inference."
        if status == 404:
            return f"OpenRouter could not find the configured model (HTTP 404). Check OPENROUTER_MODEL. Current setting: {OPENROUTER_MODEL}"
        if status == 429:
            metadata = error_obj.get("metadata", {}) if isinstance(error_obj, dict) else {}
            provider_code = str(metadata.get("provider_code", "")).strip() if isinstance(metadata, dict) else ""
            retry_after = exc.response.headers.get("Retry-After", "").strip()
            limit_remaining = exc.response.headers.get("X-RateLimit-Remaining", "").strip()
            details = []
            if provider_message:
                details.append(provider_message)
            if provider_code:
                details.append(f"provider_code={provider_code}")
            if retry_after:
                details.append(f"Retry-After={retry_after}s")
            if limit_remaining:
                details.append(f"X-RateLimit-Remaining={limit_remaining}")
            suffix = " Details: " + "; ".join(details) if details else ""
            return (
                "OpenRouter returned HTTP 429. This can be a platform per-minute limit or an upstream model-provider limit. "
                "EchoVault now supports model fallbacks; if all configured free models are rate-limited, wait and retry."
                + suffix
            )
        if status >= 500:
            return f"OpenRouter or the selected model provider returned HTTP {status}. Retry shortly. {provider_message}".strip()
        return f"OpenRouter returned HTTP {status}. {provider_message or 'Check the API request and model configuration.'}".strip()
    if isinstance(exc, httpx.TimeoutException):
        return "The OpenRouter request timed out. Check your internet connection and retry; the free endpoint may be busy."
    if isinstance(exc, httpx.RequestError):
        return "Could not reach OpenRouter. Check your internet connection, proxy/firewall settings, and OPENROUTER_BASE_URL."
    if isinstance(exc, ValueError):
        return f"OpenRouter returned an unreadable model response: {str(exc)[:420]}"
    message = str(exc).strip()
    if "OPENROUTER_API_KEY" in message:
        return "OpenRouter is selected, but OPENROUTER_API_KEY is blank. Run `py configure_openrouter.py` or add the key to the project .env file."
    return f"OpenRouter request failed: {message[:220] or type(exc).__name__}"


def _openrouter_request_body(prompt: str, *, model: str | None = None) -> dict[str, Any]:
    """Build an OpenRouter request. `model=None` enables OpenRouter model-level fallback."""
    body: dict[str, Any] = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a careful archivist. Return only a JSON object, with no markdown "
                    "or commentary. Never invent facts absent from the transcript. "
                    "Use empty arrays when a field is unknown."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 2400,
        "response_format": {"type": "json_object"},
    }
    if model is None:
        candidates = list(dict.fromkeys([OPENROUTER_MODEL, *OPENROUTER_FALLBACK_MODELS]))
        body["models"] = candidates
    else:
        body["model"] = model
    return body


def _fields_from_openrouter_payload(payload: dict[str, Any], requested_model: str) -> dict[str, Any]:
    choices = payload.get("choices", []) if isinstance(payload, dict) else []
    if not choices or not isinstance(choices[0], dict):
        raise ValueError("OpenRouter returned no completion choices.")
    message = choices[0].get("message", {})
    content = message.get("content", "") if isinstance(message, dict) else ""
    if isinstance(content, list):
        content = "".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in content
        )
    if not isinstance(content, str) or not content.strip():
        model_used = str(payload.get("model") or requested_model)
        reasoning_present = bool(message.get("reasoning") or message.get("reasoning_details")) if isinstance(message, dict) else False
        detail = " The provider returned reasoning without answer text." if reasoning_present else ""
        raise ValueError(f"Model {model_used} returned an empty answer.{detail}")
    served_model = str(payload.get("model") or requested_model)
    try:
        return _fields_from_model_response(content, f"openrouter:{served_model}")
    except ValueError as exc:
        raise ValueError(f"Model {served_model} did not return usable JSON: {exc}") from exc


def _generate_with_openrouter(prompt: str) -> dict[str, Any]:
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY.lower().startswith(("your_", "replace_", "paste_")):
        raise RuntimeError("OPENROUTER_API_KEY is missing or still contains the template placeholder.")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "X-OpenRouter-Title": OPENROUTER_APP_NAME or "EchoVault",
    }
    if OPENROUTER_SITE_URL:
        headers["HTTP-Referer"] = OPENROUTER_SITE_URL

    candidates = list(dict.fromkeys([OPENROUTER_MODEL, *OPENROUTER_FALLBACK_MODELS]))
    attempted: list[str] = []
    invalid_responses: list[str] = []
    retry_errors: list[str] = []
    # First ask OpenRouter to fail over on provider/model request errors. If an upstream
    # model returns HTTP 200 but malformed/empty output, OpenRouter itself will not retry;
    # below we explicitly validate the output and try remaining model IDs.
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0), trust_env=True) as client:
        response = client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=_openrouter_request_body(prompt),
        )
        response.raise_for_status()
        payload = response.json()
        served_model = str(payload.get("model") or "") if isinstance(payload, dict) else ""
        if served_model:
            attempted.append(served_model)
        try:
            return _fields_from_openrouter_payload(payload, served_model or OPENROUTER_MODEL)
        except ValueError as exc:
            invalid_responses.append(str(exc)[:220])
            logger.warning("OpenRouter returned an unusable response from %s; trying other configured model IDs.", served_model or "unknown model")

        # A 200 response with invalid JSON is not an HTTP/provider error, so OpenRouter's
        # `models` array does not trigger. Retry remaining configured model IDs individually.
        # Skip the model that already answered to avoid wasting a free request on it again.
        for candidate in candidates:
            if candidate == served_model or candidate in attempted:
                continue
            try:
                retry_response = client.post(
                    f"{OPENROUTER_BASE_URL}/chat/completions",
                    headers=headers,
                    json=_openrouter_request_body(prompt, model=candidate),
                )
                retry_response.raise_for_status()
                retry_payload = retry_response.json()
                actual_model = str(retry_payload.get("model") or candidate) if isinstance(retry_payload, dict) else candidate
                attempted.append(candidate)
                return _fields_from_openrouter_payload(retry_payload, actual_model)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                provider_message = ""
                try:
                    err_payload = exc.response.json()
                    err_obj = err_payload.get("error", {}) if isinstance(err_payload, dict) else {}
                    if isinstance(err_obj, dict):
                        provider_message = str(err_obj.get("message", "")).strip()
                except Exception:
                    pass
                retry_errors.append(f"{candidate}: HTTP {status} {provider_message}".strip()[:220])
                attempted.append(candidate)
                # Authentication/credit errors are account-wide; other errors may be model-specific.
                if status in (401, 403, 402):
                    raise
                logger.warning("Configured OpenRouter model %s failed with HTTP %s; trying the next model.", candidate, status)
                continue
            except httpx.RequestError as exc:
                attempted.append(candidate)
                retry_errors.append(f"{candidate}: connection error ({type(exc).__name__})")
                logger.warning("Configured OpenRouter model %s could not be reached; trying the next model.", candidate)
                continue
            except (ValueError, TypeError) as exc:
                attempted.append(candidate)
                invalid_responses.append(str(exc)[:220])
                logger.warning("Configured OpenRouter model %s returned unusable output; trying the next model.", candidate)

    models_tried = ", ".join(attempted) or ", ".join(candidates)
    reasons = " | ".join(invalid_responses[:2] + retry_errors[:2])
    raise ValueError(
        f"No configured OpenRouter model returned usable JSON. Models tried: {models_tried}. "
        f"Details: {reasons or 'empty or malformed model output'}"
    )


def generate_memory_fields(transcript: str) -> dict[str, Any]:
    """Use the selected provider. Never silently fake an OpenRouter success with a local fallback."""
    prompt = _generation_prompt(transcript)
    if AI_PROVIDER == "openrouter":
        try:
            return _generate_with_openrouter(prompt)
        except Exception as exc:
            message = _openrouter_error_message(exc)
            # Log only the sanitized diagnostic, never the transcript or API key.
            logger.warning("OpenRouter summarization failed: %s", message)
            raise AIProviderError(message) from exc

    try:
        return _generate_with_ollama(prompt)
    except Exception as exc:
        logger.warning("Local Ollama summarization failed: %s", str(exc)[:220])
        return {
            "summary": _local_summary(transcript),
            "people": [],
            "places": [],
            "dates": [],
            "themes": [],
            "quotes": [],
            "summary_source": "local-extractive",
        }


def _get_whisper_model() -> Any:
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model
    if importlib.util.find_spec("faster_whisper") is None:
        raise RuntimeError("faster-whisper is not installed. Install the requirements and restart EchoVault.")
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel

            _whisper_model = WhisperModel(
                WHISPER_MODEL_NAME,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
                download_root=str(WHISPER_CACHE_DIR),
            )
    return _whisper_model


def _audio_path(file_id: str | None) -> Path | None:
    if not file_id:
        return None
    if Path(file_id).name != file_id or not re.fullmatch(r"[0-9a-f-]{36}\.[a-z0-9]{1,8}", file_id, flags=re.IGNORECASE):
        raise HTTPException(status_code=400, detail="Invalid audio file reference.")
    path = (AUDIO_DIR / file_id).resolve()
    if path.parent != AUDIO_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid audio file reference.")
    return path


def _memory_exists(memory_id: str) -> sqlite3.Row | None:
    with get_db() as conn:
        return conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()


@app.get("/", response_class=HTMLResponse)
def home() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "app" / "static" / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    ollama = ollama_info()
    whisper_installed = importlib.util.find_spec("faster_whisper") is not None
    openrouter_configured = bool(OPENROUTER_API_KEY) and not OPENROUTER_API_KEY.lower().startswith(("your_", "replace_", "paste_"))
    openrouter = {
        "configured": openrouter_configured,
        "model": OPENROUTER_MODEL,
        "fallback_models": OPENROUTER_FALLBACK_MODELS,
        "base_url": OPENROUTER_BASE_URL,
        "test_endpoint": "/api/ai/test",
    }
    selected_ai_ready = (openrouter_configured if AI_PROVIDER == "openrouter"
                         else bool(ollama.get("available") and ollama.get("model_installed")))
    if AI_PROVIDER == "openrouter":
        summary_status = (f"OpenRouter cloud inference configured for {OPENROUTER_MODEL}"
                          if openrouter_configured else "OpenRouter selected but API key is missing; run configure_openrouter.py")
        privacy_note = "Audio transcription and archive storage stay local; transcript text is sent to OpenRouter for summarization."
    else:
        summary_status = f"Local Ollama inference using {OLLAMA_MODEL}"
        privacy_note = "Summarization runs through local Ollama when available; transcript text is not sent to OpenRouter."
    return {
        "ok": True,
        "app": "EchoVault",
        "storage": "local",
        "data_directory": str(DATA_DIR),
        "transcription": {
            "package_installed": whisper_installed,
            "model": WHISPER_MODEL_NAME,
            "device": WHISPER_DEVICE,
            "ready": whisper_installed,
            "note": "The speech model may need a one-time download before offline use." if whisper_installed else "Install requirements to enable audio transcription; text-only memories still work.",
        },
        "ai_provider": AI_PROVIDER,
        "ai_ready": selected_ai_ready,
        "ai_status": "configured" if selected_ai_ready else "configuration_missing_or_provider_unavailable",
        "ollama": ollama,
        "openrouter": openrouter,
        "summaries": summary_status,
        "privacy_note": privacy_note,
    }


@app.post("/api/ai/test")
def test_ai_connection() -> dict[str, Any]:
    """Send a tiny synthetic prompt to the currently selected AI provider without saving data."""
    test_transcript = "Mira remembers planting marigolds with her grandfather near the old courtyard on a rainy afternoon."
    try:
        if AI_PROVIDER == "openrouter":
            result = _generate_with_openrouter(_generation_prompt(test_transcript))
            return {
                "ok": True,
                "provider": "openrouter",
                "model": result["summary_source"].removeprefix("openrouter:"),
                "summary_source": result["summary_source"],
                "message": "OpenRouter completed a real test request successfully.",
                "summary_preview": result["summary"][:240],
            }
        result = _generate_with_ollama(_generation_prompt(test_transcript))
        return {
            "ok": True,
            "provider": "ollama",
            "model": OLLAMA_MODEL,
            "summary_source": result["summary_source"],
            "message": "Ollama completed a real test request successfully.",
            "summary_preview": result["summary"][:240],
        }
    except Exception as exc:
        message = _openrouter_error_message(exc) if AI_PROVIDER == "openrouter" else f"Ollama test failed: {str(exc)[:220]}"
        logger.warning("AI connection test failed for provider=%s: %s", AI_PROVIDER, message)
        raise HTTPException(status_code=502, detail=message) from exc


@app.get("/api/memories")
def list_memories(q: str | None = None) -> dict[str, Any]:
    with get_db() as conn:
        if q and q.strip():
            term = f"%{q.strip()[:120]}%"
            rows = conn.execute(
                """SELECT * FROM memories
                   WHERE title LIKE ? OR summary LIKE ? OR transcript LIKE ?
                   ORDER BY created_at DESC""",
                (term, term, term),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM memories ORDER BY created_at DESC").fetchall()
    items = [memory_from_row(row) for row in rows]
    return {"items": items, "count": len(items)}


@app.get("/api/memories/{memory_id}")
def get_memory(memory_id: str) -> dict[str, Any]:
    row = _memory_exists(memory_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Memory not found.")
    return memory_from_row(row)


@app.post("/api/transcribe")
def transcribe_audio(file: UploadFile = File(...)) -> dict[str, Any]:
    original_name = Path(file.filename or "recording.wav").name
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Unsupported audio type. Try WAV, MP3, M4A, OGG, FLAC, or WEBM.")
    if importlib.util.find_spec("faster_whisper") is None:
        raise HTTPException(status_code=503, detail="Audio transcription is not installed yet. Run the setup steps in README.md, or paste a transcript instead.")
    file_id = f"{uuid.uuid4()}{extension}"
    destination = AUDIO_DIR / file_id
    try:
        content = file.file.read((MAX_UPLOAD_MB * 1024 * 1024) + 1)
        if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"Audio files must be under {MAX_UPLOAD_MB} MB.")
        if not content:
            raise HTTPException(status_code=400, detail="The selected file is empty.")
        destination.write_bytes(content)
        model = _get_whisper_model()
        segments, info = model.transcribe(str(destination), beam_size=5, vad_filter=True)
        transcript_parts = [segment.text.strip() for segment in segments if segment.text and segment.text.strip()]
        transcript = " ".join(transcript_parts).strip()
        if not transcript:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="No speech was detected. Try a clearer recording or paste a transcript manually.")
        return {
            "audio_file_id": file_id,
            "source_filename": original_name,
            "transcript": transcript,
            "language": getattr(info, "language", None),
            "language_probability": round(float(getattr(info, "language_probability", 0) or 0), 3),
            "duration_seconds": round(float(getattr(info, "duration", 0) or 0), 1),
        }
    except HTTPException:
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        message = str(exc)
        if "download" in message.lower() or "huggingface" in message.lower() or "connection" in message.lower():
            raise HTTPException(status_code=503, detail="The Whisper model may not be cached yet. Connect once to download it, then retry; subsequent transcription can run offline.") from exc
        raise HTTPException(status_code=500, detail=f"Transcription failed: {message[:220]}") from exc
    finally:
        file.file.close()


@app.post("/api/memories")
def create_memory(payload: MemoryCreate) -> dict[str, Any]:
    title = re.sub(r"\s+", " ", payload.title).strip()
    transcript = payload.transcript.strip()
    if not title:
        raise HTTPException(status_code=422, detail="A title is required.")
    if len(transcript) < 5:
        raise HTTPException(status_code=422, detail="Add a little more transcript text before saving.")
    audio_path = _audio_path(payload.audio_file_id)
    if audio_path and not audio_path.is_file():
        raise HTTPException(status_code=400, detail="The uploaded audio could not be found. Re-import it or save the transcript without audio.")
    try:
        fields = generate_memory_fields(transcript)
    except AIProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    memory_id = str(uuid.uuid4())
    now = utc_now()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO memories (
                id, title, summary, transcript, audio_file_id, source_filename,
                people_json, places_json, dates_json, themes_json, quotes_json,
                summary_source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                memory_id,
                title,
                fields["summary"],
                transcript,
                payload.audio_file_id,
                (payload.source_filename or "")[:255] or None,
                json.dumps(fields["people"], ensure_ascii=False),
                json.dumps(fields["places"], ensure_ascii=False),
                json.dumps(fields["dates"], ensure_ascii=False),
                json.dumps(fields["themes"], ensure_ascii=False),
                json.dumps(fields["quotes"], ensure_ascii=False),
                fields["summary_source"],
                now,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return memory_from_row(row)


@app.patch("/api/memories/{memory_id}")
def update_memory(memory_id: str, payload: MemoryUpdate) -> dict[str, Any]:
    row = _memory_exists(memory_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Memory not found.")
    current = memory_from_row(row)
    title = re.sub(r"\s+", " ", payload.title).strip() if payload.title is not None else current["title"]
    transcript = payload.transcript.strip() if payload.transcript is not None else current["transcript"]
    if not title:
        raise HTTPException(status_code=422, detail="A title is required.")
    if payload.transcript is not None:
        try:
            fields = generate_memory_fields(transcript)
        except AIProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    else:
        fields = {
            "summary": current["summary"], "people": current["people"], "places": current["places"],
            "dates": current["dates"], "themes": current["themes"], "quotes": current["quotes"],
            "summary_source": current["summary_source"],
        }
    now = utc_now()
    with get_db() as conn:
        conn.execute(
            """UPDATE memories SET title = ?, summary = ?, transcript = ?, people_json = ?, places_json = ?,
               dates_json = ?, themes_json = ?, quotes_json = ?, summary_source = ?, updated_at = ? WHERE id = ?""",
            (title, fields["summary"], transcript, json.dumps(fields["people"], ensure_ascii=False),
             json.dumps(fields["places"], ensure_ascii=False), json.dumps(fields["dates"], ensure_ascii=False),
             json.dumps(fields["themes"], ensure_ascii=False), json.dumps(fields["quotes"], ensure_ascii=False),
             fields["summary_source"], now, memory_id),
        )
        updated = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return memory_from_row(updated)


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: str) -> dict[str, str]:
    row = _memory_exists(memory_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Memory not found.")
    audio_file_id = row["audio_file_id"]
    with get_db() as conn:
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    if audio_file_id:
        path = _audio_path(audio_file_id)
        if path:
            path.unlink(missing_ok=True)
    return {"deleted": memory_id}


@app.delete("/api/staged-audio/{file_id}")
def delete_staged_audio(file_id: str) -> dict[str, str]:
    """Discard a transcribed upload that the user chose not to save as a memory."""
    path = _audio_path(file_id)
    if path is None:
        raise HTTPException(status_code=400, detail="Invalid audio file reference.")
    with get_db() as conn:
        attached = conn.execute("SELECT 1 FROM memories WHERE audio_file_id = ? LIMIT 1", (file_id,)).fetchone()
    if attached:
        raise HTTPException(status_code=409, detail="This recording is attached to a saved memory.")
    path.unlink(missing_ok=True)
    return {"discarded": file_id}


@app.get("/api/memories/{memory_id}/audio")
def get_memory_audio(memory_id: str) -> FileResponse:
    row = _memory_exists(memory_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Memory not found.")
    path = _audio_path(row["audio_file_id"])
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="This memory has no saved audio recording.")
    return FileResponse(path, filename=row["source_filename"] or path.name, media_type=mimetypes.guess_type(path.name)[0] or "audio/mpeg")
