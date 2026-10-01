FROM docker.io/library/python@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d
COPY --chmod=0555 judge.py /usr/local/bin/factory-horde-judge
ENV PYTHONUNBUFFERED=1
USER 1000:1000
LABEL org.opencontainers.image.source="https://github.com/backend-developers-ltd/factory-horde"
LABEL org.opencontainers.image.description="FactoryHorde prototype judge; file checks and random fixture scores"
ENTRYPOINT ["/usr/local/bin/factory-horde-judge"]
