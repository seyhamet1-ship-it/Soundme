# Soundme — Security, Privacy & Abuse Policy

## Access control
Soundme requires an access code (token) for **all** API operations except `/api/health`.  
Tokens are configured in the `ACCESS_TOKENS` environment variable as a comma-separated list.  
There is no self-registration. Tokens are distributed by the operator.

**Token passing (any one of):**
- `Authorization: Bearer <token>` header
- `X-Access-Token: <token>` header  
- `?token=<token>` query parameter

**Per-token isolation:** Token A cannot read, list, download, or modify Token B's jobs, segments, chunks, or outputs. Cross-token access returns 404.

---

## Rate limits & abuse controls

| Protection | Default | Environment variable |
|---|---|---|
| Requests per minute per token | 120 | `RATE_LIMIT_RPM` |
| Requests per minute per IP | 240 (2×) | derived from `RATE_LIMIT_RPM` |
| Max queued jobs (all tokens) | 5 | `MAX_QUEUE_CAP` |
| Concurrent processing workers | 1 | `MAX_CONCURRENT_JOBS` |
| Daily quota per token | 60 min of video | `DAILY_QUOTA_MINUTES` |
| File/output TTL | 24 hours | `CLEANUP_TTL_HOURS` |
| Max upload size | 500 MB | `MAX_UPLOAD_MB` |
| Max video duration | 30 min | `MAX_DURATION_MIN` |

Exceeded limits return **HTTP 429** with a `Retry-After` header and a bilingual (Khmer + English) message.  
Queue-full returns a `SERVER_BUSY` code with an estimated wait time.

---

## File retention and deletion
- Uploaded videos and all processed outputs (dubbed video, SRT, VTT, WAV) are **automatically deleted after 24 hours** (configurable via `CLEANUP_TTL_HOURS`).
- Incomplete chunk uploads are also cleaned up after the TTL.
- No video, audio, or subtitle data is retained beyond the TTL.
- Users see the notice: *"Upload only content you own or have permission to dub; files are deleted after 24 h."*

---

## Privacy
- Video is **processed locally on the server** — it is never sent to external services.
- **Exception — Azure Speech TTS:** If `AZURE_SPEECH_KEY` is configured, the *translated Khmer text captions* (not the video or original audio) are sent to Microsoft Azure for speech synthesis. No video or audio leaves the server.
- Azure TTS character usage is metered. Monthly usage is tracked in the local database and capped at 450,000 characters/month (configurable via `AZURE_MONTHLY_CHAR_CAP`) to stay within the Azure free neural tier.
- No personal data is collected, stored, or transmitted beyond what is described above.

---

## Copyright and content policy
- Users must upload only content they **own** or have **explicit permission** to dub.
- The operator is not responsible for copyright violations by users.
- Soundme is designed for private testing with 1–5 people and is **not intended for public deployment**.
- Do not upload copyrighted media without authorisation from the rights holder.

---

## Abuse prevention — technical controls
| Control | Implementation |
|---|---|
| Authentication | All `/api/*` routes require a valid token; `401` otherwise |
| Token isolation | `owner_token` stored with each job; lookups filter by token |
| Rate limiting | In-memory sliding-window per token + per IP |
| Queue cap | `MAX_QUEUE_CAP` enforced before job creation |
| Daily quota | `DAILY_QUOTA_MINUTES` enforced per token per UTC day |
| File size | `413` returned if declared or actual size exceeds `MAX_UPLOAD_MB` |
| Path traversal | Filenames sanitised; `..`, `/`, `\` stripped; download `kind` whitelisted |
| Security headers | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-XSS-Protection: 1; mode=block`, `Content-Security-Policy` |
| Error handling | Global exception handler catches all unhandled errors; **no stack traces returned to clients** |
| CORS | Restricted to `CORS_ORIGINS` env var; wildcards not permitted |

---

## License notes

The following components have **non-commercial** or **unofficial** licensing:

| Component | Model / API | License | Restriction |
|---|---|---|---|
| ASR | Whisper-small (OpenAI) | MIT | Commercial use OK |
| Translation | NLLB-200-distilled-600M (Meta) | CC-BY-NC 4.0 | **Non-commercial only** |
| Offline TTS | MMS-TTS-KHM (Meta) | CC-BY-NC 4.0 | **Non-commercial only** |
| Edge TTS | Microsoft Edge TTS (unofficial) | Unofficial reverse-engineered API | Unofficial — may break without notice; not for commercial use |
| Azure Speech TTS | Microsoft Azure Cognitive Services | Microsoft commercial licence | Requires Azure subscription |

> [!CAUTION]
> As configured (with NLLB-200 and MMS-TTS), Soundme is for **non-commercial use only**.  
> For commercial deployments: replace NLLB-200 with a commercially-licensed translation API, and MMS-TTS with Azure Speech or another licensed TTS provider.
