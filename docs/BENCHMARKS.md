# Soundme Pipeline Benchmarks

**Machine:** win32, Python 3.12.10, CPU (Ryzen / i7 class)
**Date:** 2026-10-19
**ASR Model:** Whisper `small` (faster-whisper, beam_size=1, 6 threads)
**Translator:** NLLB-200-distilled-600M via CTranslate2 int8 (batched, batch_size=8)
**TTS:** Edge TTS (km-KH-SreymomNeural)
**Upload Limits:** 500 MB / 30 min (env-configurable)

## Per-Stage Wall-Clock (seconds)

| Duration | Probe | Extract | ASR | Translate | TTS | Fit/Mix | Mux | Total | Segments | TTS Retries | Peak RSS (MB) | Disk (MB) | Realtime Ratio |
|----------|-------|---------|-----|-----------|-----|---------|-----|-------|----------|-------------|---------------|-----------|----------------|
| 5 min | 13.2 | 8.1 | 221.1 | 59.7 | 193.1 | 13.3 | 29.1 | 537.5 | 62 | 0 | 1581 | 54.3 | 1.87× |

## Stage Breakdown (5-min benchmark)

| Stage | Time (s) | % of Total | Bottleneck |
|-------|----------|------------|------------|
| Probe | 13.2 | 2% | I/O |
| Extract | 8.1 | 2% | FFmpeg I/O |
| **ASR** | **221.1** | **41%** | **CPU (Whisper small)** |
| Translate | 59.7 | 11% | CPU (CTranslate2 int8) |
| **TTS** | **193.1** | **36%** | **Network (Edge TTS)** |
| Fit/Mix | 13.3 | 2% | CPU (numpy) |
| Mux | 29.1 | 5% | FFmpeg mux |

## Speed Optimization History

| Change | Before | After | Speedup |
|--------|--------|-------|---------|
| NLLB PyTorch float32 → CTranslate2 int8 | 2.87 s/seg | 0.16 s/seg | **17.9×** |
| Translation % of pipeline | 51% | 11% | - |
| Whisper beam_size 5 → 1 | ~350s (est.) | 221s | ~1.6× |

## 30-Minute Extrapolation

**Assumption:** linear scaling from the 5-min benchmark. ASR and TTS scale linearly with segment count; translation scales linearly; probe/extract scale with duration.

- **Estimated total processing time:** ~54 minutes for 30-min video
- **Estimated segments:** ~372
- **Estimated disk usage:** ~325 MB
- **Peak RSS:** ~1.6 GB (stable; pipeline streams, not bulk-loads)
- **Per minute of video:** ~1.8 min processing on CPU

## Realtime Target Analysis

The 0.6× realtime target (300s video processed in 180s) is **not reachable on CPU** with this hardware:
- **ASR (41%):** Whisper `small` on CPU with beam=1 is already the fastest configuration. Moving to `tiny` would lose significant accuracy. Only GPU (CUDA fp16) can reduce this to ~30s.
- **TTS (36%):** Edge TTS is network-bound (~3.1s/segment including network round-trip). Azure Speech SDK with parallel requests could halve this.
- **Current best:** 1.87× realtime on CPU. With GPU ASR: estimated ~1.1× realtime. With GPU ASR + parallel Azure TTS: estimated ~0.5× realtime.

> [!NOTE]
> ASR and TTS are the dominant bottlenecks. Translation was reduced from 51% → 11% via
> CTranslate2 int8 batched inference. Further improvement requires GPU for ASR and/or
> parallel Azure Speech SDK for TTS.

## Resource Measurements

| Metric | Value | Method |
|--------|-------|--------|
| Peak RSS during 5-min benchmark | 1,581 MB | psutil |
| RSS during 500 MB upload | +5 MB delta | Streaming 5 MB chunks, psutil |
| RSS idle (models loaded) | ~1,550 MB | psutil |
