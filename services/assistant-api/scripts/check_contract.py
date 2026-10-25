"""Fail when the frontend TypeScript contract and the backend schema diverge.

Compares, in both directions:
1. every interface that also exists as an OpenAPI schema has the same field set;
2. a field the backend requires is not optional on the client;
3. every endpoint constant points at a route that actually exists.

This is what keeps `v3-kit/source/src/api/contract.ts` honest after a backend change.
Run it in CI next to the test suite:

    .venv/bin/python scripts/check_contract.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TS_FILE = ROOT.parent.parent / "v3-kit" / "source" / "src" / "api" / "contract.ts"

INTERFACE = re.compile(r"export interface (\w+)(?: extends (\w+))? \{(.*?)\n\}", re.S)
FIELD = re.compile(r"^\s{2}(\w+)(\?)?:", re.M)
ENDPOINT = re.compile(r"(\w+):\s*['\"`]([^'\"`]+)['\"`]")
ENDPOINT_FN = re.compile(r"(\w+):\s*\([^)]*\)\s*=>\s*`([^`]+)`")

# Interfaces that are client-only or shaped inline by the server, not published
# as a component schema. Listing them explicitly avoids a silent skip.
NOT_SCHEMAS = {"ApiError", "StreamEvent", "MemoryQueryResponse"}


def parse_ts(text: str) -> dict[str, tuple[set[str], set[str], str | None]]:
    """Return {interface: (fields, optional fields, parent)}."""

    parsed: dict[str, tuple[set[str], set[str], str | None]] = {}
    for name, parent, body in INTERFACE.findall(text):
        matches = FIELD.findall(body)
        names = {field for field, _ in matches}
        optional = {field for field, mark in matches if mark}
        parsed[name] = (names, optional, parent)
    return parsed


def flatten(
    name: str, parsed: dict[str, tuple[set[str], set[str], str | None]]
) -> tuple[set[str], set[str]]:
    """Merge an interface with its parents so inheritance is comparable to OpenAPI."""

    names: set[str] = set()
    optional: set[str] = set()
    current: str | None = name
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        entry = parsed.get(current)
        if entry is None:
            raise KeyError(f"{name} extends unknown interface {current}")
        names |= entry[0]
        optional |= entry[1]
        current = entry[2]
    return names, optional


def normalize(path: str) -> str:
    """Collapse path parameters so templates from two languages can be compared."""

    return re.sub(r"\{[^}]*\}|\$\{[^}]*\}", "{}", path)


def main() -> int:
    """Print a comparison table and return a non-zero exit status on any mismatch."""

    if not TS_FILE.exists():
        print(f"missing contract file: {TS_FILE}")
        return 2
    ts_text = TS_FILE.read_text(encoding="utf-8")
    ts = parse_ts(ts_text)

    from assistant_api.main import create_app

    spec = create_app().openapi()
    schemas = spec.get("components", {}).get("schemas", {})
    openapi_paths = {normalize(path) for path in spec["paths"]}

    failures: list[str] = []
    rows: list[tuple[str, str]] = []

    for name in sorted(ts):
        if name in NOT_SCHEMAS:
            rows.append((name, "skipped: client-only"))
            continue
        schema = schemas.get(name)
        if schema is None:
            failures.append(f"{name}: interface exists in TypeScript but not in OpenAPI")
            rows.append((name, "MISSING in backend"))
            continue

        backend_fields = set(schema.get("properties", {}))
        backend_required = set(schema.get("required", []))
        ts_fields, ts_optional = flatten(name, ts)

        missing = backend_fields - ts_fields
        extra = ts_fields - backend_fields
        required_but_optional = backend_required & ts_optional

        problems = []
        if missing:
            problems.append(f"missing in TS: {sorted(missing)}")
        if extra:
            problems.append(f"not in backend: {sorted(extra)}")
        if required_but_optional:
            problems.append(f"required but optional in TS: {sorted(required_but_optional)}")

        if problems:
            failures.extend(f"{name}: {problem}" for problem in problems)
            rows.append((name, "; ".join(problems)))
        else:
            rows.append(
                (name, f"ok ({len(backend_fields)} fields, {len(backend_required)} required)")
            )

    declared: list[tuple[str, str]] = []
    for key, path in ENDPOINT.findall(ts_text):
        declared.append((key, path))
    for key, template in ENDPOINT_FN.findall(ts_text):
        declared.append((key, template))

    for key, path in declared:
        if not path.startswith("/"):
            continue
        if normalize(path) not in openapi_paths:
            failures.append(f"endpoint {key}: {path} is not a registered route")
            rows.append((f"endpoint {key}", "MISSING route"))
        else:
            rows.append((f"endpoint {key}", path))

    width = max(len(name) for name, _ in rows)
    print(f"contract: {TS_FILE.relative_to(ROOT.parent.parent)}")
    for name, detail in rows:
        print(f"  {name:<{width}}  {detail}")

    print()
    if failures:
        print(f"{len(failures)} mismatch(es):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        f"contract ok: {len([r for r in rows if r[1].startswith('ok')])} interfaces, "
        f"{len(declared)} endpoint constants"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
