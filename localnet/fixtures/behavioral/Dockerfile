FROM docker.io/library/python@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8
COPY --chmod=0555 factory.py /usr/local/bin/factory-horde-factory
COPY main.py /fixture/main.py
USER 1000:1000
ENTRYPOINT ["/usr/local/bin/factory-horde-factory"]
