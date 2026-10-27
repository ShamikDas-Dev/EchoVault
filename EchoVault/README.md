# EchoVault — OpenRouter Edition

**Preserve real stories. Keep the archive local. Use OpenRouter for AI summaries.**

EchoVault is a local-first memory archive for the Hacktoberfest Open-Source AI Challenge Week 4: *Ghost in the Machine*. This edition is configured for **OpenRouter by default** and includes a real connection test so the UI cannot quietly pretend a local fallback is an OpenRouter response.

## What this version does

- Import audio (`WAV`, `MP3`, `M4A`, `OGG`, `FLAC`, `WEBM`, and other formats supported by Whisper) and transcribe it locally with `faster-whisper`.
- Paste or write a transcript if audio transcription is unavailable.
- Send transcript text to a model through the OpenRouter Chat Completions API to create a grounded summary and extract people, places, dates, themes, and exact quotations.
- Use the default model `google/gemma-4-26b-a4b-it:free`, which is listed on OpenRouter as a free Gemma endpoint. Free endpoints are rate-limited and may sometimes be unavailable. You can change the model in `.env`.
- Test the actual API key/model connection using the **Test AI** button in the top bar or `POST /api/ai/test`.
- Keep memory records in SQLite and audio under `data/audio/`, on this machine.
- Search the archive, edit transcripts, play original audio, export Markdown, and delete memories.
- Use clear error messages: if OpenRouter is selected but its request fails, EchoVault shows the error and does **not** save a fake local fallback as if it were an AI-generated summary.
- Switch to local Ollama by setting `AI_PROVIDER=ollama` in `.env` if you prefer offline summarization.

## Important privacy detail

EchoVault stores audio recordings, transcripts, and archive records locally. In the default OpenRouter mode, **transcript text is sent to OpenRouter and the selected model provider for summarization**. The original audio is not sent by the summarization function; speech-to-text runs locally. Do not submit sensitive recordings unless you are comfortable with the provider's data-handling terms.

This is a local-first application, not an encrypted vault. The database and audio files are ordinary local files. Use full-disk encryption and protect your Windows account when archiving sensitive stories.

## Windows quick start

### 1. Install Python

Install Python 3.11 or newer and ensure the Python launcher (`py`) works in PowerShell.

### 2. Extract the ZIP

Extract this folder somewhere writable, for example:

```text
D:\EchoVault_OpenRouter\
```

### 3. Configure OpenRouter

Open PowerShell inside the extracted folder and run:

```powershell
py configure_openrouter.py
```

Create an API key at [openrouter.ai/keys](https://openrouter.ai/keys). The configuration script prompts for the key with hidden input and saves it to `.env` in this folder. The API key is not printed to the terminal. If you need to replace a saved key later, run:

```powershell
py configure_openrouter.py --replace-key
```

If you leave the key blank, the project still starts, but the **Test AI** button and memory creation will explain that the key is missing. Never upload `.env` to GitHub; it is excluded in `.gitignore`.

### 4. Install dependencies

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The initial install needs internet access. `faster-whisper` also downloads its speech model the first time you transcribe audio.

### 5. Run EchoVault

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

If you use `run_windows.bat`, it installs dependencies and requests OpenRouter configuration on first launch. Some Windows installations may flag downloaded script files; in that case use the manual PowerShell commands above rather than disabling Windows security features.

### 6. Verify OpenRouter

Click **Test AI** in the top bar. A successful test runs a real short request and reports the selected model. You can also inspect:

- `GET http://127.0.0.1:8000/api/health` — provider selection and configuration status.
- `POST http://127.0.0.1:8000/api/ai/test` — actual test request. In PowerShell:

  ```powershell
  Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/ai/test
  ```

The health endpoint only reports whether an API key is configured; it cannot prove that the key, account, model slug, or network are working. Use **Test AI** for that.

## Configure the model/provider

The `.env` file is created from `.env.example` on first startup if it does not exist. Its important settings are:

```dotenv
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-your-real-key-here
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=google/gemma-4-26b-a4b-it:free
OPENROUTER_SITE_URL=http://localhost:8000
OPENROUTER_APP_NAME=EchoVault
```

The placeholder key above is illustrative only. Use the configuration script to set the real key without displaying it.

To use another OpenRouter model, change `OPENROUTER_MODEL` to the exact model ID from the [OpenRouter model catalog](https://openrouter.ai/models). Free models may be rate-limited; changing to a paid model may incur charges. Check the pricing shown in the catalog before using a paid slug.

To switch to local Ollama instead:

```dotenv
AI_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gemma3:4b
```

Then restart EchoVault. This build intentionally lets `.env` override stale `AI_PROVIDER` variables inherited from an older terminal session. If you change `.env`, restart the server so Python reloads the configuration.

## Storage

```text
data/
├── echovault.db       # memory records and transcripts
├── audio/             # uploaded recordings
└── models/whisper/    # local faster-whisper cache
```

The app creates these folders automatically. Set `ECHOVAULT_DATA_DIR` in `.env` to choose a different storage path. The archive is excluded from the ZIP and from Git.

## API overview

- `GET /api/health` — configuration and local component status.
- `POST /api/ai/test` — perform a real request to the active AI provider without saving a memory.
- `GET /api/memories?q=...` — list and search memories.
- `POST /api/transcribe` — transcribe audio locally.
- `POST /api/memories` — create a memory and generate its AI summary.
- `GET /api/memories/{id}` — fetch one memory.
- `PATCH /api/memories/{id}` — edit the title/transcript and refresh extracted details.
- `GET /api/memories/{id}/audio` — stream original audio.
- `DELETE /api/memories/{id}` — delete the memory and its attached audio.

Interactive API docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## Architecture and open innovation

- `faster-whisper` does speech recognition locally, which keeps original audio on the machine and supports later offline transcription after the model is cached.
- OpenRouter exposes an OpenAI-compatible API that allows the summarization model to be changed through configuration instead of hard-wiring one provider SDK into the application.
- SQLite and local files make the archive inspectable, portable, and independent of a hosted database.
- The original transcript is retained next to its summary, so users can verify names, dates, and quotations.
- OpenRouter failures are surfaced to the user in OpenRouter mode. The application will not silently claim a deterministic fallback was generated by the remote model.

## Tests

With dependencies installed, run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The automated tests mock OpenRouter responses; use **Test AI** in the app to verify your real key, network, and chosen model.

## License

Application code is released under the MIT License; see `LICENSE`. Model weights, OpenRouter, and third-party dependencies retain their own licensing and service terms.
