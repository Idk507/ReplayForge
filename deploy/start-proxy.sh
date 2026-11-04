#!/bin/sh
set -eu
origin=${REPLAYFORGE_PUBLIC_ORIGIN:-}
if [ "${REPLAYFORGE_DEPLOYMENT_MODE:-}" != PUBLIC_DEPLOYMENT ] ||
   [ "${REPLAYFORGE_WEB_AUTH_MODE:-}" != github ] ||
   [ "${REPLAYFORGE_AUTH_API_ORIGIN:-}" != "$origin" ] ||
   [ "${REPLAYFORGE_AUTH_WEB_ORIGIN:-}" != "$origin" ]; then
  echo PUBLIC_PROXY_CONFIG_INVALID >&2
  exit 1
fi
case "$origin" in
  *[!a-z0-9./:-]*) echo PUBLIC_ORIGIN_INVALID >&2; exit 1 ;;
esac
if ! printf '%s\n' "$origin" | grep -Eq '^https://[a-z0-9]+([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]+([a-z0-9-]*[a-z0-9])?)+$'; then
  echo PUBLIC_ORIGIN_INVALID >&2
  exit 1
fi
host=${origin#https://}
if [ "${#host}" -gt 253 ] || printf '%s\n' "$host" | grep -Eq '^[0-9]+(\.[0-9]+)+$'; then
  echo PUBLIC_ORIGIN_INVALID >&2
  exit 1
fi
sed "s/__PUBLIC_HOST__/$host/g" /app/deploy/nginx.conf.template > /tmp/replayforge-nginx.conf
nginx -t -c /tmp/replayforge-nginx.conf
exec nginx -c /tmp/replayforge-nginx.conf -g 'daemon off;'
