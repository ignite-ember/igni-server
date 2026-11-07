SHELL := /bin/bash
PYTHON := .venv/bin/python
# 7777 is what the app listens on in every compose file, the Dockerfile's
# EXPOSE, and both health checks. This said 8080 and was referenced by
# nothing at all, so the one place a reader could go looking for the port
# gave them the wrong number.
PORT ?= 7777

# ``alembic`` included deliberately: migrations are the least-reviewed
# code in a deployment and the hardest to re-run, which makes them a poor
# place to have no linter. They were unlinted until the production check
# noticed.
#
# ``tools`` was missing here while `.github/workflows/ci.yml` linted it,
# so `make lint` passed locally on code CI would reject — and did, on two
# files, for however long it took anybody to look at a CI run. The two
# lists must match; `tests/test_lint_is_the_same_locally_and_in_ci.py`
# asserts it rather than trusting this comment.
SRC := app tests alembic alembic_tenant tools
SRC_FMT := app

# Pruned to the targets that exist. It used to declare eight that did not
# — `dev`, `worker`, `worker-dev`, `migrate-docker`, and all four `*-prod`
# ones — while omitting two that did. `.PHONY` does not create rules, so
# `make up-prod` failed with "No rule to make target"; the list was a
# record of intentions rather than of targets.
#
# `run` and `worker` are added below rather than dropped, because the
# README documented `make run` as the last step of its Quick Start and
# there was no such target.
.PHONY: run test lint fmt migrate downgrade-one up down restart logs rebuild \
        refresh-default-seed default-seed-migration refresh-agent-tools \
        portal-dev portal-build portal-lint portal-test portal-test-watch \
        portal-e2e portal-e2e-update check-configs docs check-docs \
        docs-check-siblings dev-env

# Run the API locally against whatever .env points at. --reload because
# this is the development entry point; the container's CMD is the one
# without it.
#
# --reload-dir: without it uvicorn watched the whole checkout, and
# `npm ci` in portal/ (the Quick start's next step) writes .py files into
# node_modules, so the API restarted under the person installing the portal.
run:
	$(PYTHON) -m uvicorn app.main:app --host 127.0.0.1 --port $(PORT) --reload --reload-dir app

# A development .env, from dist.env: localhost Postgres and Redis, a fresh
# encryption key, the API and portal URLs, sign-in codes in the server log.
# The Quick start's second step. PG_PORT / REDIS_PORT / PORTAL_PORT when
# yours are not the defaults; FORCE=1 to replace an existing .env.
PG_PORT ?= 5432
REDIS_PORT ?= 6379
PORTAL_PORT ?= 3000
dev-env:
	$(PYTHON) tools/dev_env.py --pg-port $(PG_PORT) --redis-port $(REDIS_PORT) \
		--api-port $(PORT) --portal-port $(PORTAL_PORT) $(if $(FORCE),--force)

# Run tests
test:
	uv sync --group dev
	$(PYTHON) -m pytest -q

# Linting / type checking
lint:
	uv sync --group dev
	$(PYTHON) -m black --check $(SRC_FMT)
	$(PYTHON) -m ruff check $(SRC)
	$(PYTHON) -m mypy app

# Format code / fix lint issues
fmt:
	uv sync --group dev
	$(PYTHON) -m black $(SRC_FMT)
	$(PYTHON) -m ruff check $(SRC) --fix --unsafe-fixes || true
	$(PYTHON) -m ruff format $(SRC) || true

# Database migrations
migrate:
	$(PYTHON) -m alembic upgrade head
	$(PYTHON) -m app.db.sql.tenant_migrations

downgrade-one:
	$(PYTHON) -m alembic downgrade -1

