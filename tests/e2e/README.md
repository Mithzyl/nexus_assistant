# End-to-end smoke scripts (manual)

These scripts were preserved from the local scratch area (`.tmp/`) so the flows they
cover are not lost. They are **manual** checks, not part of an automated CI run: they
require a running web app, a running backend, and Playwright + a Chromium build.

## Prerequisites

- Web: `pnpm dev:web` (scripts default to `http://localhost:3200`, override with `SMOKE_BASE_URL`)
- Backend: `services/assistant-api` listening on `127.0.0.1:8001` (the Nexus stub), plus
  `services/nexus-stub` where a phase-4 flow needs it
- Python deps for the backend in `.tmp/devvenv` (phase-4 scripts call the commute worker
  through that interpreter; override the database with `SMOKE_DB`)
- `playwright` with Chromium installed for the Python that runs these scripts

## Scripts

| Script | Flow |
| --- | --- |
| `web_smoke_mobile_home.py` | Mobile web home/ask/memory/me pages, console-error check |
| `web_smoke_mobile_home_390.py` | Same at 390x844, light/dark |
| `web_smoke_mobile_home_360.py` | Same at 360x844 (narrowest supported width) |
| `phase4_assistant_smoke.py` | Commute plan -> queued check -> worker -> advisory history/expiry |
| `phase4_assistant_continue.py` | Continuation of the phase-4 flow after a manual step |

Screenshots go to `.tmp/shots` by default (git-ignored); override with `SMOKE_SHOT_DIR`.
The phase-4 scripts expect the development fixtures created by earlier manual runs
(a local test account and seeded plans); they are development-only values and must not
be reused as real credentials.
