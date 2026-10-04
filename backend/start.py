import os

import uvicorn

from app.config import env


def port() -> int:
    # Hosting platforms such as Render inject PORT; SOUNDDROP_PORT takes precedence.
    return int(os.getenv("SOUNDDROP_PORT") or os.getenv("PORT") or "8000")


if __name__ == "__main__":
    # Rate limits are per client IP, so behind a reverse proxy this must include the proxy's
    # address (use "*" when only the proxy can reach the server), or every visitor shares a limit.
    forwarded_allow_ips = env("FORWARDED_ALLOW_IPS", "127.0.0.1")
    print(f"Trusting forwarded client addresses from: {forwarded_allow_ips}", flush=True)
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port(),
        workers=1,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips=forwarded_allow_ips,
        limit_concurrency=100,
        timeout_graceful_shutdown=10,
    )
