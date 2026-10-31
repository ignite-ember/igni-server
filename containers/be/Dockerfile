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
FROM python:3.13-slim AS builder

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
FROM python:3.13-slim

WORKDIR /app

# Copy requirements.txt from builder
COPY --from=builder /app/requirements.txt ./

# Install dependencies. pip is upgraded first because the base image's
# pip stays in the final layer as an install-time tool, and `pip-audit`
# run inside the built image reported seven advisories against it —
# unreachable without code execution in the container, but free to fix.
# No torch here. Embedding is igni__artifacts' own image (its
# `embedder/`); this server makes no vectors, and this image is pulled
# per API replica and by two marketplaces.
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

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
