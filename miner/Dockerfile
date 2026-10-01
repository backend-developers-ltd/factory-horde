FROM ghcr.io/astral-sh/uv@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7 AS uv
FROM docker.io/library/python@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d AS builder
COPY --from=uv /uv /bin/uv
WORKDIR /build
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy
COPY pyproject.toml uv.lock miner.py ./
RUN uv sync --frozen --no-default-groups --no-editable

FROM docker.io/library/python@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1
USER 1000:1000
LABEL org.opencontainers.image.source="https://github.com/backend-developers-ltd/factory-horde"
ENTRYPOINT ["miner"]
