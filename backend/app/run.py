import os
import subprocess

import uvicorn
from dotenv import load_dotenv


def main():
    load_dotenv(override=False)
    if os.environ.get("COMMIT_SHA", "unknown") == "unknown":
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            os.environ["COMMIT_SHA"] = result.stdout.strip()
    uvicorn.run(
        "backend.app.main:app", host="127.0.0.1", port=8000, workers=1, proxy_headers=False, access_log=False
    )


if __name__ == "__main__":
    main()
