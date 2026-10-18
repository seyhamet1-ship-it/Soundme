# TTS diagnosis

Updated 2026-10-16 (Phase 3: provider fallback and live probes).

## Findings

- The Microsoft-hosted voice catalogue responded and returned Khmer voice entries (including Sreymom and Piseth). Catalogue access does not verify synthesis availability.
- A live Edge synthesis attempt previously failed during its WebSocket handshake with HTTP 403 (edge-tts 6.x era). After upgrading to `edge-tts==7.3.1`, live Khmer synthesis **PASS**ed twice on 2026-10-16:
  - `python scripts/probe_edge_tts.py` → `EDGE_RESULT=PASS bytes=19008 elapsed_s=0.99 artifact=demo\edge-live-test.mp3` (voice `km-KH-SreymomNeural`, text: `សួស្តី។ នេះជាការសាកល្បងសំឡេង។`), repeated run `bytes=19008 elapsed_s=0.99`.
- MMS offline Khmer TTS **PASS** on 2026-10-16: `MMS_RESULT=PASS model=facebook/mms-tts-khm seconds=3.65 sample_rate=16000 peak=0.8145 speech_guard=PASS elapsed_s=128.25 artifact=demo\mms-live-test.wav` (116,780-byte WAV; first run includes the 145 MB model download).
- Edge TTS emits MP3. The provider converts it to mono 24 kHz PCM WAV with FFmpeg. MMS emits PCM WAV directly at 16 kHz; both paths are resampled onto the 48 kHz dub timeline during render.
- Per-job provider fallback is now implemented in `synthesize_khmer`: **Edge TTS → Azure Speech (only when `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` are set) → MMS (`facebook/mms-tts-khm`)**. Every failure is recorded and, if all candidates fail, the job fails closed with `TTS_UNAVAILABLE` (never a silent tone).
- `POST /api/tts/preview` performs a real fallback synthesis and returns WAV bytes with an `X-TTS-Provider` header; failures return HTTP 503 with the `TTS_UNAVAILABLE` detail.
- Provider failure no longer silently produces a tone or fixture translation. Demo translation/TTS require explicit `DEMO_MODE=1`; tone output carries a `demo-tone-not-speech` provider label, the transcript is stamped `"mode": "demo-plumbing"`, and the speech guard is skipped only for that explicitly enabled demo mode.
- The speech guard checks VAD, spectrum, and amplitude-envelope modulation. It is a screening heuristic, not a human intelligibility assessment or proof of natural Khmer speech.

## Remaining verification

- No human has listened to and confirmed intelligibility/naturalness of the generated Khmer speech; the guard only screens for speech-like signal properties.
- Azure Speech is code-complete and unit-tested with a fake provider but has never been exercised live (no credentials in this environment).
- NLLB-200 weights are CC-BY-NC 4.0; review licensing before commercial use.

