# Phase 3 Status Report — G1 to G8

**Project:** Soundme — Khmer video-dubbing pipeline  
**Updated:** 2026-10-16  
**Environment:** Windows 11, Python 3.14, Node 24, FFmpeg 9  
**Commit:** 7d4bdee2095c7231bd7bf682f103757f2570a0ff (master)

---

## Summary

Phase 3 completes the TTS provider fallback chain (Edge → Azure → MMS), adds fail-closed `TTS_UNAVAILABLE` handling, fixes NLLB degeneration and the Khmer-text gate, deprecates `torch_dtype` → `dtype`, and adds GUI evidence + machine-verification scripts. All backend unit tests (22), frontend unit tests (5), lint, build, and `verify_all.py` checks pass except for items noted below. Live Edge and MMS synthesis artifacts exist; Azure Speech has never been exercised live (no credentials).

---

## G1–G8 detailed status

| Gate | Title | Status | Evidence / notes |
|------|-------|--------|------------------|
| **G1** | TTS provider fallback order: Edge → Azure → MMS | ✅ PASS | `synthesize_khmer` tries Edge TTS first, then Azure Speech only when `AZURE_SPEECH_KEY`+`AZURE_SPEECH_REGION` are set, then MMS (`facebook/mms-tts-khm`). Unit-tested in `tests/test_reliability.py`. Fallback order verified statically by `verify_all.py` (`provider-fallback-order-present`). |
| **G2** | Fail-closed `TTS_UNAVAILABLE` (no silent tone/fixture) | ✅ PASS | When all providers fail, the job fails with code `TTS_UNAVAILABLE` and Khmer + English messages. Verified by `pipeline-preserves-tts-unavailable-code`, `render-rejects-demo-outside-demo-mode`, and e2e `unavailable` mode (PASS). Preview endpoint returns HTTP 503 with `TTS_UNAVAILABLE` detail. |
| **G3** | NLLB degeneration fixed (greedy decode, `no_repeat_ngram_size=3`) | ✅ PASS | `app/providers.py`: switched `num_beams=2` → greedy `num_beams=1` with `no_repeat_ngram_size=3`. Resolved the `អូន` repetition loop; coherent Khmer output confirmed. All NLLB-related tests pass. |
| **G4** | Khmer-text gate fixed (`is_khmer_text`) | ✅ PASS | `app/segments.py`: presence-based gate (any Khmer letters, no Chinese) instead of brittle 0.75 ratio. Accepts Khmer with Latin brand names (e.g. "SoundMe"); rejects empty/English-passthrough/Chinese. Covered by `test_khmer_gate_allows_latin_brand_but_rejects_english`. |
| **G5** | `torch_dtype` → `dtype` deprecation fix | ✅ PASS | `app/providers.py`: replaced deprecated `torch_dtype=` with `dtype=`. mypy clean; no warnings. |
| **G6** | Real / demo / unavailable e2e runs | ✅ PASS | `scripts/run_e2e.py` three modes all PASS: `real` (37.9s), `demo`, `unavailable` (fail-closed `TTS_UNAVAILABLE`). Live Edge synthesis artifacts: `demo/edge-live-test.mp3` (19,008 bytes, `km-KH-SreymomNeural`), `demo/mms-live-test.wav` (116,780 bytes). Azure never exercised live (no credentials). |
| **G7** | GUI evidence screenshots | ⚠️ PARTIAL | Playwright spec `frontend/e2e/gui.spec.js` written and `Makefile`/`playwright.config.js` created. `docs/evidence/` directory exists. **Blocker:** backend `:8000` not running in this session, so screenshots `gui-01..05-*.png` were not captured. No Azure credentials available for live TTS in GUI full-run. |
| **G8** | Machine-verification script (`verify_all.py`) | ⚠️ PARTIAL | `scripts/verify_all.py` runs and passes 16/17 checks. **Remaining:** `doc-exists:docs/PHASE3_STATUS.md` — this document. Frontend checks fixed (`npm.cmd` on Windows, vitest excludes `e2e/`). |

---

## Remaining / unknowns

- **Azure Speech:** code-complete and unit-tested with a fake provider, but never exercised live (no `AZURE_SPEECH_KEY`/`AZURE_SPEECH_REGION` in this environment).
- **npm audit:** 13 dependency-chain findings previously reported (5 moderate, 6 high, 2 critical). `npm audit fix` was not applied broadly due to risk of breaking the locked frontend toolchain; see `docs/npm-audit-before.json`.
- **Human listening check:** no human has confirmed intelligibility/naturalness of generated Khmer speech; the speech guard (VAD/spectrum/envelope) is a screening heuristic only.
- **NLLB-200 license:** CC-BY-NC 4.0; review before commercial use.
- **FastAPI/Starlette deprecation warnings:** `router.on_event` and TestClient/httpx integration — pre-existing, non-blocking.

---

## Artifacts

- `demo/edge-live-test.mp3` — live Edge Khmer TTS (PASS)
- `demo/mms-live-test.wav` — live MMS Khmer TTS (PASS)
- `demo/verify_all.json` — machine-verification output
- `docs/evidence/` — intended GUI screenshot directory (empty; GUI e2e not run in this session)
- `frontend/e2e/gui.spec.js` — Playwright spec (excluded from vitest; requires backend on `:8000`)