# Docker commands - Containers folder (for isolated environments)
DOCKER_COMPOSE_DIR := containers
COMPOSE_FILES := -f docker-compose.local.yml
# The root .env the Quick start makes, for the compose files' ${...}
# values (a SECRET_ENCRYPTION_KEY of your own, a LOG_LEVEL). Compose
# would otherwise read containers/.env, which nothing tells you to make.
COMPOSE_FILES += $(if $(wildcard .env),--env-file ../.env)
# A second stack beside the first: its own compose project and container
# names (IGNI_STACK), and its own host ports (IGNI_API_PORT,
# IGNI_PORTAL_PORT, read by docker-compose.local.yml). Without a project of
# its own, `make up` in a second checkout took over the first one's
# containers — both directories are called `containers`.
COMPOSE_FILES += $(if $(IGNI_STACK),-p $(IGNI_STACK))

up:
	cd $(DOCKER_COMPOSE_DIR) && docker-compose $(COMPOSE_FILES) up --build -d

down:
	cd $(DOCKER_COMPOSE_DIR) && docker-compose $(COMPOSE_FILES) down

restart:
	cd $(DOCKER_COMPOSE_DIR) && docker-compose $(COMPOSE_FILES) restart

logs:
	cd $(DOCKER_COMPOSE_DIR) && docker-compose $(COMPOSE_FILES) logs -f

rebuild:
	cd $(DOCKER_COMPOSE_DIR) && docker-compose $(COMPOSE_FILES) up --build --force-recreate -d

