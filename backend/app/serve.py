"""Single-process launcher for containers and built local installations."""

import os

import uvicorn


def main():
    port = int(os.environ.get("PORT", "8000"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    uvicorn.run(
        "backend.app.main:app", host="0.0.0.0", port=port, workers=1, proxy_headers=False, access_log=False
    )


if __name__ == "__main__":
    main()
