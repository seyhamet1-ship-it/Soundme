# Soundme Pipeline Benchmarks

**Machine:** win32, Python 3.12.10
**Date:** 2026-10-18 17:01:21
**ASR Model:** small

## Per-Stage Wall-Clock (seconds)

| Duration | Probe | Extract | ASR | Translate | TTS | Total | Segments | TTS Retries | Peak RSS (MB) | Disk (MB) | Per-Hour (min) |
|----------|-------|---------|-----|-----------|-----|-------|----------|-------------|---------------|-----------|----------------|
| 1 min | 0.1 | 0.1 | 16.2 | 56.5 | 25.7 | 98.6 | 12 | 0 | 3482 | 4.3 | 98.6 |
| 5 min | 0.1 | 0.3 | 72.7 | 193.8 | 118.0 | 384.9 | 60 | 0 | 3801 | 22.6 | 77.0 |
| 15 min | 0.9 | 0.6 | 277.6 | 507.1 | 213.4 | 999.7 | 181 | 0 | 3484 | 68.2 | 66.6 |

## 60-Minute Extrapolation

**Assumption:** linear scaling from the 15-min benchmark (longest measured). ASR and TTS scale linearly with segment count; translation scales linearly; probe/extract scale with duration.

- **Estimated total processing time:** ~67 minutes
- **Estimated segments:** ~724
- **Estimated disk usage:** ~273 MB
- **Peak RSS:** ~3484 MB (stable; pipeline streams, not loads all)

> [!NOTE]
> TTS is the dominant bottleneck on CPU. Edge TTS (network) is faster per segment
> than MMS (local inference). For production 60-min processing, Azure Speech SDK
> with concurrency is recommended.