# Portal (frontend)
# The portal's dev server proxies /v1 to the API on $(PORT) — the one
# `make run` serves — so the two agree without IGNI_API_URL by hand.
portal-dev:
	cd portal && IGNI_API_URL=$${IGNI_API_URL:-http://localhost:$(PORT)} npm run dev -- --port $(PORTAL_PORT)

portal-build:
	cd portal && npm run build

# Re-copy what a new org's default group is seeded with — the agents and
# skills igni bundles — from a checked-out CLI. Affects new orgs only: an
# existing Default group needs an alembic_tenant migration that freezes the
# new seed (`make default-seed-migration`, see t0025), and the suite fails
# until it has one.
IGNI_CLI ?= ../igni
refresh-default-seed:
	@test -d "$(IGNI_CLI)/src/ember_code/bundled_agents" || \
		(echo "No igni checkout at $(IGNI_CLI); pass IGNI_CLI=<path>" && exit 1)
	rm -f app/services/portal/default_group_agents/*.md app/services/portal/default_group_skills/*.md
	@mkdir -p app/services/portal/default_group_agents app/services/portal/default_group_skills
	cp "$(IGNI_CLI)"/src/ember_code/bundled_agents/*.md app/services/portal/default_group_agents/
	@for d in "$(IGNI_CLI)"/src/ember_code/bundled_skills/*/; do \
		cp "$$d/SKILL.md" "app/services/portal/default_group_skills/$$(basename $$d).md"; \
	done
	@echo "Seeded from $(IGNI_CLI): $$(ls app/services/portal/default_group_agents/*.md | wc -l | tr -d ' ') agents, $$(ls app/services/portal/default_group_skills/*.md | wc -l | tr -d ' ') skills"

# After refresh-default-seed: write the alembic_tenant migration that brings
# every existing Default group to the new seed, frozen into the migration,
# with a downgrade to the previous one. SLUG names the file.
default-seed-migration:
	@test -n "$(SLUG)" || (echo "Pass SLUG=<words_in_snake_case>, e.g. SLUG=the_default_group_follows_igni_0_7" && exit 1)
	.venv/bin/python tools/write_default_seed_migration.py --slug "$(SLUG)"

# Re-read the tool names igni offers, so the portal's typo warning does
# not start rejecting tools that were added since.
refresh-agent-tools:
	@test -d "$(IGNI_CLI)/src/ember_code" || \
		(echo "No igni checkout at $(IGNI_CLI); pass IGNI_CLI=<path>" && exit 1)
	@"$(IGNI_CLI)"/.venv/bin/python -c "\
from ember_code.core.tools.registry import ToolRegistry; \
print(chr(10).join(sorted(ToolRegistry(base_dir='.').available_tools)))" \
		| .venv/bin/python scripts/write_agent_tools.py

portal-lint:
	cd portal && npx tsc -b

# Vitest + React Testing Library + MSW (component/unit tests)
portal-test:
	cd portal && npm test

portal-test-watch:
	cd portal && npm run test:watch

# Playwright E2E (requires `make up` or PORTAL_URL pointing at a deployed env)
portal-e2e:
	cd portal && npm run test:e2e

# Refresh visual regression snapshots
portal-e2e-update:
	cd portal && npx playwright test --update-snapshots

# Validate every shipped deployment config.
#
# B2/F140. `containers/Caddyfile` had `email` inside the site block,
# where it is not a directive at all — `caddy validate` rejects the
# whole file, so the only TLS path this product documents exited on
# start. It was written that way from the beginning and nothing ever
# parsed it. A config that ships is code.
#
# The placeholder values below are only here to get past interpolation:
# `docker compose config` refuses to render without IGNI_ENV_FILE and
# IGNI_IMAGE, which is the prod file working as designed.
check-configs:
	@echo "→ Caddyfile"
	@docker run --rm \
	  -e IGNI_DOMAIN=igni.example.invalid \
	  -e IGNI_ACME_EMAIL=ops@example.invalid \
	  -v "$(PWD)/containers/Caddyfile":/etc/caddy/Caddyfile:ro \
	  caddy:2.10-alpine caddy validate --config /etc/caddy/Caddyfile 2>&1 \
	  | grep -q "Valid configuration" || { echo "   Caddyfile is not valid"; exit 1; }
	@echo "   valid"
	@echo "→ compose files"
	@cd containers && for spec in \
	    "-f docker-compose.local.yml" \
	    "-f docker-compose.prod.yml" \
	    "-f docker-compose.prod.yml -f docker-compose.tls.yml"; do \
	  IGNI_ENV_FILE=/dev/null \
	  IGNI_IMAGE=example.invalid/igni-api:0.0.0 \
	  IGNI_PORTAL_IMAGE=example.invalid/igni-portal:0.0.0 \
	  IGNI_DOMAIN=igni.example.invalid \
	  IGNI_ACME_EMAIL=ops@example.invalid \
	  docker compose $$spec config > /dev/null || { echo "   $$spec is not valid"; exit 1; }; \
	  echo "   $$spec valid"; \
	done

# ── The generated compliance documents ───────────────────────────────
#
# Five documents are generated from the code and the register rather than
# written: the Article 30 record (B4), the Statement of Applicability
# (C1), the risk assessment (C2), the policies (C3) and the incident
# response plan (C4). Each has a test that fails when the committed file
# and its generator disagree.
#
# They form a chain. The register's statuses feed the SoA, the SoA's
# dispositions feed the risk assessment, and the risk assessment's
# treatment plan quotes each row's status back — so changing one register
# row can make three documents stale at once. That happened the first
# time LG-8 moved from `Process` to `Partial`: the suite failed on the
# risk assessment, which is the chain working and also a good way to
# forget one.
#
# So: one target. `make docs` after touching COMPLIANCE_TRACKER.md.
DOC_GENERATORS := \
	generate_records_of_processing \
	generate_statement_of_applicability \
	generate_risk_assessment \
	generate_policies \
	generate_incident_response \
	generate_dpia \
	generate_vulnerability_management \
	generate_supplier_register \
	generate_decisions \
	generate_awareness_training \
	generate_internal_audit

docs:
	@for g in $(DOC_GENERATORS); do $(PYTHON) tools/$$g.py || exit 1; done

# What CI would call: the same generators, refusing to write.
check-docs:
	@for g in $(DOC_GENERATORS); do $(PYTHON) tools/$$g.py --check || exit 1; done

# Ask the cross-repo checks what the code in a sibling checkout says
# today. Never changes a committed file — the document is generated
# from this repository alone, on purpose, so that it means the same
# thing in CI as on a machine that happens to have the sibling.
docs-check-siblings:
	@$(PYTHON) tools/generate_decisions.py --check-siblings

