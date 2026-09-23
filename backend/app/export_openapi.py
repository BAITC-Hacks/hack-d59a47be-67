import json
from pathlib import Path

from .main import create_app

if __name__ == "__main__":
    path = Path("contracts/openapi.json")
    path.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {path}")
