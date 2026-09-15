# Build rules — read before writing any code

## Hard constraints
1. **No client names anywhere.** These repos must not contain the hiring company's name or
   the author's employer's name, in code, comments, docs, commit messages, or cloud resource
   names. Cloud resources use the prefix `ak-project-`.
2. **No secrets in the repo.** `.env.example` only. Real values live on the server / in
   Modal Secrets. `.gitignore` must cover `.env`, `.env.local`, `data/`, `*.mp4` uploads,
   `node_modules`, `__pycache__`, `.next`.
3. **The contract files in `_contracts/` are law.** If something must change, change the
   contract file first and say so in your report.

## Code style — "best fit", not maximal
- Prefer a 200-line file that is obviously correct over six 40-line files with an
  abstraction layer between them. Do not create `interfaces/`, `factories/`, `managers/`,
  or a DI container.
- One module per real concept. In the Python backends that's roughly:
  `main.py`, `models.py`, `store.py`, `vlm.py`/`slam/*`, `pipeline.py`, `export.py`.
- Type hints everywhere in Python, `strict: true` in TypeScript. No `any`, no bare `except:`.
- Comments explain *why*, never *what*. Density should match a good production repo:
  sparse, and only where the reasoning is non-obvious (a magic threshold, a numerical
  stability trick, a deliberate deviation).
- No TODOs, no commented-out code, no dead branches in the delivered result.
- Every piece of code you write must actually run. If you cannot run it, say so explicitly
  in your report rather than claiming it works.

## Python
- Python 3.11, FastAPI + uvicorn, pydantic v2.
- `pyproject.toml` with pinned direct deps (`~=` minor pins). `ruff` config included.
- Async where it buys concurrency (network I/O); plain sync + threadpool for CPU work.

## TypeScript
- Next.js 15 App Router, `output: 'standalone'`, Node 20+.
- Server components by default; `'use client'` only where interactivity requires it.
- No `useEffect` for data that a server component can fetch.

## Deliverables per assignment
- `README.md` — what it is, live URL, quickstart (docker compose up), env vars, how to run
  tests, measured performance, limitations.
- `docs/` — architecture, key decisions with trade-offs, ADRs numbered `ADR-0001-*.md`.
- Working `docker-compose.yml` and `Dockerfile`s.
- A `Makefile` with `make dev`, `make test`, `make build`, `make bench`.
- Tests that actually assert behaviour (pytest for backends, one Playwright smoke for each
  frontend is enough — do not chase coverage numbers).
