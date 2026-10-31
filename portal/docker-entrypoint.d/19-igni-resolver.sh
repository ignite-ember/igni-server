#!/bin/sh
# Give nginx a DNS resolver, taken from whatever the platform provides.
#
# The /v1 proxy in nginx.conf uses a *variable* upstream so the host is
# resolved per request rather than once at startup — without that, this
# container refuses to start whenever the API is not already resolvable,
# and exits with `host not found in upstream`, which reads like a
# misconfiguration rather than an ordering problem.
#
# Variable upstreams need a `resolver`, and the address differs by
# platform: Docker's embedded DNS is 127.0.0.11, a Kubernetes cluster's
# is whatever the kubelet wrote. Both put it in /etc/resolv.conf, so
# that is where this reads it from.
#
# Written as a file rather than substituted into the template: the base
# image exports `NGINX_LOCAL_RESOLVERS` for this purpose and its
# envsubst step does not expand it, leaving `${NGINX_LOCAL_RESOLVERS}`
# in the rendered config and nginx refusing to load. Measured, not
# assumed. `resolver local=on` would also say it in one word and this
# build rejects that too.
#
# Files in conf.d are included at `http` level, so a `resolver` here is
# inherited by every server and location below it.
set -eu

CONF_DIR=/etc/nginx/conf.d
RESOLVERS=$(awk '$1 == "nameserver" { if ($2 ~ /:/) print "[" $2 "]"; else print $2 }' /etc/resolv.conf | tr '\n' ' ')

if [ -z "${RESOLVERS% }" ]; then
    # No nameserver at all. Say so and start anyway: the application
    # itself is static files and does not need DNS, so the right
    # outcome is a working portal whose /v1 proxy fails loudly rather
    # than a container that will not boot.
    echo "19-igni-resolver.sh: no nameserver in /etc/resolv.conf; /v1 proxying will fail"
    exit 0
fi

echo "resolver ${RESOLVERS% } ipv6=off valid=10s;" > "$CONF_DIR/igni-resolver.conf"
echo "19-igni-resolver.sh: resolver ${RESOLVERS% }"
