"""First-run EchoVault setup for OpenRouter. Uses only the Python standard library."""
from __future__ import annotations

import getpass
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
EXAMPLE_FILE = ROOT / ".env.example"
MODEL_DEFAULT = "google/gemma-4-26b-a4b-it:free"


def parse_env(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def write_value(lines: list[str], key: str, value: str) -> list[str]:
    prefix = f"{key}="
    replaced = False
    result: list[str] = []
    for line in lines:
        if line.strip().startswith(prefix):
            result.append(f"{key}={value}")
            replaced = True
        else:
            result.append(line)
    if not replaced:
        if result and result[-1].strip():
            result.append("")
        result.append(f"{key}={value}")
    return result


def main() -> int:
    if not ENV_FILE.exists():
        if not EXAMPLE_FILE.exists():
            print("ERROR: .env.example is missing from the project folder.", file=sys.stderr)
            return 1
        shutil.copyfile(EXAMPLE_FILE, ENV_FILE)

    current_text = ENV_FILE.read_text(encoding="utf-8")
    current = parse_env(current_text)
    lines = current_text.splitlines()
    existing_key = current.get("OPENROUTER_API_KEY", "")

    lines = write_value(lines, "AI_PROVIDER", "openrouter")
    if not current.get("OPENROUTER_MODEL"):
        lines = write_value(lines, "OPENROUTER_MODEL", MODEL_DEFAULT)

    key_is_saved = bool(existing_key) and not existing_key.lower().startswith(("your_", "replace_", "paste_"))
    replace_requested = "--replace-key" in sys.argv
    if key_is_saved and not replace_requested:
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("OpenRouter key already exists; preserving it without displaying it.")
        print("AI_PROVIDER is set to openrouter. Use --replace-key if you need to change the key.")
        print("Start EchoVault and click 'Test AI' to verify the key and model with a real request.")
        return 0

    print("Create/copy a key at https://openrouter.ai/keys")
    if replace_requested and key_is_saved:
        print("Replacing the existing key; the previous key will not be displayed.")
    print("Your key is hidden while typing and will be saved only in this project's .env file.")
    new_key = getpass.getpass("OpenRouter API key (press Enter to leave it blank): ").strip()
    if new_key:
        if not new_key.startswith("sk-or-"):
            print("WARNING: This key does not start with 'sk-or-'. Check that you copied an OpenRouter key.")
        lines = write_value(lines, "OPENROUTER_API_KEY", new_key)
    else:
        lines = write_value(lines, "OPENROUTER_API_KEY", existing_key)

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    saved = bool(new_key or existing_key) and not (new_key or existing_key).lower().startswith(("your_", "replace_", "paste_"))
    print()
    print("Configuration saved to .env. The API key is not displayed.")
    print("AI_PROVIDER is set to openrouter.")
    print(f"Model: {current.get('OPENROUTER_MODEL') or MODEL_DEFAULT}")
    if saved:
        print("Next: start EchoVault, then click 'Test AI' in the top bar.")
    else:
        print("No API key was saved. Re-run 'py configure_openrouter.py' when you have one.")
    print("Do not upload .env to GitHub; it is excluded by .gitignore.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
