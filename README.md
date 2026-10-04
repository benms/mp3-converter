# Sounddrop

Turn a YouTube video into an MP3. Paste a link, choose 128, 192 or 320 kbps, and download the file.

- **Frontend:** React and Vite single-page app (`frontend/`), hosted on Vercel.
- **Backend:** FastAPI service (`backend/`) that runs yt-dlp and FFmpeg in a Docker container, hosted on Render.

No accounts and no saved history. Jobs live in memory and files are deleted automatically.

> Convert only audio you own or have permission to download.

## How it works

1. The browser checks `GET /api/health`. On Render's free plan the server may take about a minute to wake up.
2. `POST /api/jobs` checks the link, accepts only a single YouTube video, and puts the job in a queue (one conversion runs at a time).
3. The worker inspects the video and rejects live streams and videos over the length limit. It then downloads the best audio stream with yt-dlp, encodes it with FFmpeg, and verifies the result with ffprobe.
4. The browser checks `GET /api/jobs/{id}` every 2 seconds, then downloads the MP3 from `GET /api/jobs/{id}/download`.
5. Finished and failed jobs are removed after `SOUNDDROP_FILE_TTL_SECONDS`. All jobs are lost when the server restarts.

### Safeguards

Every subprocess has a limit on output, file size (`RLIMIT_FSIZE`), disk use and run time, and its whole process group is killed when it finishes. Links are reduced to `https://www.youtube.com/watch?v=<id>` before reaching yt-dlp. Error messages never include tool output or server paths. Each client IP is limited to a set number of conversions per hour.

## Project layout

```
backend/
  app/
    main.py        FastAPI app, routes, error responses
    jobs.py        In-memory queue, worker, rate limits, expiry sweeper
    converter.py   yt-dlp download, FFmpeg encode, storage checks
    processes.py   Subprocess runner and error classification
    urls.py        YouTube URL normalisation, safe download filenames
    config.py      Settings from SOUNDDROP_* environment variables
    child.py       Applies the file-size limit before running a tool
  tests/           pytest suite
  Dockerfile       runtime, test, and production stages
  start.py         Uvicorn entry point
frontend/
  src/App.tsx      The whole UI
  src/api.ts       API client and URL validation
  tests/           Playwright tests (desktop and mobile)
compose.yaml       Local backend container
render.yaml        Render blueprint for the backend
```

## Local development

### Backend

The backend uses Linux process controls (`os.killpg`, `resource`). On Windows, run it in Docker:

```bash
docker compose up --build
```

The API is then available at http://127.0.0.1:8000 and allows requests from the Vite dev server at `localhost:5173`.

On Linux or macOS you can run it directly. You need Python 3.11+, FFmpeg (with ffprobe) and Deno on your `PATH`:

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python start.py
```

### Frontend

You need Node.js 22.12+.

```bash
cd frontend
npm ci
cp .env.example .env.local
npm run dev
```

Open http://localhost:5173. If `VITE_SOUNDDROP_API_URL` is not set, the page shows "The converter is not connected yet" and conversion is disabled.

## Tests

Run the backend tests and linter in the Docker `test` stage. This stage includes FFmpeg, so the real MP3 encoding tests run too:

```bash
docker build --target test -t sounddrop-test backend
docker run --rm sounddrop-test
docker run --rm sounddrop-test sh -c "python -m ruff check --no-cache . && python -m ruff format --no-cache --check ."
```

Frontend type-check, build and end-to-end tests. Playwright starts its own dev servers on ports 5173 and 5174, so stop `npm run dev` first. The API is mocked.

```bash
cd frontend
npx playwright install chromium
npm run build
npm test
```

## Configuration

### Backend

| Variable | Default | Description |
|---|---|---|
| `SOUNDDROP_ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated exact origins allowed by CORS; no wildcards or paths. |
| `SOUNDDROP_PORT` | `PORT`, then `8000` | Listening port. Hosting platforms usually set `PORT`. |
| `SOUNDDROP_FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxy addresses allowed to supply the client IP via `X-Forwarded-For`. Rate limits are per client IP, so if the proxy is not trusted, every visitor shares one limit. Use `*` when only the proxy can reach the server, as on Render. |
| `SOUNDDROP_DATA_DIR` | `/tmp/sounddrop` | Temporary working folder. Only folders the app created are cleaned at startup. |
| `SOUNDDROP_MAX_DURATION_SECONDS` | `3600` | Longest video accepted. |
| `SOUNDDROP_MAX_SOURCE_MB` | `256` | Largest downloaded audio source. |
| `SOUNDDROP_MAX_STORAGE_MB` | `1024` | Total temporary storage cap. |
| `SOUNDDROP_JOB_TIMEOUT_SECONDS` | `1200` | Time limit for a whole conversion. |
| `SOUNDDROP_FILE_TTL_SECONDS` | `3600` | How long finished and failed jobs are kept. |
| `SOUNDDROP_MAX_QUEUE_SIZE` | `10` | Jobs that can wait in the queue. |
| `SOUNDDROP_RATE_LIMIT_PER_HOUR` | `10` | Conversions per client IP per hour. |
| `SOUNDDROP_YTDLP_COOKIES_FILE` | *(unset)* | Optional path to a Netscape-format `cookies.txt` from a YouTube account. Each job gets its own writable copy. The server refuses to start if the file is missing or unreadable. |
| `SOUNDDROP_YTDLP_PROXY` | *(unset)* | Optional `http(s)://` or `socks5(h)://` proxy URL for yt-dlp. Credentials are masked in logs. |

