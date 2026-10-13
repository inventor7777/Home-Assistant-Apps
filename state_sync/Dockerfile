FROM ghcr.io/home-assistant/base:3.23
ARG BUILD_ARCH
ARG BUILD_VERSION
RUN apk add --no-cache python3 py3-aiohttp py3-yaml
WORKDIR /app
COPY app ./app
COPY run.sh /run.sh
RUN chmod +x /run.sh
LABEL io.hass.type="app" io.hass.arch="${BUILD_ARCH}" io.hass.version="${BUILD_VERSION}"
CMD ["/run.sh"]
