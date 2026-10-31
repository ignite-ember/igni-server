#!/usr/bin/env bash
#
# Install the chart on a real cluster and see whether igni comes up.
#
#   tools/kind/install.sh          # create, install, verify, delete
#   tools/kind/install.sh --keep   # leave the cluster running to poke at
#
# F1. `tests/test_the_helm_chart_deploys_igni.py` renders the chart and
# asserts on the manifests; `helm lint` reads Chart.yaml; `kubeconform`
# checks every field against the Kubernetes schemas. All three are
# static, and all three passed on a chart that had never been applied to
# anything. F140 is the precedent worth remembering: the Caddyfile this
# product ships was rejected by `caddy validate` from the day it was
# written, and nobody knew because nothing ever started it.
#
# So this does the whole thing: builds the two images, side-loads them,
# deploys a throwaway Postgres and Redis, installs the chart, waits for
# the migration Job and both rollouts, and asks the API for
# `/health`, `/ready` and `/version` through a port-forward.
#
# The datastores are external by design (see values.yaml, "The data
# stores, both external"), so the harness supplies them —
# `tools/kind/datastores.yaml`, one pod each, emptyDir, thrown away with
# the cluster.
set -euo pipefail

CLUSTER="${IGNI_KIND_CLUSTER:-igni-f1}"
NAMESPACE="${IGNI_KIND_NAMESPACE:-igni}"
RELEASE=igni
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

step() { printf '\n\033[1m→ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

for tool in kind kubectl helm docker; do
  command -v "$tool" >/dev/null || fail "$tool is not installed"
done

cleanup() {
  local code=$?
  if [[ $code -ne 0 ]]; then
    # Diagnostics *before* teardown, because the reason a rollout failed
    # is in the events and the pod logs and both die with the cluster.
    printf '\n\033[31m--- pods ---\033[0m\n' >&2
    kubectl -n "$NAMESPACE" get pods -o wide 2>&1 | sed 's/^/    /' >&2 || true
    printf '\033[31m--- recent events ---\033[0m\n' >&2
    kubectl -n "$NAMESPACE" get events --sort-by=.lastTimestamp 2>&1 | tail -25 | sed 's/^/    /' >&2 || true
    for pod in $(kubectl -n "$NAMESPACE" get pods -o name 2>/dev/null); do
      printf '\033[31m--- %s ---\033[0m\n' "$pod" >&2
      kubectl -n "$NAMESPACE" logs "$pod" --all-containers --tail=40 2>&1 | sed 's/^/    /' >&2 || true
    done
  fi
  if [[ $KEEP -eq 1 ]]; then
    printf '\nCluster %s left running. `kind delete cluster --name %s` when done.\n' "$CLUSTER" "$CLUSTER"
  else
    kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
  fi
  exit $code
}
trap cleanup EXIT

step "Creating cluster ${CLUSTER}"
kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
kind create cluster --name "$CLUSTER" --wait 120s

step "Building the images CI builds"
docker build -q -f "${ROOT}/containers/be/Dockerfile" -t igni-api:kind "${ROOT}" >/dev/null
docker build -q -f "${ROOT}/portal/Dockerfile" -t igni-portal:kind "${ROOT}/portal" >/dev/null

step "Side-loading them into the cluster"
kind load docker-image igni-api:kind igni-portal:kind --name "$CLUSTER"

step "Deploying the throwaway Postgres and Redis"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NAMESPACE" apply -f "${HERE}/datastores.yaml"
kubectl -n "$NAMESPACE" rollout status deploy/pg --timeout=180s
kubectl -n "$NAMESPACE" rollout status deploy/redis --timeout=120s

step "helm install"
helm install "$RELEASE" "${ROOT}/charts/igni" \
  --namespace "$NAMESPACE" \
  --values "${HERE}/values.yaml" \
  --wait --timeout 5m ||
  fail "helm install did not converge"

step "The migration job"
# Named by the chart's fullname helper. Found rather than assumed, so a
# rename does not turn this into a silent skip.
migrate_job=$(kubectl -n "$NAMESPACE" get jobs -o name | grep migrate || true)
[[ -n "$migrate_job" ]] || fail "no migration job was created"
kubectl -n "$NAMESPACE" wait --for=condition=complete "$migrate_job" --timeout=180s ||
  fail "the migration job did not complete"

step "Rollouts"
for deploy in $(kubectl -n "$NAMESPACE" get deploy -o name | grep -v -e '/pg$' -e '/redis$'); do
  kubectl -n "$NAMESPACE" rollout status "$deploy" --timeout=180s
done

step "Asking the API for its own opinion"
# The API and the portal get separate Services; the API one is
# `<release>-api`. Port-forwarding `svc/<release>` found nothing and
# reported it as "/health did not answer", which is the harness
# blaming the product for its own mistake.
kubectl -n "$NAMESPACE" port-forward "svc/${RELEASE}-api" 18777:7777 >/dev/null 2>&1 &
forward=$!
# shellcheck disable=SC2064
trap "kill $forward 2>/dev/null || true" RETURN
for _ in $(seq 1 30); do
  curl -fsS --max-time 2 http://127.0.0.1:18777/health >/dev/null 2>&1 && break
  sleep 1
done

for path in /health /ready /version; do
  body=$(curl -fsS --max-time 5 "http://127.0.0.1:18777${path}") ||
    fail "${path} did not answer through the service"
  printf '    %-10s %s\n' "$path" "$body"
done

# `/ready` distinguishes liveness from readiness (AV-1), so its body has
# to say the dependencies are up rather than merely return 200.
ready=$(curl -fsS --max-time 5 http://127.0.0.1:18777/ready)
# The keys are `postgres` and `redis` — checked against the real
# response rather than guessed, because a grep for a key that does not
# exist fails in the same way as a dependency that is down.
grep -q '"postgres": *"ok"' <<<"$ready" || fail "/ready does not report postgres as ok: ${ready}"
grep -q '"redis": *"ok"' <<<"$ready" || fail "/ready does not report redis as ok: ${ready}"

kill $forward 2>/dev/null || true

step "And the portal"
# Worth its own check: F105 found the portal container would not start
# unless the API was already resolvable, and that its default upstream
# named a service that had been renamed. Both are exactly the kind of
# thing a rendered manifest cannot show.
kubectl -n "$NAMESPACE" port-forward "svc/${RELEASE}-portal" 18300:3000 >/dev/null 2>&1 &
portal_forward=$!
for _ in $(seq 1 30); do
  curl -fsS --max-time 2 -o /dev/null http://127.0.0.1:18300/ 2>/dev/null && break
  sleep 1
done
curl -fsS --max-time 5 -o /tmp/igni-portal-index.html http://127.0.0.1:18300/ ||
  fail "the portal did not serve its index through the service"
# The bundle, not just a 200: nginx answers for an empty root happily.
grep -qi '<div id="root"' /tmp/igni-portal-index.html ||
  fail "the portal served something that is not the app: $(head -c 200 /tmp/igni-portal-index.html)"
printf '    %-10s served, and it is the app\n' /
kill $portal_forward 2>/dev/null || true

printf '\n\033[32m✓ the chart installs, migrates and serves\033[0m\n'
