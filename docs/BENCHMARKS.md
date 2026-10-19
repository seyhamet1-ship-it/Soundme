# Soundme Pipeline Benchmarks

**Machine:** win32, Python 3.12.10
**Date:** 2026-10-19 10:48:15
**ASR Model:** small

## Per-Stage Wall-Clock (seconds)

| Duration | Probe | Extract | ASR | Translate | TTS | Fit/Mix | Mux | Total | Segments | TTS Retries | Peak RSS (MB) | Disk (MB) | Per-Hour (min) |
|----------|-------|---------|-----|-----------|-----|---------|-----|-------|----------|-------------|---------------|-----------|----------------|
| 5 min | 13.2 | 8.1 | 221.1 | 59.7 | 193.1 | 13.3 | 29.1 | 537.5 | 62 | 0 | 1581 | 54.3 | 107.5 |

## 60-Minute Extrapolation

**Assumption:** linear scaling from the 5-min benchmark (longest measured). ASR and TTS scale linearly with segment count; translation scales linearly; probe/extract scale with duration.

- **Estimated total processing time:** ~108 minutes
- **Estimated segments:** ~744
- **Estimated disk usage:** ~651 MB
- **Peak RSS:** ~1581 MB (stable; pipeline streams, not loads all)

> [!NOTE]
> TTS is the dominant bottleneck on CPU. Edge TTS (network) is faster per segment
> than MMS (local inference). For production 60-min processing, Azure Speech SDK
> with concurrency is recommended.
