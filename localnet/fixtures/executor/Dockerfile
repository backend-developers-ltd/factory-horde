FROM docker.io/library/python@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d
COPY --chmod=0555 factory.py /usr/local/bin/factory-horde-factory
COPY --chmod=0555 judge.py /usr/local/bin/factory-horde-judge
ARG FIXTURE_PROFILE=lifecycle
ENV FIXTURE_PROFILE=$FIXTURE_PROFILE
ENV PYTHONUNBUFFERED=1
USER 1000:1000
LABEL org.opencontainers.image.source="https://github.com/backend-developers-ltd/factory-horde"
LABEL org.opencontainers.image.description="Localnet-only executor lifecycle fault fixture"
ENTRYPOINT ["/usr/local/bin/factory-horde-factory"]
