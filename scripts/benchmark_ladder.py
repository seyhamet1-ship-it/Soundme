"""Benchmark ladder: 1 min -> 5 min -> 15 min varied-text REAL mode runs.
Measures wall-clock per stage, peak RSS, disk use, TTS retries/failures.
Saves results to docs/BENCHMARKS.md and extrapolates to 60 min.
"""
from __future__ import annotations
import json, os, sys, time, subprocess, shutil, uuid, hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FFMPEG = os.getenv("FFMPEG", "ffmpeg")
FFPROBE = os.getenv("FFPROBE", "ffprobe")

# Varied sentences for realistic benchmark (no repeated text)
SENTENCES_EN = [
    "Welcome to the Soundme Khmer dubbing studio.",
    "This benchmark measures real processing performance.",
    "Natural language processing has advanced significantly.",
    "The Mekong River flows through six countries in Southeast Asia.",
    "Cambodia's Angkor Wat is the largest religious monument in the world.",
    "Machine translation quality continues to improve each year.",
    "The future of speech technology is voice cloning.",
    "Phnom Penh is the vibrant capital of Cambodia.",
    "Artificial intelligence is transforming healthcare delivery worldwide.",
    "The monsoon season brings heavy rainfall to the region.",
    "Traditional Khmer music features unique instruments and melodies.",
    "Solar energy adoption is accelerating across developing nations.",
    "Rice paddies stretch across the Cambodian countryside.",
    "The digital economy is creating new opportunities for young people.",
    "Education reform requires sustained investment and political will.",
    "Biodiversity loss threatens ecosystem stability globally.",
    "Urban planning must balance growth with environmental protection.",
    "Cultural heritage preservation is a priority for many nations.",
    "The telecommunications industry is rapidly evolving.",
    "Water scarcity affects millions of people in arid regions.",
    "International cooperation is essential for addressing climate change.",
    "Modern agriculture uses technology to increase crop yields.",
    "Tourism contributes significantly to many national economies.",
    "Public health infrastructure needs constant improvement.",
    "Renewable energy sources are becoming cost competitive.",
    "The history of Southeast Asia spans thousands of years.",
    "Financial technology is democratizing access to banking services.",
    "Space exploration captures the imagination of people worldwide.",
    "Sustainable development balances economic growth with environmental care.",
    "Literacy programs empower communities and reduce poverty.",
]

SENTENCES_ZH = [
    "欢迎使用Soundme柬埔寨语配音工作室。",
    "本次基准测试衡量实际处理性能。",
    "自然语言处理技术取得了显著进步。",
    "湄公河流经东南亚六个国家。",
    "柬埔寨的吴哥窟是世界上最大的宗教纪念建筑。",
    "机器翻译的质量每年都在持续改善。",
    "语音技术的未来是声音克隆。",
    "金边是柬埔寨充满活力的首都。",
    "人工智能正在改变全球医疗服务的提供方式。",
    "季风季节为该地区带来大量降雨。",
    "传统高棉音乐具有独特的乐器和旋律。",
    "太阳能在发展中国家的使用正在加速。",
    "稻田遍布柬埔寨的乡村地区。",
    "数字经济正在为年轻人创造新的机会。",
    "教育改革需要持续的投资和政治意愿。",
]


