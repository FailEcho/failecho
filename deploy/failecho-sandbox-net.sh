#!/bin/sh
# The sandbox VM's link to the host, and the fence around it.
#
# One tap device, owned by the service user so Firecracker can open it
# without privileges, with the host end at 172.16.0.1/30 and the guest at
# 172.16.0.2. IP forwarding stays off, so the guest cannot be routed
# anywhere; the ufw rules below let it reach exactly one thing -- the
# allowlisting proxy on 172.16.0.1:8888 -- and refuse everything else on the
# interface explicitly, ahead of the Cloudflare allow rules that come later
# in the chain.
#
# Idempotent: safe to run on every boot and after every change.
set -eu

TAP=${FAILECHO_SANDBOX_TAP:-fctap0}
USER_=${FAILECHO_SANDBOX_USER:-failecho}
HOST_IP=172.16.0.1
GUEST_IP=172.16.0.2
PROXY_PORT=8888

if [ ! -e "/sys/class/net/$TAP" ]; then
    ip tuntap add dev "$TAP" mode tap user "$USER_"
fi
ip addr replace "$HOST_IP/30" dev "$TAP"
ip link set "$TAP" up

# never forward for the guest, whatever else is configured
sysctl -q -w "net.ipv4.conf.$TAP.forwarding=0"
sysctl -q -w "net.ipv4.conf.$TAP.send_redirects=0"

# ufw keeps rules ordered; insert ours at the top so nothing more general
# (the Cloudflare 80/443 allows are not interface-bound) matches first.
rule_present() { ufw status | grep -qF "$1"; }
if ! rule_present "$HOST_IP $PROXY_PORT/tcp on $TAP"; then
    ufw insert 1 allow in on "$TAP" from "$GUEST_IP" to "$HOST_IP" port "$PROXY_PORT" proto tcp \
        comment 'sandbox guest -> allowlisting proxy' >/dev/null
fi
if ! rule_present "Anywhere on $TAP"; then
    ufw insert 2 deny in on "$TAP" comment 'sandbox guest: nothing else' >/dev/null
fi
if ! ufw status | grep -qF "DENY FWD" ; then
    ufw route deny in on "$TAP" comment 'sandbox guest: no forwarding' >/dev/null || true
fi
echo "sandbox net: $TAP up, $HOST_IP/30, guest $GUEST_IP may reach only $HOST_IP:$PROXY_PORT"