The frontend reads `SOUNDDROP_MAX_DURATION_SECONDS` and `SOUNDDROP_FILE_TTL_SECONDS` from `/api/health` for its text, so nothing needs changing there.

### Frontend

| Variable | Description |
|---|---|
| `VITE_SOUNDDROP_API_URL` | Backend origin, for example `https://sounddrop-api.onrender.com`. Origin only, no path. Production builds require `https`. It is set at build time, so rebuild after changing it. |

## Deployment

### Backend on Render

`render.yaml` defines a free Docker web service (`sounddrop-api`, Frankfurt) built from `backend/Dockerfile`. Its health check uses `/api/health`. Create the service from the blueprint, then set `SOUNDDROP_ALLOWED_ORIGINS` in the dashboard to the frontend's origin, for example `https://sounddrop.vercel.app`.

Free instances sleep when idle, and the frontend shows a "connecting" state while the server wakes up.

#### YouTube blocking on Render

YouTube often refuses downloads from cloud server IPs, including Render's. Users then see the `upstream_restricted` error. The Render log shows YouTube's actual reason in a `tool_failed … detail=ERROR: …` line, for example a "Sign in to confirm you're not a bot" check or an HTTP 403. yt-dlp has two standard ways past this, and the server supports both:

- **Cookies from a YouTube account.** Export a `cookies.txt` (Netscape format) from a browser signed in to YouTube, ideally a separate account in a private window that you then close. In Render, go to **Environment → Secret Files**, add it as `youtube-cookies.txt`, and set `SOUNDDROP_YTDLP_COOKIES_FILE=/etc/secrets/youtube-cookies.txt`. Cookies expire and YouTube rotates them, so you'll need to export them again from time to time.
- **A proxy.** Set `SOUNDDROP_YTDLP_PROXY`, for example to a residential proxy service URL. Datacenter proxies are usually blocked as well.

Both are against YouTube's terms of service. An account whose cookies are used this way can be flagged or banned, so don't use your main account. When the server starts, the log line `ytdlp_access cookies=on|off proxy=on|off` confirms what is active.

### Alternative: backend on your own machine (Cloudflare quick tunnel)

Home connections are rarely blocked by YouTube. As an alternative to Render, or as a temporary fallback, you can serve the live site from your own machine by starting the backend together with a [Cloudflare quick tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/):

```bash
docker compose --profile tunnel up -d
docker compose logs tunnel | grep trycloudflare.com
```

The second command prints the public `https://…trycloudflare.com` URL. Set it as `VITE_SOUNDDROP_API_URL` in Vercel, then redeploy the frontend. No account, router ports or certificates are needed, and your home IP stays hidden.

- **The URL changes whenever the `tunnel` container restarts**, including after a reboot, so Vercel must then be updated and redeployed. For a permanent URL, use a named tunnel with a Cloudflare account and domain.
- Conversions only work while your machine and Docker are running.
- `compose.yaml` already allows `https://mp3-converter-gray.vercel.app`. It trusts forwarded client IPs only from the tunnel container (fixed address `172.28.250.10`), so each visitor keeps their own rate limit.
- `docker compose up` without the profile runs the backend on `127.0.0.1:8000` only.

### Frontend on Vercel

Import the repository with **Root Directory** set to `frontend`. `vercel.json` sets the build command, output folder and security headers. Add `VITE_SOUNDDROP_API_URL` with the backend URL (Render or tunnel) as a **Config** variable, not a Secret: Vercel refuses `VITE_` values stored as secrets because they end up in browser code. Then redeploy.

## API

Errors use the format `{"detail": {"code": "...", "message": "..."}}`. The message is safe to show to users. The exception is request-body validation errors (`422`), which use FastAPI's standard `detail` list.

| Method and path | Response |
|---|---|
| `GET /api/health` | `200` when ready, `503` while the tools or worker are unavailable. Body: `{status, tools, limits: {max_duration_seconds, file_ttl_seconds}}`. |
| `POST /api/jobs` | Body `{"url": "...", "bitrate": 128 \| 192 \| 320}` returns `202` with the job status. Possible errors: `422` (`invalid_url`, or a body validation error), `429` (`rate_limited`), `503` (`queue_full`, `storage_full`, `tools_unavailable`). |
| `GET /api/jobs/{id}` | Job status: `stage` (`queued`, `inspecting`, `downloading`, `converting`, `ready`, `failed`), `progress`, `title`, `duration`, `queue_position`, `expires_at`, `error`. Returns `404` (`job_missing`) after expiry or a restart. |
| `GET /api/jobs/{id}/download` | The MP3 as an attachment. Returns `409` (`not_ready`) before the job is ready, `404` once it has gone. |

When a job fails, its `error.code` is one of: `invalid_url`, `live_video`, `unknown_duration`, `video_too_long`, `unavailable`, `upstream_restricted`, `source_too_large`, `storage_full`, `output_limit`, `invalid_output`, `timeout`, `conversion_failed`.
