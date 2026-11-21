#!/bin/bash
# Start a localhost.run tunnel for the local MCP server on port 8000.
# The VM has no public IP, so SSH goes out through the egress proxy
# (read from $https_proxy / $HTTPS_PROXY, auth included) via socat.
# The assigned URL (https://xxx.lhr.life) ROTATES on every restart —
# the other Muse must be given the new URL each time this restarts.
# Usage: ./start-tunnel.sh   (runs in foreground; log it from outside)
set -u
PROXY_URL="${https_proxy:-${HTTPS_PROXY:-}}"
if [ -z "$PROXY_URL" ]; then
  echo "no https_proxy in env — cannot reach the egress proxy" >&2
  exit 1
fi
AUTH_HOST="${PROXY_URL#http://}"
AUTH_HOST="${AUTH_HOST#https://}"
AUTH="${AUTH_HOST%%@*}"
HOSTPORT="${AUTH_HOST#*@}"
PROXY_HOST="${HOSTPORT%%:*}"
PROXY_PORT="${HOSTPORT##*:}"
TARGET="localhost.run"
PC="socat - PROXY:${PROXY_HOST}:${TARGET}:22,proxyport=${PROXY_PORT},proxyauth=${AUTH}"
# NOTE: no -N here — localhost.run prints the assigned public URL only
# inside the remote shell session; with -N there is no session channel
# and the URL never appears in the log.
exec ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -o ServerAliveInterval=30 -o ExitOnForwardFailure=yes \
  -o ProxyCommand="$PC" \
  -R 80:localhost:8000 "nokey@${TARGET}"
