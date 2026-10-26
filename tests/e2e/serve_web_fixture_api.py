"""Run the real PA app against the synthetic fixture with a fixed clock.

This is a test launcher only: it does not change production code. A fixed clock
makes the commute window deterministic (the synthetic plan always has a future
arrival), so browser checks do not depend on the time of day.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import os
BACKEND_SRC = Path(
    os.environ.get("E2E_BACKEND_SRC", str(ROOT / "services" / "assistant-api" / "src"))
).resolve()
sys.path.insert(0, str(BACKEND_SRC))

from assistant_api.commute import runtime as commute_runtime  # noqa: E402

RAW_NOW = os.environ.get("E2E_NOW")
if not RAW_NOW:
    raise SystemExit("E2E_NOW is required")
FIXED = datetime.fromisoformat(RAW_NOW)
if FIXED.tzinfo is None:
    FIXED = FIXED.replace(tzinfo=UTC)


class FixedClock:
    def __call__(self) -> datetime:
        return FIXED


commute_runtime.SystemClock = FixedClock

import uvicorn  # noqa: E402
from assistant_api.main import create_app  # noqa: E402


def main() -> None:
    port = int(os.environ.get("E2E_API_PORT", "8300"))
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
