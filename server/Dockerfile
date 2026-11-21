# SPDX-License-Identifier: Apache-2.0
# Eddy: a thing's own git server (docs/EDDY_CONTRACT.md, section 14).
#
#   docker build -f server/Dockerfile -t eddy .
#   docker run -d --name eddy --restart unless-stopped \
#          -p 127.0.0.1:8765:8765 -v eddy-data:/srv/eddy eddy
#   docker exec eddy eddy token <login>          # prints a token, once
#
# Ubuntu 22.04 on purpose: its git is 2.34.1, the version the pre-receive hook
# was verified on by a real push (server/pre-receive's docstring), and its
# python3 is 3.10, the oldest the spine supports. Rejected: python:3.12-slim
# (git 2.39, whose trailer heuristics the hook's E16 scan has not been held
# against: section 13, point 12).
FROM ubuntu:22.04
RUN apt-get update \
 && apt-get install -y --no-install-recommends git python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# The hook runs as this user and imports nopekit from /opt/nopekit, which it
# cannot write: the bytecode is compiled here, once, so a push does not pay
# for compiling the spine and nothing in the image changes after it is built.
RUN useradd --system --create-home --home-dir /srv/eddy --shell /usr/sbin/nologin eddy
COPY src /opt/nopekit/src
COPY packs /opt/nopekit/packs
COPY server /opt/nopekit/server
RUN python3 -m compileall -q /opt/nopekit/src /opt/nopekit/server \
 && printf '#!/bin/sh\nexec python3 /opt/nopekit/server/eddy.py "$@"\n' > /usr/local/bin/eddy \
 && chmod 755 /usr/local/bin/eddy
ENV EDDY_ROOT=/srv/eddy
USER eddy
VOLUME /srv/eddy
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2)"
ENTRYPOINT ["eddy"]
# Inside the container the server must listen on every interface; what the
# host exposes is the `-p 127.0.0.1:8765:8765` above. --public-read: on a
# loopback-only port the screens open without a token; a push always needs one.
CMD ["serve", "--host", "0.0.0.0", "--port", "8765", "--public-read"]
