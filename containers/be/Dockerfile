# Stage 1: Build stage
#
# 3.13, matching ``requires-python = "==3.13.*"`` in pyproject.toml and the
# only version any CI job runs. Both stages said 3.12, which meant the
# shipped image ran a Python the project declares it does not support —
# and the builder resolved dependencies under a *managed* 3.13 that uv
# fetched to satisfy ``requires-python``, then stage 2 installed the
# resulting requirements.txt with 3.12's pip.
#
# It imported fine, so this was latent rather than broken. The gap is that
# nothing could catch it: CI is 3.13 everywhere, so the first 3.13-only
# construct anyone writes passes every job and crash-loops the container.
# Demonstrated with PEP 696 type-parameter defaults — ``type A[T = int]``
# parses on 3.13 and is a SyntaxError on 3.12, which fails at import, so
# the container would not have started at all.
#
# **The Debian release is named, not inherited.** ``python:3.13-slim`` is a
# moving tag: it was bookworm and became trixie, silently, between two
# builds of this image. That is how the zlib in the shipped image changed
# under us without a line of this file changing — the image kept the same
# tag and the same declared base, and the OS underneath it moved. Writing
# ``-trixie`` makes the Debian release an explicit, auditable choice
# instead of a property of whatever the tag pointed at that morning.
# Slim rather than alpine because the wheels this project installs are
# built against glibc.
FROM python:3.13-slim-trixie AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y build-essential libsndfile1 curl git && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Copy project metadata
COPY pyproject.toml uv.lock ./

# Sync dependencies and generate requirements.txt
RUN uv sync --no-dev && uv pip freeze > requirements.txt

# Stage 2: Final image
#
# The same base as the builder, so the two stages cannot disagree about
# which Debian release and which glibc the wheels are built and shipped
# against.
FROM python:3.13-slim-trixie

WORKDIR /app

# Copy requirements.txt from builder
COPY --from=builder /app/requirements.txt ./

# **The security update, because the base image is older than it.**
#
# The scheduled scan (V-4, V-7) gates on the image's CVEs and it went red
# on two HIGH advisories in the base OS's zlib: CVE-2026-76844 (disclosure
# via path traversal) and CVE-2026-85091 (a heap buffer overflow in
# ``gz_vacate()``). The scan reads the *installed* version and said so:
# ``1:1.3.dfsg+really1.3.1-1+b1``, status "fixed", fixed in
# ``1:1.3.dfsg+really1.3.1-1+deb13u1``.
#
# That fixed version exists — in trixie-security, which is where the
# ``+deb13u1`` suffix means it came from — but not in the image: the
# published slim image was built before the update landed and still
# carries the older zlib. So the base image is the stale part, and moving
# the base tag does not fix it. It has to be pulled in here.
#
# ``--only-upgrade`` on purpose: it takes the security update and leaves
# every other package in the image exactly where the base image put it,
# so this is the smallest change that closes the finding. A full
# ``apt-get upgrade`` would drag every package in the base forward, which
# is a different change with a different blast radius, and this is not
# that change.
#
# The scan is the backstop. If a future advisory lands in zlib1g, this
# line takes the fix as long as Debian has published one, and the daily
# scan says so if it does not.
RUN apt-get update \
 && apt-get install --yes --only-upgrade zlib1g \
 && rm -rf /var/lib/apt/lists/*

# Install dependencies, then remove pip itself. pip is an install-time
# tool and nothing in the running image calls it, but it carries its own
# vendored copies of msgpack, urllib3 and setuptools (pip/_vendor), which
# image scanners read from its vendor.txt. Those copies trail the ones in
# uv.lock: the scheduled scan went red on five HIGH advisories that were
# all pip's vendored msgpack 1.1.2, urllib3 2.7.0 and setuptools 70.3.0
# (CVE-2025-47273), while the application's own msgpack and urllib3 were
# already fixed. Upgrading pip fixes msgpack and urllib3 but not
# setuptools, which pip still vendors at 70.3.0; removing pip fixes all
# of them, and whatever its next vendored advisory is. The earlier
# `--upgrade pip` (seven advisories `pip-audit` reported against pip
# itself) goes with it.
# No torch here. Embedding is igni__artifacts' own image (its
# `embedder/`); this server makes no vectors, and this image is pulled
# per API replica and by two marketplaces.
RUN pip install --no-cache-dir -r requirements.txt \
 && pip uninstall --yes pip \
 && ! python -c "import pip" 2>/dev/null

# Copy app sources
COPY ./app /app/app

# Alembic migrations
COPY alembic.ini .
COPY alembic ./alembic
# Each organisation's own database (app/db/sql/tenancy.py)
COPY alembic_tenant ./alembic_tenant
# Re-encrypts stored secrets after SECRET_ENCRYPTION_KEY is rotated. In
# the image because the runbook runs it here, against this deployment's
# own settings (charts/igni/README.md, docs/self-hosting.md).
COPY tools/rotate_secret_key.py ./tools/rotate_secret_key.py
# Sends one real request through a configured backend, to prove a cloud
# backend's identity and model where the server's own identity lives
# (docs/self-hosting.md §1).
COPY tools/smoke_provider.py ./tools/smoke_provider.py

# ── Run as a non-root user ────────────────────────────────────────────
#
# Required by the GCP Marketplace container requirements, and flagged by
# every image scanner an AWS Marketplace listing goes through. It was root:
# `docker run --rm <image> id -u` printed 0.
#
# **Numeric**, not a name. Kubernetes' `runAsNonRoot: true` inspects the
# image's configured user and refuses to start a container whose USER it
# cannot prove is non-zero — a name it would have to resolve inside the
# image fails with "container has runAsNonRoot and image has non-numeric
# user". The account is still created so the uid has a name, a home and a
# shell in `/etc/passwd`, which some libraries expect to exist.
#
# 10001 rather than 1000: high enough not to collide with a uid the host
# already means something by, which matters when a volume is bind-mounted
# rather than named.
RUN groupadd --system --gid 10001 igni \
 && useradd --system --uid 10001 --gid igni --home-dir /app --shell /usr/sbin/nologin igni

# Nothing in the image needs a chown any more. DEC-13 removed Flower,
# whose state directory was the only path a named volume mounted over —
# and a named volume inherits the ownership of the directory it covers,
# which is why that one needed it.
#
# `app/` writes nothing to disk at runtime; the temporary files Python and
# its libraries create go to /tmp, which is world-writable, so nothing else
# needs an ownership change either.
USER 10001

# ── Provenance labels ────────────────────────────────────────────────
#
# Both marketplaces expect an image to say what it is and where it came
# from; scanners and `docker inspect` read these.
LABEL org.opencontainers.image.title="igni server" \
      org.opencontainers.image.description="igni server — API (sign-in, roles, SCIM, audit, inference routing)" \
      org.opencontainers.image.source="https://github.com/ignite-ember/igni-server" \
      # SPDX identifier, which is what a scanner and a marketplace review
      # read. `NOASSERTION` was the placeholder and it is the literal
      # string tooling uses for "nobody said", so it reported as an
      # unlicensed image rather than an unfinished label.
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="Ignite Ember"

# Expose FastAPI port
EXPOSE 7777

# Set default log level (can be overridden by environment variable)
ENV LOG_LEVEL=INFO

# Start FastAPI with configurable log level
CMD ["bash", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 7777 --log-level ${LOG_LEVEL,,}"]
