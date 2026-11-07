#!/usr/bin/env bash
#
# Rehearse an air-gapped install of the free base, end to end, and show
# that nothing in the cluster reaches out.
#
#   tools/airgap-rehearsal.sh          # bundle, cut off, import, install, smoke, report, delete
#   tools/airgap-rehearsal.sh --keep   # leave the cluster and registry running to poke at
#
# T11. docs/OFFLINE_UPDATES.md says an air-gapped install never contacts
# the internet; tools/kind/install.sh shows the chart installs on a
# cluster that *has* the internet. Neither shows the claim. This does:
#
#   1. Connected, as Ignite Ember's CD is: builds igni-api and igni-portal,
#      pushes them with the third-party images the install needs
#      (Postgres, Redis) to a throwaway staging registry, signs each with
#      a TEST cosign key and attests a CycloneDX SBOM (syft), exports them
#      with `tools/release/igni-bundle export-image`, packages the chart
#      and seals the bundle with the same TEST cosign key.
#   2. The air gap: a kind cluster (one control-plane node) on its own
#      Docker network, a registry on that network, and netfilter rules in
#      the node's network namespace — where every pod and every node
#      process (kubelet, containerd, CoreDNS's upstream) sends from — that
#      drop and log (NFLOG) anything not addressed to the cluster, the
#      registry or the node network. Docker's embedded DNS and the
#      network gateway (the host) count as outside. Egress is checked
#      first from the node and from a pod: before the rules (it works, so
#      the check means something) and after (it fails).
#   3. The import, as a customer does it: `igni-import` from inside the
#      bundle, against the TEST public key, into the air-gapped registry;
#      the Helm chart from the verified bundle, nothing else.
#   4. Smoke: migration job, rollouts, /health, /ready, /version, the
#      portal; then sign-up, an organisation, a member, the igni CLI's
#      own /login (its AuthController, with the portal's /cli-auth hop
#      done over HTTP) and `igni -m` to a stub model that runs inside the
#      cluster. The CLI steps need the igni checkout (IGNI_CLI, default
#      ../igni, with `uv sync` run); without it they are skipped, and say so.
#   5. The report: every image pull the cluster made (events, containerd),
#      every DNS name looked up (CoreDNS `log`), and every packet the gap
#      dropped (NFLOG capture), each with the pod or node process it came
#      from. Any of those leaving the cluster fails the run.
#
# TEST KEYS. The release key is in Cloud KMS and only CD signs with it
# (docs/OFFLINE_UPDATES.md, "The release key"). The keys made here are generated per
# run in $WORK/keys, named TEST-*, and deleted with it. They sign nothing
# that leaves this machine. Never point this at a real key.
#
# Needs: docker, kind, kubectl, helm, skopeo, cosign, jq, python3; syft
# for the image signatures and SBOM attestations (skipped, and said so,
# when it is missing). Docker must have about 4 GB free for the node.
#
# Environment (all optional):
#   IGNI_AIRGAP_NAME      cluster, network and container prefix (igni-airgap)
#   IGNI_AIRGAP_WORK      working directory (${TMPDIR:-/tmp}/igni-airgap-rehearsal)
#   IGNI_AIRGAP_SUBNET    the gap's Docker network (172.30.250.0/24)
#   IGNI_AIRGAP_SKIP_BUILD=1   use igni-api:airgap and igni-portal:airgap as built
#   IGNI_CLI              the igni CLI checkout (../igni)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

NAME="${IGNI_AIRGAP_NAME:-igni-airgap}"
WORK="${IGNI_AIRGAP_WORK:-${TMPDIR:-/tmp}/igni-airgap-rehearsal}"
WORK="${WORK%/}"
SUBNET="${IGNI_AIRGAP_SUBNET:-172.30.250.0/24}"
PREFIX="${SUBNET%.*}"           # 172.30.250
GATEWAY="${PREFIX}.1"           # Docker's gateway: the host, so outside
REGISTRY_IP="${PREFIX}.10"
NET="$NAME"
NODE="${NAME}-control-plane"
REGISTRY="${NAME}-registry"     # the customer's registry, inside the gap
STAGING="${NAME}-staging"       # Ignite Ember's side, connected
CAPTURE="${NAME}-capture"
CAPTURE_IMAGE="${NAME}-tcpdump:local"
POD_CIDR=10.244.0.0/16
SVC_CIDR=10.96.0.0/12
NAMESPACE=igni
RELEASE=igni
STAGING_PORT=55100
REGISTRY_PORT=55101
API_SERVER_PORT=56443
API_FWD=18777
PORTAL_FWD=18300
IN_GAP="${REGISTRY}:5000/igni"  # what pods pull from
FROM_HOST="127.0.0.1:${REGISTRY_PORT}/igni"  # the same registry, as the import host reaches it
CLI="${IGNI_CLI:-${ROOT}/../igni}"
PG_IMAGE=postgres:17.6-alpine
REDIS_IMAGE=redis:7.4-alpine
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "${ROOT}/pyproject.toml" | head -1)"
BUNDLE="igni-bundle-${VERSION}"
export KUBECONFIG="${WORK}/kubeconfig"
export COSIGN_PASSWORD=TEST-only-not-a-release-key

