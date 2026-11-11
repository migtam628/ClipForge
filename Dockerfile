# yt-dlp requires a JavaScript runtime to solve modern YouTube player challenges.
# Use the official multi-arch Deno binary image instead of running a remote install script.
FROM denoland/deno:bin-2.9.8 AS deno-bin

FROM python:3.12-slim
COPY --from=deno-bin /deno /usr/local/bin/deno
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates fonts-dejavu-core libgomp1 && rm -rf /var/lib/apt/lists/*
RUN deno --version
WORKDIR /app
COPY pyproject.toml README.md ./
COPY clipforge ./clipforge
RUN pip install --no-cache-dir ".[ai]" && useradd -m -u 10001 clipforge && mkdir -p /data && chown clipforge:clipforge /data
USER clipforge
ENV CLIPFORGE_DATA_DIR=/data CLIPFORGE_HOST=0.0.0.0 CLIPFORGE_PORT=8765
EXPOSE 8765
CMD ["clipforge"]
