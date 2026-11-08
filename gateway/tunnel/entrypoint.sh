#!/bin/sh
# Reverse SSH tunnel: the VPS listens on 127.0.0.1:<port> for each forward and leads back to services here.
# FORWARDS is a space-separated list of "remote_bind:remote_port:target_host:target_port".
# Endless loop: reconnects after any drop (network change, VPN toggle, VPS reboot).
set -u

: "${VPS_HOST:?}" "${VPS_USER:?}" "${FORWARDS:?}"
: "${VPS_PORT:=22}" "${RETRY_DELAY:=10}"

# ssh wants the key readable by the owner only; files from a Windows drive are 0777
KEY=/tmp/tunnel_key
cp /config/tunnel_key "$KEY" && chmod 600 "$KEY" || exit 1

set --
for f in $FORWARDS; do
  set -- "$@" -R "$f"
done

while true; do
  echo "$(date -Iseconds) connecting to $VPS_USER@$VPS_HOST:$VPS_PORT, forwards: $FORWARDS"
  ssh -N -T \
    -i "$KEY" -p "$VPS_PORT" \
    -o IdentitiesOnly=yes \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=yes \
    -o UserKnownHostsFile=/config/known_hosts \
    -o ConnectTimeout=20 \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 \
    -o TCPKeepAlive=yes \
    -o ExitOnForwardFailure=yes \
    "$@" \
    "$VPS_USER@$VPS_HOST"
  rc=$?
  echo "$(date -Iseconds) ssh exited with code $rc, retrying in ${RETRY_DELAY}s"
  sleep "$RETRY_DELAY"
done
