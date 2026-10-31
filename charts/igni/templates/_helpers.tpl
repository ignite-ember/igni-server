{{/*
Names and labels.

`fullname` follows the usual Helm convention: the release name alone when
the release is already called `igni`, so a customer does not end up with
`igni-igni-api`.
*/}}
{{- define "igni.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "igni.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "igni.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "igni.labels" -}}
helm.sh/chart: {{ include "igni.chart" . }}
app.kubernetes.io/name: {{ include "igni.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: igni
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/*
Selector labels for one component. A Deployment's selector is immutable
after creation, so these must never gain a field that changes between
releases — no chart version, no image tag, no checksum.
*/}}
{{- define "igni.selectorLabels" -}}
app.kubernetes.io/name: {{ include "igni.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "igni.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "igni.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "igni.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "igni.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
PUBLIC_BASE_URL: the server's external address. Defaults to the ingress
host, because with the ingress enabled that is where `/.well-known` and
`/v1` are published.
*/}}
{{- define "igni.publicBaseUrl" -}}
{{- if .Values.urls.publicBase -}}
{{- .Values.urls.publicBase -}}
{{- else if and .Values.ingress.enabled .Values.ingress.host -}}
{{- printf "https://%s" .Values.ingress.host -}}
{{- end -}}
{{- end -}}

{{/*
Artifacts' own public URL: its identity. The audience of every token
issued for it and the base igni calls for erasure and export. Always
https on its own host — see the `artifacts` block in values.yaml.
*/}}
{{- define "igni.artifactsPublicUrl" -}}
{{- printf "https://%s" (lower .Values.artifacts.host) -}}
{{- end -}}

{{/*
This release's API as a pod in the same cluster reaches it. A service
calls igni here; the issuer it compares tokens against is still
PUBLIC_BASE_URL, which this need not equal.
*/}}
{{- define "igni.apiInClusterUrl" -}}
{{- printf "http://%s-api.%s.svc:%v" (include "igni.fullname" .) .Release.Namespace .Values.service.apiPort -}}
{{- end -}}

{{/*
One NetworkPolicy peer from `{namespaceSelector, podSelector}` label
maps: both in one peer, so a podSelector narrows the namespace rather
than widening to that label in every namespace.
*/}}
{{- define "igni.networkPolicyPeer" -}}
- namespaceSelector:
    matchLabels:
      {{- toYaml .namespaceSelector | nindent 6 }}
  {{- with .podSelector }}
  podSelector:
    matchLabels:
      {{- toYaml . | nindent 6 }}
  {{- end }}
{{- end -}}

{{- define "igni.configMapName" -}}
{{- printf "%s-config" (include "igni.fullname" .) -}}
{{- end -}}

{{/*
Image references.

Both refuse rather than default, and both refuse `latest`. A chart that
renders a plausible-but-wrong image reference is worse than one that will
not render: `docker-compose.prod.yml` shipped `ember/ember-api:latest`
for a while, which is an unqualified name and therefore Docker Hub — a
namespace that is not ours and that answers 401, so it could not even be
established who owned it.
*/}}
{{- define "igni.apiImage" -}}
{{- $repo := .Values.image.repository | default "" -}}
{{- if not $repo -}}
{{- fail "image.repository is required. Point it at the registry your cluster pulls from, e.g. registry.example.com/igni/igni-api, or ghcr.io/ignite-ember/igni-api where igni publishes releases — there is deliberately no default: an unqualified name resolves to Docker Hub, and an air-gapped cluster has to use its own registry." -}}
{{- end -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion -}}
{{- if eq (toString $tag) "latest" -}}
{{- fail "image.tag must not be `latest`. A `latest` tag cannot be rolled back to, and an unchanged tag gives Kubernetes no reason to roll pods on upgrade. Pin the tag you built." -}}
{{- end -}}
{{- printf "%s:%s" $repo (toString $tag) -}}
{{- end -}}

{{- define "igni.portalImage" -}}
{{- $repo := .Values.portalImage.repository | default "" -}}
{{- if not $repo -}}
{{- fail "portalImage.repository is required. Point it at the registry your cluster pulls from, e.g. registry.example.com/igni/igni-portal, or ghcr.io/ignite-ember/igni-portal." -}}
{{- end -}}
{{- $tag := .Values.portalImage.tag | default .Chart.AppVersion -}}
{{- if eq (toString $tag) "latest" -}}
{{- fail "portalImage.tag must not be `latest`. Pin the tag you built." -}}
{{- end -}}
{{- printf "%s:%s" $repo (toString $tag) -}}
{{- end -}}

{{/*
Everything the chart refuses to guess.

Called from every template that needs the application to actually work,
so the message arrives at `helm install` rather than as a CrashLoopBackOff
somebody has to read logs to explain.
*/}}
{{- define "igni.validate" -}}
{{- if not .Values.postgres.host -}}
{{- fail "postgres.host is required. This chart deploys no database: bring your own Postgres and point at it." -}}
{{- end -}}
{{- if not .Values.redis.host -}}
{{- fail "redis.host is required. This chart deploys no Redis: bring your own and point at it." -}}
{{- end -}}
{{- if .Values.artifacts.enabled -}}
{{- if not .Values.artifacts.host -}}
{{- fail "artifacts.host is required when artifacts.enabled is true: a service is served on its own hostname (e.g. artifacts.example.com), never as a path on igni's. Its URL is its identity: the audience of every token issued for it, and where igni calls it to erase or export a person's data. See docs/service-identity.md." -}}
{{- end -}}
{{- if or (contains "/" .Values.artifacts.host) (contains ":" .Values.artifacts.host) -}}
{{- fail "artifacts.host must be a bare hostname such as artifacts.example.com, with no scheme, port or path: the chart publishes it on https:// and gives the service https://<artifacts.host> as its URL." -}}
{{- end -}}
{{- if eq (lower .Values.artifacts.host) (lower (.Values.ingress.host | default "")) -}}
{{- fail "artifacts.host must differ from ingress.host: Artifacts is its own origin, and sharing igni's would make the audience of its tokens ambiguous." -}}
{{- end -}}
{{- if not .Values.artifacts.serviceName -}}
{{- fail "artifacts.serviceName is required when artifacts.enabled is true: it is the Service the artifacts.host ingress rule sends document traffic to." -}}
{{- end -}}
{{- end -}}
{{- if and .Values.secrets.create (not .Values.secrets.existingSecret) -}}
{{- if not .Values.secrets.secretEncryptionKey -}}
{{- fail "secrets.secretEncryptionKey is required. The server generates its own token-signing key and keeps it encrypted under this one, so without it nobody can sign in; the server refuses to start too. Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' — or set secrets.existingSecret and keep it out of your values file entirely." -}}
{{- end -}}
{{- if ne (len .Values.secrets.secretEncryptionKey) 44 -}}
{{- fail "secrets.secretEncryptionKey is not a Fernet key: one is exactly 44 characters of url-safe base64. Check for a truncated paste. The server refuses to start on this too." -}}
{{- end -}}
{{- end -}}
{{- if and (ne .Values.appEnv "dev") (not (hasPrefix "https://" (include "igni.publicBaseUrl" .))) -}}
{{- fail "urls.publicBase is required outside appEnv dev, and must be this server's https:// address (or enable the ingress, which supplies https://<ingress.host>). It is the issuer of every token, and services fetch the public signing keys from it; over plain HTTP those keys could be replaced in transit. The server refuses to start on this too." -}}
{{- end -}}
{{- if and (ne .Values.appEnv "prod") (ne .Values.appEnv "dev") -}}
{{- fail "appEnv must be `prod` or `dev`." -}}
{{- end -}}
{{- if and .Values.ingress.enabled (not .Values.ingress.host) -}}
{{- fail "ingress.host is required when ingress.enabled is true." -}}
{{- end -}}
{{- end -}}
