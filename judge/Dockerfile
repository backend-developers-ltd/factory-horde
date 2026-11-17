# node:24-bookworm-slim
FROM docker.io/library/node@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS claude
RUN npm install -g --no-audit --no-fund @anthropic-ai/claude-code@2.1.325

# python:3.14.9-slim-trixie
FROM docker.io/library/python@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8
# Claude Code ships as one self-contained binary.
COPY --from=claude /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe /usr/local/bin/claude
COPY --chmod=0555 judge.py /usr/local/bin/factory-horde-judge
# The root filesystem is read-only at run time; /tmp is the only writable scratch space.
ENV HOME=/tmp/home \
    PYTHONUNBUFFERED=1 \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
USER 1000:1000
LABEL org.opencontainers.image.source="https://github.com/backend-developers-ltd/factory-horde"
LABEL org.opencontainers.image.description="FactoryHorde judge with frozen review criteria and explicit offline fixture scoring"
ENTRYPOINT ["/usr/local/bin/factory-horde-judge"]
