# n8n plus the tools this portfolio's Execute Command nodes call:
#   bash + FFmpeg/ffprobe (project 4), Python 3 + Pillow (projects 5 and 6).
#
# Used by the repo-root docker-compose.yml (your everyday n8n) and by tests/e2e,
# so both run exactly the same image.
#
# The official n8n image is a hardened Alpine build with no package manager, so the
# tools are installed in a stock Alpine stage of the same release (same musl libc) and
# copied across. Rebuild after an n8n upgrade:  docker compose build --pull
FROM alpine:3.24 AS tools
RUN apk add --no-cache bash ffmpeg python3 py3-pillow font-dejavu \
 && mkdir /out \
 && cp /usr/bin/ffmpeg /usr/bin/ffprobe /usr/bin/python3* /out/

FROM docker.n8n.io/n8nio/n8n:latest
USER root
COPY --from=tools /usr/lib/ /usr/lib/
COPY --from=tools /lib/ /lib/
COPY --from=tools /usr/share/fonts/ /usr/share/fonts/
COPY --from=tools /out/ /usr/bin/
COPY --from=tools /bin/bash /bin/bash
USER node
