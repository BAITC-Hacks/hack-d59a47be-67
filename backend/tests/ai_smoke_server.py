"""Test-only loopback process: real AI plugin, no remote transport or runtime fallback.

Not shipped in the container; invoked only by scripts/smoke --ai on a temporary
synthetic database. Never use this process as the application entrypoint.
"""

import argparse

import uvicorn
from pydantic import SecretStr

from backend.app import ai
from backend.app.config import Settings
from backend.app.main import create_app


class SyntheticSelection:
    async def select_events(self, payload, *, instructions):
        if not instructions or not payload["eligible_candidates"]:
            raise ValueError("Synthetic smoke requires eligible candidates and instructions")
        return [payload["eligible_candidates"][0]["event_id"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    if settings.app_env != "test" or not settings.ai_enabled:
        parser.error("Only an explicitly enabled test environment is allowed")
    if not settings.database_path.parent.name.startswith("career-quest-synthetic-smoke-"):
        parser.error("Only the temporary synthetic smoke database is allowed")
    # Replace before app construction. Real provider configuration/credentials are never read.
    ai.AISettings.from_env = classmethod(
        lambda cls: ai.AISettings(api_key=SecretStr("synthetic-tcp-never-sent"))
    )
    ai.AsyncOpenAIProvider = lambda **kwargs: SyntheticSelection()
    uvicorn.run(
        create_app(settings),
        host="127.0.0.1",
        port=args.port,
        workers=1,
        proxy_headers=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