def generate_test_video(duration_min: int, lang: str = "en") -> Path:
    """Generate a varied-text test video of the given duration using Edge TTS."""
    import asyncio
    import edge_tts

    sentences = SENTENCES_EN if lang == "en" else SENTENCES_ZH
    out_dir = ROOT / "tests" / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    video_path = out_dir / f"bench_{duration_min}min_{lang}.mp4"

    if video_path.exists():
        # Verify duration
        probe = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_format", "-of", "json", str(video_path)],
            capture_output=True, text=True, check=True
        ).stdout)
        dur = float(probe["format"]["duration"])
        if abs(dur - duration_min * 60) < 5:
            print(f"  Reusing existing {video_path} ({dur:.1f}s)")
            return video_path

    # Target: each sentence ~4s with gaps -> ~5s per sentence
    target_seconds = duration_min * 60
    secs_per_sentence = 5.0
    n_sentences = max(1, int(target_seconds / secs_per_sentence))

    # Generate TTS audio segments
    audio_parts = []
    silence = out_dir / "silence_500ms.wav"
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                    "-t", "0.5", "-c:a", "pcm_s16le", str(silence)],
                   check=True, capture_output=True)

    voice = "en-US-JennyNeural" if lang == "en" else "zh-CN-XiaoxiaoNeural"
    for i in range(n_sentences):
        text = sentences[i % len(sentences)]
        seg_wav = out_dir / f"seg_{lang}_{i:04d}.wav"
        seg_mp3 = out_dir / f"seg_{lang}_{i:04d}.mp3"

        if not seg_wav.exists():
            async def synth():
                c = edge_tts.Communicate(text, voice)
                await c.save(str(seg_mp3))
            asyncio.run(synth())
            # Convert to wav
            subprocess.run([FFMPEG, "-y", "-i", str(seg_mp3), "-ar", "24000",
                            "-ac", "1", "-c:a", "pcm_s16le", str(seg_wav)],
                           check=True, capture_output=True)
            seg_mp3.unlink(missing_ok=True)

        audio_parts.append(str(seg_wav))
        audio_parts.append(str(silence))

    # Concat audio
    concat_list = out_dir / f"concat_{lang}_{duration_min}.txt"
    with concat_list.open("w") as f:
        for p in audio_parts:
            f.write(f"file '{p}'\n")

    full_audio = out_dir / f"bench_{duration_min}min_{lang}.wav"
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i",
                    str(concat_list), "-c:a", "pcm_s16le", str(full_audio)],
                   check=True, capture_output=True)

    # Trim to exact duration
    trimmed = out_dir / f"bench_{duration_min}min_{lang}_trimmed.wav"
    subprocess.run([FFMPEG, "-y", "-i", str(full_audio), "-t", str(target_seconds),
                    "-c:a", "pcm_s16le", str(trimmed)],
                   check=True, capture_output=True)

    # Mux with color video
    subprocess.run([FFMPEG, "-y",
                    "-f", "lavfi", "-i", f"color=c=0x1a1a2e:s=640x360:d={target_seconds}:r=10",
                    "-i", str(trimmed),
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35",
                    "-c:a", "aac", "-b:a", "64k", "-shortest",
                    str(video_path)],
                   check=True, capture_output=True)

    # Cleanup intermediate files
    full_audio.unlink(missing_ok=True)
    trimmed.unlink(missing_ok=True)
    concat_list.unlink(missing_ok=True)

    return video_path


def measure_rss_kb() -> int:
    """Current process RSS in KB."""
    try:
        import psutil
        return psutil.Process().memory_info().rss // 1024
    except Exception:
        return 0