step() { printf '\n\033[1m→ %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
fail() {
  printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2
  exit 1
}
need() { command -v "$1" >/dev/null || fail "$1 is not installed"; }
node() { docker exec "$NODE" "$@"; }

for tool in docker kind kubectl helm skopeo cosign jq python3 curl; do need "$tool"; done
SIGN=1
command -v syft >/dev/null || {
  SIGN=0
  printf 'note: syft not found; images are bundled without cosign signatures and SBOM attestations\n'
}
[[ -n "$VERSION" ]] || fail "no version in pyproject.toml"

forwards=()
cleanup() {
  local code=$?
  for pid in "${forwards[@]}"; do kill "$pid" 2>/dev/null || true; done
  if [[ $code -ne 0 && -f "$KUBECONFIG" ]]; then
    printf '\n\033[31m--- pods ---\033[0m\n' >&2
    kubectl get pods -A -o wide 2>&1 | sed 's/^/    /' >&2 || true
    printf '\033[31m--- recent events ---\033[0m\n' >&2
    kubectl get events -A --sort-by=.lastTimestamp 2>&1 | tail -25 | sed 's/^/    /' >&2 || true
    for pod in $(kubectl -n "$NAMESPACE" get pods -o name 2>/dev/null); do
      printf '\033[31m--- %s ---\033[0m\n' "$pod" >&2
      kubectl -n "$NAMESPACE" logs "$pod" --all-containers --tail=30 2>&1 | sed 's/^/    /' >&2 || true
    done
  fi
  if [[ $KEEP -eq 1 ]]; then
    printf '\nLeft running: cluster %s (KUBECONFIG=%s), registry %s, network %s.\n' "$NAME" "$KUBECONFIG" "$REGISTRY" "$NET"
    printf 'Remove with: kind delete cluster --name %s; docker rm -f -v %s %s %s; docker network rm %s\n' \
      "$NAME" "$REGISTRY" "$STAGING" "$CAPTURE" "$NET"
  else
    teardown
  fi
  exit $code
}
teardown() {
  kind delete cluster --name "$NAME" >/dev/null 2>&1 || true
  docker rm -f -v "$REGISTRY" "$STAGING" "$CAPTURE" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
  # The TEST keys and the bundle go with the run; the report and the capture stay.
  rm -rf "${WORK}/keys" "${WORK}/staging" "${WORK}/import" "${WORK}/dist" "${WORK}/cli" "$KUBECONFIG"
}
trap cleanup EXIT

teardown
rm -rf "$WORK"
mkdir -p "$WORK"/{keys,staging,dist,capture,report,cli}

# ── 1. Connected: build, sign, bundle ──────────────────────────────────

if [[ "${IGNI_AIRGAP_SKIP_BUILD:-}" != 1 ]]; then
  step "Building igni-api and igni-portal"
  docker build -q -f "${ROOT}/containers/be/Dockerfile" -t igni-api:airgap "${ROOT}" >/dev/null
  docker build -q -f "${ROOT}/portal/Dockerfile" -t igni-portal:airgap "${ROOT}/portal" >/dev/null
fi
docker pull -q "$PG_IMAGE" >/dev/null
docker pull -q "$REDIS_IMAGE" >/dev/null

step "TEST keys, made for this run only (${WORK}/keys)"
# One key, as in a release: it signs the images and the bundle's MANIFEST.
(cd "${WORK}/keys" && cosign generate-key-pair --output-key-prefix TEST-cosign >/dev/null 2>&1) ||
  fail "cosign could not generate the TEST key"
note "cosign: TEST-cosign.{key,pub}"

step "Staging registry (Ignite Ember's side) on 127.0.0.1:${STAGING_PORT}"
docker run -d --name "$STAGING" -p "127.0.0.1:${STAGING_PORT}:5000" registry:2 >/dev/null
for _ in $(seq 1 30); do curl -fsS "http://127.0.0.1:${STAGING_PORT}/v2/" >/dev/null 2>&1 && break; sleep 1; done
# skopeo needs telling that this one is plain HTTP; cosign and syft are told per call.
cat >"${WORK}/registries.conf" <<EOF
[[registry]]
location = "127.0.0.1:${STAGING_PORT}"
insecure = true
EOF
export CONTAINERS_REGISTRIES_CONF="${WORK}/registries.conf"

# name, source image, bundle tag
images=(
  "igni-api igni-api:airgap ${VERSION}"
  "igni-portal igni-portal:airgap ${VERSION}"
  "postgres ${PG_IMAGE} ${PG_IMAGE#*:}"
  "redis ${REDIS_IMAGE} ${REDIS_IMAGE#*:}"
)
for line in "${images[@]}"; do
  read -r name source tag <<<"$line"
  ref="127.0.0.1:${STAGING_PORT}/${name}:${tag}"
  docker tag "$source" "$ref"
  docker push -q "$ref" >/dev/null
  if [[ $SIGN -eq 1 ]]; then
    # Key-based and kept out of any transparency log, as CD signs.
    cosign_flags=(--key "${WORK}/keys/TEST-cosign.key" --tlog-upload=false --allow-insecure-registry --yes)
    # cosign 3 writes a sigstore bundle by default; the bundle tool
    # carries the `.sig`/`.att` tags cosign 2 (CD's pin) writes.
    if [[ "$(cosign version 2>&1 | sed -n 's/^GitVersion: *v\([0-9]*\).*/\1/p')" -ge 3 ]]; then
      cosign_flags+=(--new-bundle-format=false --use-signing-config=false)
    fi
    cosign sign "${cosign_flags[@]}" "$ref" >/dev/null 2>&1 || fail "cosign could not sign $ref"
    SYFT_REGISTRY_INSECURE_USE_HTTP=true syft -q "registry:${ref}" -o cyclonedx-json >"${WORK}/${name}.cdx.json"
    cosign attest "${cosign_flags[@]}" --type cyclonedx --predicate "${WORK}/${name}.cdx.json" "$ref" >/dev/null 2>&1 ||
      fail "cosign could not attest $ref"
  fi
  export_args=(--staging "${WORK}/staging/${BUNDLE}" --name "$name" --repository "127.0.0.1:${STAGING_PORT}/${name}"
    --source-tag "$tag" --tag "$tag")
  [[ $SIGN -eq 0 ]] || export_args+=(--cosign-key "${WORK}/keys/TEST-cosign.pub")
  "${ROOT}/tools/release/igni-bundle" export-image "${export_args[@]}" >"${WORK}/export-${name}.log" 2>&1 ||
    fail "igni-bundle export-image ${name}: $(tail -5 "${WORK}/export-${name}.log")"
  note "$(grep '^exported ' "${WORK}/export-${name}.log")"
done

step "Chart, and the seal (TEST cosign key)"
helm package "${ROOT}/charts/igni" --destination "${WORK}/staging/${BUNDLE}/charts" >/dev/null
"${ROOT}/tools/release/igni-bundle" seal --staging "${WORK}/staging/${BUNDLE}" --version "$VERSION" \
  --commit "$(git -C "$ROOT" rev-parse HEAD)" --key "${WORK}/keys/TEST-cosign.key" \
  --public-key "${WORK}/keys/TEST-cosign.pub" --out "${WORK}/dist" | sed 's/^/    /'
note "$(du -h "${WORK}/dist/${BUNDLE}.tar" | cut -f1) ${WORK}/dist/${BUNDLE}.tar"
docker rm -f -v "$STAGING" >/dev/null
rm -rf "${WORK}/staging"

# ── 2. The gap ─────────────────────────────────────────────────────────

step "The gap's network ${NET} (${SUBNET}), its registry, and a one-node kind cluster"
docker network create --subnet "$SUBNET" --gateway "$GATEWAY" "$NET" >/dev/null
docker run -d --name "$REGISTRY" --network "$NET" --ip "$REGISTRY_IP" \
  -p "127.0.0.1:${REGISTRY_PORT}:5000" registry:2 >/dev/null
cat >"${WORK}/kind.yaml" <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  apiServerAddress: 127.0.0.1
  apiServerPort: ${API_SERVER_PORT}
containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri".registry]
      config_path = "/etc/containerd/certs.d"
nodes:
  - role: control-plane
EOF
# A --internal Docker network would be the obvious gap, but kind's node
# entrypoint cannot start on one (it rewrites Docker's DNS through the
# network gateway, and an internal network has none). So the network is
# ordinary and the gap is netfilter inside the node, below.
KIND_EXPERIMENTAL_DOCKER_NETWORK="$NET" kind create cluster --name "$NAME" --config "${WORK}/kind.yaml" \
  --kubeconfig "$KUBECONFIG" --wait 120s >/dev/null 2>&1 || fail "kind could not create the cluster"
NODE_IP="$(docker inspect -f "{{(index .NetworkSettings.Networks \"${NET}\").IPAddress}}" "$NODE")"
note "node ${NODE} ${NODE_IP}; registry ${REGISTRY} ${REGISTRY_IP}; gateway (the host) ${GATEWAY}"

# containerd reaches the registry by name, from /etc/hosts: Docker's
# embedded DNS is outside the gap. Plain HTTP, as a lab registry often is.
node sh -c "echo '${REGISTRY_IP} ${REGISTRY}' >> /etc/hosts"
node mkdir -p "/etc/containerd/certs.d/${REGISTRY}:5000"
docker exec -i "$NODE" sh -c "cat > '/etc/containerd/certs.d/${REGISTRY}:5000/hosts.toml'" <<EOF
server = "http://${REGISTRY}:5000"
[host."http://${REGISTRY}:5000"]
  capabilities = ["pull", "resolve"]
EOF

step "Egress before the gap closes (the control: this should work)"
control="$(node curl -sS -m 8 -o /dev/null -w '%{http_code}' http://1.1.1.1/ 2>&1 || true)"
note "node -> http://1.1.1.1/: ${control}"
[[ "$control" =~ ^[0-9]{3}$ && "$control" != 000 ]] ||
  note "no internet even before the gap: the control proves nothing on this machine"

step "Capture: tcpdump in the node's network namespace (every pod and the node itself)"
printf 'FROM alpine:3.20\nRUN apk add --no-cache tcpdump\n' | docker build -q -t "$CAPTURE_IMAGE" - >/dev/null
# nflog:5 is what the gap drops (below); `any` is every connection attempt
# and every DNS packet on every interface, for context.
docker run -d --name "$CAPTURE" --network "container:${NODE}" --cap-add NET_ADMIN --cap-add NET_RAW \
  -v "${WORK}/capture:/cap" --entrypoint sh "$CAPTURE_IMAGE" -c \
  "tcpdump -i nflog:5 -nn -U -w /cap/dropped.pcap & \
   tcpdump -i any -nn -U -w /cap/attempts.pcap 'udp port 53 or tcp port 53 or (tcp[tcpflags] & tcp-syn != 0 and tcp[tcpflags] & tcp-ack == 0 and not (src net 127.0.0.0/8 and dst net 127.0.0.0/8))' & \
   wait" >/dev/null
sleep 2

step "Closing the gap: netfilter in the node drops everything not addressed to the cluster"
# mangle, so it runs before kube-proxy's and Docker's NAT and sees the
# address a packet was sent to. PREROUTING for what pods send, OUTPUT for
# what the node's own processes send. Replies to connections that came
# in (kubectl through the published API port) are let through.
node sh -eu -c "
for t in iptables ip6tables; do
  \$t -t mangle -N AIRGAP
  \$t -t mangle -A AIRGAP -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
done
iptables -t mangle -A AIRGAP -d 127.0.0.11 -j NFLOG --nflog-group 5 --nflog-prefix docker-dns
iptables -t mangle -A AIRGAP -d 127.0.0.11 -j DROP
iptables -t mangle -A AIRGAP -d ${GATEWAY} -j NFLOG --nflog-group 5 --nflog-prefix gateway
iptables -t mangle -A AIRGAP -d ${GATEWAY} -j DROP
for net in 127.0.0.0/8 ${POD_CIDR} ${SVC_CIDR} ${SUBNET}; do
  iptables -t mangle -A AIRGAP -d \$net -j RETURN
done
ip6tables -t mangle -A AIRGAP -d ::1/128 -j RETURN
ip6tables -t mangle -A AIRGAP -d fe80::/10 -j RETURN
for t in iptables ip6tables; do
  \$t -t mangle -A AIRGAP -j NFLOG --nflog-group 5 --nflog-prefix egress
  \$t -t mangle -A AIRGAP -j DROP
  \$t -t mangle -I OUTPUT 1 -j AIRGAP
done
iptables -t mangle -I PREROUTING 1 -s ${POD_CIDR} -j AIRGAP
ip6tables -t mangle -I PREROUTING 1 ! -i lo -j AIRGAP
"

mark() { printf '%s %s\n' "$(node date +%s.%N)" "$1" >>"${WORK}/report/phases.txt"; }
mark gap-closed

step "Every DNS question, logged by CoreDNS with who asked"
kubectl -n kube-system get configmap coredns -o json |
  jq '.data.Corefile |= sub("errors"; "log\n    errors")' | kubectl replace -f - >/dev/null
kubectl -n kube-system rollout restart deploy/coredns >/dev/null
kubectl -n kube-system rollout status deploy/coredns --timeout=120s >/dev/null

step "Egress after the gap closes (this must fail)"
probe_node() {
  local what=$1
  shift
  if node "$@" >/dev/null 2>&1; then fail "the node reached ${what} through the gap"; fi
  note "node -> ${what}: refused, as it should be"
}
probe_node "http://1.1.1.1/" curl -sS -m 5 http://1.1.1.1/
probe_node "https://example.com/ (DNS)" curl -sS -m 5 https://example.com/
probe_node "registry-1.docker.io (containerd pull)" crictl pull docker.io/library/busybox:1.36

# ── 3. Import, as the customer does ────────────────────────────────────

step "igni-import, from inside the bundle, into the gap's registry"
mkdir -p "${WORK}/import"
tar -xf "${WORK}/dist/${BUNDLE}.tar" -C "${WORK}/import" "${BUNDLE}/igni-import"
import_args=(--bundle "${WORK}/dist/${BUNDLE}.tar" --public-key "${WORK}/keys/TEST-cosign.pub"
  --registry "$FROM_HOST" --dest-tls-verify false --expect-version "$VERSION" --keep "${WORK}/import/kept")
[[ $SIGN -eq 0 ]] || import_args+=(--cosign-key "${WORK}/keys/TEST-cosign.pub")
"${WORK}/import/${BUNDLE}/igni-import" "${import_args[@]}" >"${WORK}/report/igni-import.log" 2>&1 ||
  fail "igni-import refused: $(tail -5 "${WORK}/report/igni-import.log")"
grep -E '^(Verified|  signature|  files|Imported|  [0-9]|Helm chart)' "${WORK}/report/igni-import.log" | sed 's/^/    /'
KEPT="${WORK}/import/kept/${BUNDLE}"
CHART="$(ls "${KEPT}"/charts/igni-*.tgz)"

step "Egress from a pod (this must fail too)"
kubectl create namespace "$NAMESPACE" >/dev/null
kubectl -n "$NAMESPACE" run egress-probe --image "${IN_GAP}/igni-api:${VERSION}" --restart=Never \
  --command -- python3 -c '
import socket
for target in [("1.1.1.1", 443), ("example.com", 443), ("ghcr.io", 443)]:
    try:
        socket.create_connection(target, timeout=5).close()
        print("REACHED", *target)
    except OSError as exc:
        print("blocked", *target, type(exc).__name__, exc)
' >/dev/null
kubectl -n "$NAMESPACE" wait --for=jsonpath='{.status.phase}'=Succeeded pod/egress-probe --timeout=120s >/dev/null ||
  fail "the egress probe pod did not run"
probe_out="$(kubectl -n "$NAMESPACE" logs egress-probe)"
printf '%s\n' "$probe_out" | sed 's/^/    pod -> /'
! grep -q '^REACHED' <<<"$probe_out" || fail "a pod reached the internet through the gap"
kubectl -n "$NAMESPACE" delete pod egress-probe --wait=false >/dev/null
# Everything after this is the install and the journey: an attempt to
# leave from here on is a finding, not a probe.
mark probes-done

# ── 4. Install and smoke ───────────────────────────────────────────────

step "Postgres, Redis and the stub model, from the bundle's images"
# The bundle names them postgres and redis, with their upstream tags.
sed -e "s#image: ${PG_IMAGE}#image: ${IN_GAP}/${PG_IMAGE}#" -e "s#image: ${REDIS_IMAGE}#image: ${IN_GAP}/${REDIS_IMAGE}#" \
  "${ROOT}/tools/kind/datastores.yaml" >"${WORK}/datastores.yaml"
grep -q "image: ${IN_GAP}/${PG_IMAGE}" "${WORK}/datastores.yaml" || fail "datastores.yaml: Postgres image not rewritten"
grep -q "image: ${IN_GAP}/${REDIS_IMAGE}" "${WORK}/datastores.yaml" || fail "datastores.yaml: Redis image not rewritten"
kubectl -n "$NAMESPACE" apply -f "${WORK}/datastores.yaml" >/dev/null
# The journey's stub, run by the API image's Python: it is the model,
# inside the gap, and its log says what the chat proxy sent it.
kubectl -n "$NAMESPACE" create configmap igni-stub --from-file="${ROOT}/tools/e2e_journey/stub_openai.py" >/dev/null
kubectl -n "$NAMESPACE" apply -f - >/dev/null <<EOF
apiVersion: apps/v1
kind: Deployment
metadata: {name: igni-stub}
spec:
  selector: {matchLabels: {app: igni-stub}}
  template:
    metadata: {labels: {app: igni-stub}}
    spec:
      containers:
        - name: stub
          image: ${IN_GAP}/igni-api:${VERSION}
          command: [python3, /stub/stub_openai.py, --host, 0.0.0.0, --port, "8000"]
          env: [{name: STUB_LOG, value: /tmp/stub.jsonl}]
          volumeMounts: [{name: stub, mountPath: /stub}]
      volumes: [{name: stub, configMap: {name: igni-stub}}]
---
apiVersion: v1
kind: Service
metadata: {name: igni-stub}
spec:
  selector: {app: igni-stub}
  ports: [{port: 8000, targetPort: 8000}]
EOF
for d in pg redis igni-stub; do kubectl -n "$NAMESPACE" rollout status "deploy/$d" --timeout=180s >/dev/null; done

step "helm install, from the chart in the verified bundle"
cat >"${WORK}/values-airgap.yaml" <<EOF
image:
  repository: ${IN_GAP}/igni-api
  tag: "${VERSION}"
  pullPolicy: IfNotPresent
portalImage:
  repository: ${IN_GAP}/igni-portal
  tag: "${VERSION}"
  pullPolicy: IfNotPresent
email:
  logSignInCodes: true
extraEnv:
  - name: INFERENCE_BACKENDS
    value: '[{"id":"airgap-stub","base_url":"http://igni-stub:8000/v1","model":"e2e-stub-model","max_concurrent":8,"pools":["chat"]}]'
EOF
helm install "$RELEASE" "$CHART" --namespace "$NAMESPACE" \
  --values "${ROOT}/tools/kind/values.yaml" --values "${WORK}/values-airgap.yaml" \
  --wait --timeout 6m >/dev/null || fail "helm install did not converge"

migrate_job=$(kubectl -n "$NAMESPACE" get jobs -o name | grep migrate || true)
[[ -n "$migrate_job" ]] || fail "no migration job was created"
kubectl -n "$NAMESPACE" wait --for=condition=complete "$migrate_job" --timeout=180s >/dev/null ||
  fail "the migration job did not complete"
note "migration job complete"
for deploy in "${RELEASE}-api" "${RELEASE}-portal"; do
  kubectl -n "$NAMESPACE" rollout status "deploy/${deploy}" --timeout=180s >/dev/null
  note "deploy/${deploy} rolled out"
done

forward() {
  kubectl -n "$NAMESPACE" port-forward "svc/$1" "$2:$3" >/dev/null 2>&1 &
  forwards+=($!)
  for _ in $(seq 1 30); do curl -fsS -m 2 -o /dev/null "http://127.0.0.1:$2$4" 2>/dev/null && return 0; sleep 1; done
  fail "svc/$1 did not answer on $4 through a port-forward"
}
forward "${RELEASE}-api" "$API_FWD" 7777 /health
forward "${RELEASE}-portal" "$PORTAL_FWD" 3000 /
API="http://127.0.0.1:${API_FWD}"
PORTAL="http://127.0.0.1:${PORTAL_FWD}"

step "Smoke: the API and the portal"
for path in /health /ready /version; do
  body=$(curl -fsS -m 5 "${API}${path}") || fail "${path} did not answer"
  note "$(printf '%-9s %s' "$path" "${body:0:160}")"
done
ready=$(curl -fsS -m 5 "${API}/ready")
grep -q '"postgres": *"ok"' <<<"$ready" || fail "/ready does not report postgres ok: $ready"
grep -q '"redis": *"ok"' <<<"$ready" || fail "/ready does not report redis ok: $ready"
curl -fsS -m 5 -o "${WORK}/report/portal-index.html" "${PORTAL}/" || fail "the portal did not serve its index"
grep -qi '<div id="root"' "${WORK}/report/portal-index.html" || fail "the portal served something that is not the app"
note "portal    served, and it is the app"
# What the browser would fetch from elsewhere: anything absolute in the index.
external=$(grep -oE '(src|href)="https?://[^"]+' "${WORK}/report/portal-index.html" | grep -vE '127\.0\.0\.1|localhost' || true)
[[ -z "$external" ]] || note "portal index references outside addresses (the browser would fetch them): ${external}"

# Codes are logged (email.logSignInCodes, the documented first setup
# with no relay); read from the API pod's log.
api_log() { kubectl -n "$NAMESPACE" logs "deploy/${RELEASE}-api" --since=10m 2>/dev/null; }
code_for() {
  local kind=$1 email=$2 code
  for _ in $(seq 1 20); do
    code=$(api_log | grep -oE "${kind} code for ${email} is [0-9]+" | tail -1 | awk '{print $NF}')
    [[ -n "$code" ]] && { echo "$code"; return 0; }
    sleep 1
  done
  fail "no '${kind} code for ${email}' in the API log"
}
call() { # METHOD PATH TOKEN ORG [JSON]
  local args=(-sS -m 30 -X "$1" "${API}$2" -H 'Content-Type: application/json' -w '\n%{http_code}')
  [[ -z "$3" ]] || args+=(-H "Authorization: Bearer $3")
  [[ -z "$4" ]] || args+=(-H "X-Org-Id: $4")
  [[ -z "${5:-}" ]] || args+=(-d "$5")
  local out status
  out=$(curl "${args[@]}") || fail "$1 $2: no answer"
  status="${out##*$'\n'}"
  out="${out%$'\n'*}"
  [[ "$status" =~ ^2 ]] || fail "$1 $2 -> ${status} ${out:0:300}"
  printf '%s' "$out"
}
PASSWORD='Airgap-rehearsal-0nly!'
sign_up() { # first last email -> token
  call POST /v1/send-sign-up-code '' '' "{\"email\":\"$3\"}" >/dev/null
  local code
  code=$(code_for sign-up "$3")
  call POST /v1/sign-up '' '' \
    "{\"first_name\":\"$1\",\"last_name\":\"$2\",\"email\":\"$3\",\"code\":\"$code\",\"password\":\"$PASSWORD\"}" |
    jq -r .access_token
}

step "Journey: sign-up, organisation, member"
DOMAIN="acme-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n').com"
ADA="ada@${DOMAIN}"
MAX="max@${DOMAIN}"
ada=$(sign_up Ada Lovelace "$ADA")
[[ "$ada" == eyJ* ]] || fail "sign-up issued no token"
note "sign-up    ${ADA}: $(call GET /v1/portal/me "$ada" '' | jq -c '{is_super_admin, orgs: (.organizations | length)}')"
org=$(call POST /v1/portal/organizations "$ada" '' "{\"display_name\":\"Acme ${DOMAIN}\",\"domains\":[\"${DOMAIN}\"]}" | jq -r .id)
note "org        ${org}, creator role $(call GET /v1/portal/me "$ada" "$org" | jq -r .role)"
call POST /v1/portal/team/invite "$ada" "$org" "{\"email\":\"${MAX}\",\"role\":\"member\"}" >/dev/null
max=$(sign_up Max Planck "$MAX")
max_role=$(call GET /v1/portal/me "$max" "$org" | jq -r .role)
[[ "$max_role" == member ]] || fail "the invited member is '${max_role}' in the organisation, not member"
note "member     ${MAX} invited, signed up, role ${max_role}"

if [[ -x "${CLI}/.venv/bin/igni" ]]; then
  step "Journey: the igni CLI's /login, and a chat to the stub model inside the gap"
  home="${WORK}/cli/home"
  project="${WORK}/cli/project"
  mkdir -p "${home}/.igni" "${project}/.igni"
  printf 'api_url: %s\nportal_url: %s\nupdate_check_interval: 0\n' "$API" "$PORTAL" >"${home}/.igni/config.yaml"
  printf "print('hi')\n" >"${project}/hello.py"
  git -C "$project" init -q
  cli_env=(env -u VIRTUAL_ENV -u PYTHONPATH -u PYTHONHOME HOME="$home" HF_HUB_OFFLINE=1 NO_COLOR=1)
  url_file="${WORK}/cli/login-url"
  # The CLI's own AuthController: callback server, /v1/auth/exchange,
  # /v1/portal/me, credentials file. Its "browser" writes the URL down.
  (cd "$project" && "${cli_env[@]}" BROWSER="${ROOT}/tools/e2e_journey/record_url.sh %s" CLI_LOGIN_URL_FILE="$url_file" \
    "${CLI}/.venv/bin/python" "${ROOT}/tools/e2e_journey/cli_login.py" --project "$project" --timeout 90 \
    >"${WORK}/cli/login.log" 2>&1) &
  login_pid=$!
  for _ in $(seq 1 60); do [[ -s "$url_file" ]] && break; sleep 0.5; done
  [[ -s "$url_file" ]] || fail "the CLI never opened its login page: $(tail -5 "${WORK}/cli/login.log")"
  login_url=$(cat "$url_file")
  port=$(sed -nE 's/.*[?&]port=([0-9]+).*/\1/p' <<<"$login_url")
  [[ -n "$port" ]] || fail "no callback port in the CLI's login URL: $login_url"
  # What /cli-auth does in the browser once signed in: an org-scoped
  # handoff code from the API, then the CLI's callback.
  handoff=$(call POST /v1/cli/token "$max" '' "{\"org_id\":\"${org}\"}" | jq -r .handoff_code)
  curl -fsS -m 10 -o /dev/null "http://127.0.0.1:${port}/callback?code=${handoff}" || fail "the CLI's callback refused the code"
  wait "$login_pid" || fail "the CLI's login failed: $(tail -5 "${WORK}/cli/login.log")"
  note "CLI login  $(tail -1 "${WORK}/cli/login.log")"
  cli_token=$(jq -r .access_token "${home}/.igni/credentials.json")
  note "models     $(call GET /v1/chat/models "$cli_token" '' | head -c 200)"
  chat_out=$(cd "$project" && "${cli_env[@]}" "${CLI}/.venv/bin/igni" -m 'Say hello' --no-web 2>&1) ||
    fail "igni -m failed: ${chat_out: -500}"
  grep -q 'STUB-REPLY' <<<"$chat_out" || fail "igni -m printed no reply from the stub: ${chat_out: -500}"
  posts=$(kubectl -n "$NAMESPACE" exec deploy/igni-stub -- sh -c 'grep -c "\"method\": *\"POST\"" /tmp/stub.jsonl || true')
  [[ "${posts:-0}" -gt 0 ]] || fail "the stub model saw no completion request"
  note "chat       igni -m printed the stub's reply; the stub saw ${posts} completion request(s)"
else
  note "SKIPPED: the CLI's /login and igni -m — no igni CLI at ${CLI} (IGNI_CLI, with \`uv sync\` run in it)"
  step "Journey: an org-scoped CLI token and a chat to the stub model inside the gap, over HTTP"
  cli_token=$(call POST /v1/cli/token "$max" '' "{\"org_id\":\"${org}\"}" | jq -r .access_token)
  reply=$(call POST /v1/chat/completions "$cli_token" '' \
    '{"model":"e2e-stub-model","messages":[{"role":"user","content":"Say hello"}],"stream":false}')
  grep -q 'STUB-REPLY' <<<"$reply" || fail "the chat proxy returned no stub reply: ${reply:0:300}"
  note "chat       /v1/chat/completions answered with the stub's reply"
fi

# ── 5. The report ──────────────────────────────────────────────────────

step "Egress report"
sleep 3
mark end
docker stop -t 5 "$CAPTURE" >/dev/null
kubectl get pods -A -o json >"${WORK}/report/pods.json"
kubectl get events -A -o json >"${WORK}/report/events.json"
kubectl -n kube-system logs -l k8s-app=kube-dns --tail=-1 --prefix --timestamps >"${WORK}/report/coredns.log" 2>&1 || true
node crictl images -o json >"${WORK}/report/node-images.json"
node journalctl -u containerd --no-pager -o cat >"${WORK}/report/containerd.log" 2>&1 || true
node sh -c 'iptables -t mangle -L AIRGAP -v -n -x; ip6tables -t mangle -L AIRGAP -v -n -x' >"${WORK}/report/airgap-counters.txt"
for pcap in dropped attempts; do
  docker run --rm -v "${WORK}/capture:/cap" --entrypoint tcpdump "$CAPTURE_IMAGE" -nn -tttt -r "/cap/${pcap}.pcap" \
    >"${WORK}/report/${pcap}.txt" 2>/dev/null || true
done
python3 "${HERE}/airgap_egress.py" --report "${WORK}/report" --node-ip "$NODE_IP" --gateway "$GATEWAY" \
  --allow "$POD_CIDR" "$SVC_CIDR" "$SUBNET" 127.0.0.0/8 --registry "${REGISTRY}:5000" ||
  fail "something in the cluster tried to reach outside it (above; evidence in ${WORK}/report)"

printf '\n\033[32m✓ installed from the bundle alone, smoke passed, and nothing left the gap\033[0m\n'
printf 'Evidence: %s/report\n' "$WORK"
