ARG DEBIAN_IMAGE=debian:trixie-slim@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad
FROM ${DEBIAN_IMAGE}

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates file pkg-config libfreetype-dev \
    && rm -rf /var/lib/apt/lists/*

COPY vendor/bcm2835-1.60.tar.gz /tmp/bcm2835-1.60.tar.gz
RUN printf '%s  %s\n' \
      333e7ceee895e910c29098074773ee86bcab4a82c2af0cf083c4533767e52d27 \
      /tmp/bcm2835-1.60.tar.gz | sha256sum --check - \
    && cd /tmp && tar -xzf bcm2835-1.60.tar.gz \
    && cd bcm2835-1.60 && ./configure && make -j2 && make install \
    && rm -rf /tmp/bcm2835-1.60 /tmp/bcm2835-1.60.tar.gz

# bcm2835's make check touches real GPIO and is intentionally a device-only check.
COPY docker/build-display.sh /usr/local/bin/build-display
WORKDIR /workspace
ENTRYPOINT ["/bin/sh", "/usr/local/bin/build-display"]
