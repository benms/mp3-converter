FROM denoland/deno:bin-2.5.4 AS deno
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SOUNDDROP_DATA_DIR=/tmp/sounddrop \
    DENO_DIR=/tmp/deno

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app
COPY --from=deno /deno /usr/local/bin/deno
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY start.py ./start.py
RUN mkdir -p /tmp/sounddrop /tmp/deno && chown -R app:app /tmp/sounddrop /tmp/deno
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import start,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{start.port()}/api/health',timeout=4)"
CMD ["python", "start.py"]

FROM runtime AS test
USER root
COPY requirements-dev.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests ./tests
USER app
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

FROM runtime AS production
