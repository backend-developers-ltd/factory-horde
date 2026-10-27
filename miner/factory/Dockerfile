# node:24.22.1-bookworm-slim
FROM docker.io/library/node@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205
WORKDIR /opt/pi
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY --chmod=0555 factory.mjs /usr/local/bin/factory-horde-factory
ENV HOME=/tmp
USER 1000:1000
LABEL org.opencontainers.image.source="https://github.com/backend-developers-ltd/factory-horde"
LABEL org.opencontainers.image.description="FactoryHorde baseline factory; fixed output with no inference"
ENTRYPOINT ["/usr/local/bin/factory-horde-factory"]
