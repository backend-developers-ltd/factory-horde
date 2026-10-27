# python:3.14.9-slim-trixie
FROM docker.io/library/python@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8
COPY --chmod=0555 factory.py /usr/local/bin/factory-horde-factory
COPY --chmod=0555 judge.py /usr/local/bin/factory-horde-judge
ARG FIXTURE_PROFILE=lifecycle
ENV FIXTURE_PROFILE=$FIXTURE_PROFILE
ENV PYTHONUNBUFFERED=1
USER 1000:1000
LABEL org.opencontainers.image.source="https://github.com/backend-developers-ltd/factory-horde"
LABEL org.opencontainers.image.description="Localnet-only executor lifecycle fault fixture"
ENTRYPOINT ["/usr/local/bin/factory-horde-factory"]