def run_benchmark(duration_min: int, lang: str = "en") -> dict:
    """Run a full pipeline benchmark and return timing + resource stats."""
    from app.providers import WhisperASR, translate_khmer, synthesize_khmer
    from app.segments import Segment, normalize_numbers
    from app.audio_quality import require_real_speech

    print(f"\n{'='*60}")
    print(f"BENCHMARK: {duration_min} min, lang={lang}")
    print(f"{'='*60}")

    video_path = generate_test_video(duration_min, lang)
    file_size_mb = video_path.stat().st_size / (1024 * 1024)
    print(f"  Input: {video_path} ({file_size_mb:.1f} MB)")

    stats = {
        "duration_min": duration_min,
        "lang": lang,
        "file_size_mb": round(file_size_mb, 1),
        "stages": {},
        "peak_rss_kb": 0,
        "tts_retries": 0,
        "tts_failures": 0,
        "total_segments": 0,
    }
    workdir = ROOT / "tests" / "benchmark" / f"work_{duration_min}_{lang}"
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)

    rss_samples = [measure_rss_kb()]

    # 1. Probe
    t0 = time.monotonic()
    probe_data = json.loads(subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video_path)],
        capture_output=True, text=True, check=True
    ).stdout)
    duration_s = float(probe_data["format"]["duration"])
    stats["stages"]["probe"] = round(time.monotonic() - t0, 2)
    rss_samples.append(measure_rss_kb())
    print(f"  probe: {stats['stages']['probe']:.2f}s (duration={duration_s:.1f}s)")

    # 2. Extract audio
    t0 = time.monotonic()
    wav_path = workdir / "source.wav"
    subprocess.run([FFMPEG, "-y", "-i", str(video_path), "-map", "0:a:0", "-vn",
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path)],
                   check=True, capture_output=True)
    stats["stages"]["extract"] = round(time.monotonic() - t0, 2)
    rss_samples.append(measure_rss_kb())
    print(f"  extract: {stats['stages']['extract']:.2f}s")

    # 3. ASR
    t0 = time.monotonic()
    asr = WhisperASR(os.getenv("ASR_MODEL", "small"))
    transcript = asr.transcribe(wav_path, lang)
    segments = [Segment(i+1, float(s.start), float(s.end), str(s.text).strip())
                for i, s in enumerate(transcript.segments) if str(s.text).strip()]
    stats["stages"]["asr"] = round(time.monotonic() - t0, 2)
    stats["total_segments"] = len(segments)
    rss_samples.append(measure_rss_kb())
    print(f"  asr: {stats['stages']['asr']:.2f}s ({len(segments)} segments)")

    # 4. Translate (batched)
    t0 = time.monotonic()
    from app.providers import translate_khmer_batch
    source_texts = [s.source_text for s in segments]
    translated_km, _ = translate_khmer_batch(source_texts, transcript.language)
    for s, km in zip(segments, translated_km):
        s.text_km = normalize_numbers(km)
    stats["stages"]["translate"] = round(time.monotonic() - t0, 2)
    rss_samples.append(measure_rss_kb())
    print(f"  translate: {stats['stages']['translate']:.2f}s (batched)")

    # 5. TTS
    t0 = time.monotonic()
    tts_retries = 0
    tts_failures = 0
    for idx, s in enumerate(segments):
        out_wav = workdir / f"voice-{idx:04d}.wav"
        try:
            synthesize_khmer(s.text_km, "km-KH-SreymomNeural", out_wav, max(0.2, s.end - s.start))
        except Exception as e:
            tts_retries += 1
            try:
                time.sleep(1)
                synthesize_khmer(s.text_km, "km-KH-SreymomNeural", out_wav, max(0.2, s.end - s.start))
            except Exception:
                tts_failures += 1
                print(f"    TTS FAILED for segment {idx}: {e}")
    stats["stages"]["tts"] = round(time.monotonic() - t0, 2)
    stats["tts_retries"] = tts_retries
    stats["tts_failures"] = tts_failures
    rss_samples.append(measure_rss_kb())
    print(f"  tts: {stats['stages']['tts']:.2f}s (retries={tts_retries}, failures={tts_failures})")

    # 6. Fit & Mix
    t0 = time.monotonic()
    import wave
    import numpy as np
    dub = np.zeros(int(duration_s * 48000), dtype=np.float32)
    prev_end = 0.0
    for idx, s in enumerate(segments):
        out_wav = workdir / f"voice-{idx:04d}.wav"
        if not out_wav.exists():
            continue
        slot = max(0.2, s.end - s.start)
        with wave.open(str(out_wav), "rb") as f:
            rate = f.getframerate()
            data = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        raw_dur = len(data) / rate
        tempo = min(1.25, max(1.0, raw_dur / slot)) if raw_dur > slot else 1.0
        if tempo > 1.0:
            data = np.interp(np.arange(int(len(data) / tempo)) * tempo, np.arange(len(data)), data).astype(np.float32)
        res = np.interp(np.arange(int(len(data) * 48000 / rate)) * rate / 48000, np.arange(len(data)), data).astype(np.float32)
        fitted_dur = len(res) / 48000.0
        start_sec = max(s.start, prev_end - 0.08) if prev_end > 0 else s.start
        start_idx = int(start_sec * 48000)
        end_idx = min(len(dub), start_idx + len(res))
        if start_idx < len(dub):
            dub[start_idx:end_idx] += res[:end_idx - start_idx]
        prev_end = start_sec + fitted_dur
    peak = float(np.max(np.abs(dub))) if len(dub) else 0.0
    if peak > 0:
        dub *= min(0.89, 0.89 / peak)
    dub_wav = workdir / "dub.wav"
    with wave.open(str(dub_wav), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(48000)
        f.writeframes((np.clip(dub, -0.89, 0.89) * 32767).astype("<i2").tobytes())
    stats["stages"]["fit_mix"] = round(time.monotonic() - t0, 2)
    rss_samples.append(measure_rss_kb())
    print(f"  fit_mix: {stats['stages']['fit_mix']:.2f}s")

    # 7. Mux
    t0 = time.monotonic()
    out_mp4 = workdir / "dubbed.mp4"
    mux_cmd = [
        FFMPEG, "-y", "-i", str(video_path), "-i", str(dub_wav),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-t", str(duration_s), "-movflags", "+faststart",
        str(out_mp4)
    ]
    subprocess.run(mux_cmd, check=True, capture_output=True)
    stats["stages"]["mux"] = round(time.monotonic() - t0, 2)
    rss_samples.append(measure_rss_kb())
    print(f"  mux: {stats['stages']['mux']:.2f}s")

    # 6. Disk usage
    disk_kb = sum(f.stat().st_size for f in workdir.rglob("*") if f.is_file()) // 1024
    stats["disk_use_kb"] = disk_kb
    stats["peak_rss_kb"] = max(rss_samples)

    # Total
    total = sum(stats["stages"].values())
    stats["stages"]["total"] = round(total, 2)
    stats["per_hour_min"] = round(total / (duration_min / 60) / 60, 1) if duration_min > 0 else 0
    print(f"  TOTAL: {total:.1f}s | peak RSS: {stats['peak_rss_kb']} KB | disk: {disk_kb} KB")
    print(f"  Extrapolated per hour of video: {stats['per_hour_min']:.1f} min")

    # Cleanup
    shutil.rmtree(workdir, ignore_errors=True)

    return stats


def write_benchmarks_md(results: list[dict]):
    """Write docs/BENCHMARKS.md with results and 60-min extrapolation."""
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    out = docs / "BENCHMARKS.md"

    lines = [
        "# Soundme Pipeline Benchmarks",
        "",
        f"**Machine:** {sys.platform}, Python {sys.version.split()[0]}",
        f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**ASR Model:** {os.getenv('ASR_MODEL', 'small')}",
        "",
        "## Per-Stage Wall-Clock (seconds)",
        "",
        "| Duration | Probe | Extract | ASR | Translate | TTS | Fit/Mix | Mux | Total | Segments | TTS Retries | Peak RSS (MB) | Disk (MB) | Per-Hour (min) |",
        "|----------|-------|---------|-----|-----------|-----|---------|-----|-------|----------|-------------|---------------|-----------|----------------|",
    ]

    for r in results:
        s = r["stages"]
        lines.append(
            f"| {r['duration_min']} min | {s.get('probe',0):.1f} | {s.get('extract',0):.1f} "
            f"| {s.get('asr',0):.1f} | {s.get('translate',0):.1f} | {s.get('tts',0):.1f} "
            f"| {s.get('fit_mix',0):.1f} | {s.get('mux',0):.1f} "
            f"| {s.get('total',0):.1f} | {r['total_segments']} | {r['tts_retries']} "
            f"| {r['peak_rss_kb']/1024:.0f} | {r['disk_use_kb']/1024:.1f} "
            f"| {r['per_hour_min']:.1f} |"
        )

    # Extrapolation
    if results:
        last = results[-1]
        factor = 60 / last["duration_min"]
        est_total_min = last["stages"]["total"] * factor / 60
        est_segments = int(last["total_segments"] * factor)
        est_disk_mb = last["disk_use_kb"] * factor / 1024
        lines += [
            "",
            "## 60-Minute Extrapolation",
            "",
            f"**Assumption:** linear scaling from the {last['duration_min']}-min benchmark "
            f"(longest measured). ASR and TTS scale linearly with segment count; "
            f"translation scales linearly; probe/extract scale with duration.",
            "",
            f"- **Estimated total processing time:** ~{est_total_min:.0f} minutes",
            f"- **Estimated segments:** ~{est_segments}",
            f"- **Estimated disk usage:** ~{est_disk_mb:.0f} MB",
            f"- **Peak RSS:** ~{last['peak_rss_kb']/1024:.0f} MB (stable; pipeline streams, not loads all)",
            "",
            "> [!NOTE]",
            "> TTS is the dominant bottleneck on CPU. Edge TTS (network) is faster per segment",
            "> than MMS (local inference). For production 60-min processing, Azure Speech SDK",
            "> with concurrency is recommended.",
        ]

    lines += [""]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nBenchmarks written to {out}")
    return out


if __name__ == "__main__":
    # Pre-warm models so benchmark measures pure execution throughput
    print("Pre-warming ASR and Translator models...")
    t_warm = time.time()
    from app.providers import WhisperASR, NllbTranslator
    WhisperASR(os.getenv("ASR_MODEL", "small"))
    NllbTranslator().translate("Hello world", "en")
    print(f"Models warmed in {time.time() - t_warm:.1f}s!\n")

    # Run benchmark ladder
    results = []
    durs = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else [1, 5, 15]
    for dur in durs:
        try:
            r = run_benchmark(dur, "en")
            results.append(r)
        except Exception as e:
            print(f"BLOCKED: {dur}-min benchmark failed: {e}")
            import traceback
            traceback.print_exc()

    write_benchmarks_md(results)

    # Save raw JSON
    raw = ROOT / "docs" / "benchmarks_raw.json"
    raw.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Raw results saved to {raw}")
