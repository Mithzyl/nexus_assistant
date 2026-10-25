"""Export the service OpenAPI document used by the App and backend contract."""

from __future__ import annotations

import json
from pathlib import Path

from assistant_api.core.config import Settings
from assistant_api.main import create_app

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT.parents[1] / "docs" / "commute" / "openapi.json"


def main() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        allow_schema_create=False,
    )
    app = create_app(settings)
    document = app.openapi()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    paths = len(document.get("paths", {}))
    print(f"wrote {OUTPUT} ({paths} paths)")


if __name__ == "__main__":
    main()
