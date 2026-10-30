"""
Standalone smoke test for the Nebius Token Factory API key - NOT part of the
pytest suite, just a quick manual script to confirm the key works and to see
a real response/cost before trusting it for the full app.

Usage:
    cd backend
    .venv\\Scripts\\python.exe nebius_smoke_test.py            # uses Nano (cheapest)
    .venv\\Scripts\\python.exe nebius_smoke_test.py super       # or: nano | super | ultra

Reads NEBIUS_API_KEY from backend/.env (same file the main app uses) via
python-dotenv, so you only need to set it in one place.
"""
import sys

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()  # loads backend/.env into os.environ, same as the app's config.py

from app.config import get_settings  # noqa: E402 (import after load_dotenv on purpose)

MODEL_IDS = {
    "nano": "nemotron_nano_model",
    "super": "nemotron_super_model",
    "ultra": "nemotron_ultra_model",
}

SYSTEM_PROMPT = """You are a precise assistant being smoke-tested for a
personal-AI app called Personal Nemo. Reply in at most two short sentences."""

USER_MESSAGE = "In one sentence, what is one food recommendation you'd give someone who orders ramen often?"


def main() -> None:
    settings = get_settings()
    if not settings.nebius_api_key:
        print("ERROR: NEBIUS_API_KEY is not set in backend/.env")
        sys.exit(1)

    size = sys.argv[1] if len(sys.argv) > 1 else "nano"
    if size not in MODEL_IDS:
        print(f"ERROR: unknown model size '{size}', expected one of {list(MODEL_IDS)}")
        sys.exit(1)
    model_id = getattr(settings, MODEL_IDS[size])

    client = OpenAI(
        base_url=settings.nebius_base_url,
        api_key=settings.nebius_api_key,
    )

    print(f"Calling {model_id} ({size}) at {settings.nebius_base_url} ...")
    response = client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_MESSAGE},
        ],
    )

    print(response.to_json())

    usage = response.usage
    if usage:
        print(
            f"\ntokens: input={usage.prompt_tokens} output={usage.completion_tokens} "
            f"total={usage.total_tokens}"
        )


if __name__ == "__main__":
    main()

