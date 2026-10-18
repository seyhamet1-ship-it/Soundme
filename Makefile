.PHONY: verify verify-live verify-full test frontend e2e probes gui

# ── Deterministic verification (no network, no model inference) ──────────────
# Runs: compile, pytest, mypy, frontend tests/lint/build, behavioural artifact checks, and Playwright E2E.
# BLOCKED/SKIPPED always shows as BLOCKED, never PASS.
verify:
	python scripts/verify_all.py --e2e

# ── Live verification (adds network probes + step 6 pipeline) ────────────────
verify-live:
	python scripts/verify_all.py --live

# ── Full verification (live + Playwright E2E, requires running server) ────────
verify-full:
	python scripts/verify_all.py --live --full

# ── Unit tests only ───────────────────────────────────────────────────────────
test:
	python -m pytest -q

# ── Frontend: Vitest + lint + build ──────────────────────────────────────────
frontend:
	cd frontend && npm test && npm run lint && npm run build

# ── Playwright GUI E2E (requires uvicorn running on :8000) ───────────────────
gui:
	cd frontend && npx playwright test

# ── Live TTS provider probes ──────────────────────────────────────────────────
probes:
	python scripts/probe_edge_tts.py
	python scripts/probe_mms.py

# ── Step 6: Full pipeline real-mode (en + zh), saves to demo/ ────────────────
pipeline:
	python scripts/step6_pipeline_real.py
