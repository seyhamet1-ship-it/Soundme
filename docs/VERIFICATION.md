# Verification report

Updated 2026-10-16. Environment: Windows 11, Python 3.14, Node 24, FFmpeg 9.

## Verified

- `python -m compileall -q app` succeeds.
- FastAPI app imports; OpenAPI registers job upload/list/status/events/segments/review/resume/cancel/delete/download, voices, preview, and health paths.
- TestClient requests to `/api/health`, `/api/voices`, and `/api/jobs` returned HTTP 200.
- `python -m pytest -q`: 14 passed, including API, translation retry, provider-fallback/demo-mode, and audio-guard regression checks. Two pre-existing FastAPI/Starlette deprecation warnings remain.
- `npm test`: 3 React smoke tests passed (Khmer render, language switch, invalid-file validation).
- `npm run build`: passed after self-hosted Khmer font integration; generated local Noto Sans Khmer 400/700 font assets.
- `npm run lint`: passed for the modified React app, UI tests, and entry point.

## Not verified / blockers

- Edge TTS synthesis was previously tested and failed with WebSocket HTTP 403. Retry has not been able to verify live synthesis from the current environment. See [TTS diagnosis](TTS_DIAGNOSIS.md); do not claim a successful real Khmer dub until a live speech artifact is generated and reviewed.
- Rendering now fails closed on Edge errors and on demo/non-Edge TTS artifacts; NLLB errors likewise fail rather than silently using a fixture. Demo fixtures require explicit `DEMO_MODE=1` and have provenance labels.
- A VAD/spectrum/envelope guard rejects silence and generated tones. This is a screening heuristic, not proof of intelligibility or natural Khmer speech.
- NLLB-200 weights are CC-BY-NC 4.0; review licensing before commercial use.
- No real speech-containing video was run through the complete transcription → translation → TTS → mux pipeline, and no browser screenshot/E2E run is claimed.
- npm reported 13 dependency audit findings (5 moderate, 6 high, 2 critical); no broad automatic upgrades were applied.
- FastAPI emits deprecation warnings for `router.on_event` and Starlette's TestClient/httpx integration.

## UI/API scope

The React client provides video selection/drop, source and voice selection, upload progress, job polling/history, translation review, cancellation, and download links. Current tests cover rendering, language switch, and invalid-file validation. This is an MVP, not evidence of successful live voice dubbing.
