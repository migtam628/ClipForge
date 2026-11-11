FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY clipforge ./clipforge
RUN pip install --no-cache-dir . && useradd -m -u 10001 clipforge && mkdir -p /data && chown clipforge:clipforge /data
USER clipforge
ENV CLIPFORGE_DATA_DIR=/data CLIPFORGE_HOST=0.0.0.0 CLIPFORGE_PORT=8765
EXPOSE 8765
CMD ["clipforge"]
