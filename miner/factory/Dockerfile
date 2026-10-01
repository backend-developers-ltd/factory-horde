FROM docker.io/library/node@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6
WORKDIR /opt/pi
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY --chmod=0555 factory.mjs /usr/local/bin/factory-horde-factory
ENV HOME=/tmp
USER 1000:1000
LABEL org.opencontainers.image.source="https://github.com/backend-developers-ltd/factory-horde"
LABEL org.opencontainers.image.description="FactoryHorde baseline factory; fixed output with no inference"
ENTRYPOINT ["/usr/local/bin/factory-horde-factory"]
