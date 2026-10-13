FROM ghcr.io/home-assistant/base:latest
ARG BUILD_ARCH
ARG BUILD_VERSION

RUN apk add --no-cache \
    iproute2 \
    python3 \
    vnstat

WORKDIR /app

COPY run.sh /run.sh
COPY vnstat-cli /usr/local/bin/vnstat-cli
COPY app /app

RUN chmod a+x /run.sh /usr/local/bin/vnstat-cli

LABEL io.hass.type="app" io.hass.arch="${BUILD_ARCH}" io.hass.version="${BUILD_VERSION}"
CMD ["/run.sh"]
