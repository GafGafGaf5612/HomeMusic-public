# Build context: embedder/ (see docker-compose.yml). PyTorch wheels bring their own CUDA 12.8 libraries;
# the driver comes from the host (Docker Desktop + WSL2, `deploy.resources.reservations.devices`).
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    HF_HOME=/models/hf HF_HUB_DISABLE_TELEMETRY=1 \
    UV_NO_CACHE=1 UV_CONCURRENT_INSTALLS=2 UV_CONCURRENT_DOWNLOADS=4
# ~7 GB of CUDA wheels: no uv cache in the layer and few parallel unpacks — the build ran the WSL VM out of memory
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra gpu --no-install-project
COPY . ./
RUN rm -rf /app/.venv && uv sync --frozen --no-dev --extra gpu
EXPOSE 8090
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8090", "--no-access-log"]
