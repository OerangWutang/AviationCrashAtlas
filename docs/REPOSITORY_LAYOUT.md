# Repository layout

AviationCrashAtlas is a mixed Python + TypeScript repository. This document records the intended boundaries so new code lands in the right place and future moves can be done incrementally.

## Top-level areas

| Path | Purpose |
| --- | --- |
| `application/` | Backend application services, use cases, ingestion orchestration, DTOs. |
| `domain/` | Backend domain model, domain services, fakes used by backend tests. |
| `infrastructure/` | Backend adapters: database repositories, event bus, external integrations. |
| `presentation/` | Backend delivery layers: FastAPI routers/schemas and Typer CLI commands. |
| `security/`, `config.py`, `logging_config.py`, `mfa.py` | Backend cross-cutting runtime support. |
| `alembic/` | Backend database migrations. |
| `api/`, `domain/test_*.py`, `application/**/test_*.py`, `infrastructure/test_*.py` | Current backend test locations. Move these under `tests/` in follow-up PRs. |
| `src/` | TypeScript runtime code plus the `src/atlas` Python compatibility namespace. |
| `app/`, `components/`, `features/`, `routes/`, `lib/`, `types/` | React/Vite frontend UI code. |
| `e2e/` | Playwright end-to-end tests for the frontend/API flow. |
| `apps/marketing/` | Static marketing site. |
| `deploy/`, `ops/`, `scripts/` | Deployment, operational tooling, and one-off scripts. |

## Python namespace

Python code should import backend modules through `atlas.*`, for example:

```python
from atlas.application.dto import IngestionClaimDTO
from atlas.domain.enums import Role
```

The physical backend implementation is still in root-level packages. `src/atlas/__init__.py` provides a compatibility namespace so packaging, tests, CLI entry points, and docs can use one stable import path while source files are migrated toward `src/atlas/...` in smaller PRs.

## Recommended follow-up moves

1. Move backend production packages into `src/atlas/` and remove the compatibility aliases.
2. Move backend tests into `tests/api/`, `tests/domain/`, `tests/application/`, and `tests/infrastructure/`.
3. Move frontend root directories into a dedicated app boundary, for example `apps/web/`, once Vite, TypeScript, Playwright, and import aliases are updated together.
4. Keep `apps/marketing/` separate from the authenticated Atlas web app.

## New-code rules

- Backend production code goes under the appropriate backend layer and imports through `atlas.*`.
- Backend tests should prefer `tests/` for new files.
- Frontend UI code should stay in the existing React/Vite folders until the full `apps/web/` move is done.
- Deployment and operational scripts should stay out of runtime packages unless they are imported by the application.
