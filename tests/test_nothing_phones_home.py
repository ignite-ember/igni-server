"""The air-gap claim, defended by a rule instead of by good intentions.

DP-5 in `COMPLIANCE_TRACKER.md`. `self-hosting.md` §8 has asked for this
since long before the production review:

    Clean. Nothing in `app/` phones home. The analytics snippet that was in
    the portal HTML, gated on hostname, left with the public site when it
    moved to its own repository; the portal has none. Worth an explicit
    test so it stays that way, because a single well-meaning
    error-reporting SDK would break the air-gap claim silently.

It is worth more than a tidiness check. `COMPLIANCE.md` sets out why: a
self-hosted deployment where no customer data reaches us usually makes the
**customer the controller** and us neither controller nor processor, which
removes the most expensive part of a GDPR posture — no per-customer DPA,
no transfer mechanism, and a breach here cannot expose their code. All of
that is contingent on nothing phoning home. The claim is the asset.

Two findings in this review were already attacks on it: F75, where a
plugin README could exfiltrate via `background-image: url(...)`, and F83,
where every accepted webhook wrote contributor emails into a log stream
bound for whatever aggregator the deployment ships to.

So this file enforces three things:

1. no telemetry or error-reporting SDK in either dependency tree;
2. every host named in `app/` is on an allow-list, with a reason;
3. the portal loads no analytics tag at all. It used to carry one,
   gated on the vendor's hostname, for the public site that shared its
   bundle; the site is its own repository now.

The second is the load-bearing one, and it is deliberately a rule with an
explicit allow-list rather than a blocklist of known vendors — a blocklist
only knows about the telemetry companies that exist today. The allow-list
doubles as documentation of every host this codebase can name, which is
the thing an air-gap review actually asks for.
"""

from __future__ import annotations

import ast
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_APP = _ROOT / 'app'

# ── Hosts `app/` may name, and why ───────────────────────────────────
#
# Every entry is either somewhere the *customer* chose to send data or an
# example in a docstring or schema description. Adding one means saying
# which, in the comment, before the test will pass.
# Shorter than a `grep` over `app/` suggests, and deliberately: the scan
# reads string literals only, so a host named in a docstring or a comment
# does not appear. Three entries were removed on the first run for exactly
# that reason — the staleness check below caught them, which is the guard
# working on its own author.
_ALLOWED_HOSTS: dict[str, str] = {
    # Google, reached only if the customer configured Google sign-in.
    'accounts.google.com': 'Google OAuth, only when the customer enables it',
    'oauth2.googleapis.com': 'Google token exchange',
    'www.googleapis.com': 'Google userinfo, after the token exchange',
    # Cloud model providers, reached only through a backend an operator
    # configured with that cloud's kind (app/services/ai/providers). There
    # is no default backend of any cloud kind.
    'aiplatform.googleapis.com': "Vertex AI's global endpoint, only for a vertex_openai backend in region global",
    'cognitiveservices.azure.com': 'the Entra ID scope Azure OpenAI tokens are issued for, never fetched',
    'api.anthropic.com': "Anthropic's API, only for an anthropic backend that names no other address",
    'bedrock-runtime.{}.amazonaws.com': "a region's Bedrock host, only for a bedrock backend in that region",
    '{}-aiplatform.googleapis.com': "a region's Vertex AI host, only for a Vertex backend in that region",
    # Not hosts.
    'www.w3.org': 'an XML namespace in an SVG, never fetched',
    # Examples in docstrings, schema descriptions and log samples.
    'localhost': 'a local default or an example',
    'gpu-3': 'an inference-node example',
    'mcp_gateway': 'a compose service name in an example',
}

# Packages whose entire purpose is to send data to a third party. A
# blocklist is the wrong shape for hosts and the right shape here: the
# failure mode is somebody adding one of these by name, and the list of
# plausible additions is short and well known.
_TELEMETRY_PACKAGES = (
    'sentry-sdk',
    'posthog',
    'mixpanel',
    'segment-analytics-python',
    'analytics-python',
    'datadog',
    'datadog-api-client',
    'ddtrace',
    'bugsnag',
    'bugsnag-python',
    'rollbar',
    'pyrollbar',
    'newrelic',
    'elastic-apm',
    'scout-apm',
    'honeycomb-beeline',
    'honeycomb-opentelemetry',
    'logrocket',
    'azure-monitor-opentelemetry',
    'azure-monitor-opentelemetry-exporter',
    'opentelemetry-exporter-gcp-trace',
    'aws-xray-sdk',
    'logfire',
    'langfuse',
    'langsmith',
    'arize-otel',
    'arize-phoenix-otel',
    'agentops',
    'openlit',
    'traceloop-sdk',
    'wandb',
    'lunary',
    'opik',
    'braintrust',
)

# The OTLP exporters, allowed by name and for one reason each. They were on
# the list above until igni server could trace to the customer's collector
# (app/observability/otel.py). An exporter is a means of sending data; these
# send only where the operator says:
#
# - there is no default endpoint: with OTEL_EXPORTER_OTLP_ENDPOINT unset no
#   provider is built and nothing is exported;
# - the endpoint is the operator's: read from the process environment and
#   nothing else, not the .env file, the database or an admin screen;
# - request bodies, the query string and the caller's address are not
#   exported.
#
# `tests/test_traces_go_only_to_the_operators_collector.py` proves each; the
# test below checks those proofs are still there.
_OTLP_EXPORTERS: dict[str, str] = {
    'opentelemetry-exporter-otlp-proto-http': "the operator's collector, over OTLP/HTTP",
    'opentelemetry-exporter-otlp-proto-grpc': "the operator's collector, over OTLP/gRPC",
    'opentelemetry-exporter-otlp-proto-common': 'the encoding both exporters share',
    'opentelemetry-exporter-otlp-common': 'the encoding both exporters share',
    'opentelemetry-exporter-http-transport': "the HTTP exporter's transport",
}

# Hosts that may never be named in app/, whatever the reason: Agno's own
# servers. igni's client runs on Agno, which posts every run to
# os-api.agno.com unless told not to; the server does not use Agno, and must
# not start naming its hosts.
_NEVER_NAMED_SUFFIXES = ('agno.com',)

_JS_TELEMETRY_PACKAGES = (
    '@sentry/browser',
    '@sentry/react',
    '@sentry/vue',
    'posthog-js',
    'mixpanel-browser',
    '@amplitude/analytics-browser',
    '@datadog/browser-rum',
    '@datadog/browser-logs',
    'logrocket',
    'bugsnag-js',
    '@bugsnag/js',
    'rollbar',
    'fullstory',
    '@fullstory/browser',
)

_HOST_IN_URL = re.compile(r'https?://([A-Za-z0-9._%{}-]+)')


def _code_strings(source: str) -> list[str]:
    """Every string literal in the module that is not a docstring.

    Docstrings are excluded because a host named in prose cannot make a
    request, and including them makes the allow-list a catalogue of
    examples rather than of reachable hosts. Comments never reach the AST
    at all, so they need no handling.
    """
    tree = ast.parse(source)

    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, 'body', [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    # An f-string is read whole, a `{}` where each value goes, so the host it
    # builds — `https://bedrock-runtime.{}.amazonaws.com` — is seen as one,
    # rather than as fragments the allow-list would have to name.
    in_fstrings: set[int] = set()
    rendered: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                in_fstrings.add(id(value))
                parts.append(str(value.value) if isinstance(value, ast.Constant) else '{}')
            rendered.append(''.join(parts))

    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and id(node) not in in_fstrings
    ] + rendered


def _hosts_named_in_app() -> dict[str, list[str]]:
    """host → the files naming it."""
    found: dict[str, list[str]] = {}
    for path in sorted(_APP.rglob('*.py')):
        for literal in _code_strings(path.read_text()):
            for host in _HOST_IN_URL.findall(literal):
                found.setdefault(host, []).append(str(path.relative_to(_ROOT)))
    return found


class TestTheScanIsMeasuringSomething:
    """Two leak sweeps in this review looked like success while measuring
    nothing, and a dependency check earlier today reported every package as
    present because `grep -c` plus `|| echo 0` printed "0\\n0". A rule that
    cannot fail is worse than no rule."""

    def test_it_reads_the_application(self):
        files = list(_APP.rglob('*.py'))

        assert len(files) > 100, f'only found {len(files)} modules under app/'

    def test_it_finds_the_hosts_it_is_checking(self):
        hosts = _hosts_named_in_app()

        assert len(hosts) >= 5, sorted(hosts)
        # Google's sign-in hosts are definitely in there; if they are
        # missing the extraction has stopped working.
        assert 'accounts.google.com' in hosts
        assert 'oauth2.googleapis.com' in hosts

    def test_it_can_tell_a_docstring_from_code(self):
        source = '"""A module docstring naming https://telemetry.example.com."""\nURL = "https://real.example.com"\n'

        strings = _code_strings(source)

        assert any('real.example.com' in s for s in strings)
        assert not any('telemetry.example.com' in s for s in strings)


class TestNoHostIsNamedWithoutAReason:
    def test_every_host_in_app_is_allow_listed(self):
        """The load-bearing check.

        An allow-list rather than a blocklist: a blocklist knows only about
        the telemetry vendors that exist today, and the thing being
        defended is "no data leaves the customer's network", not "not to
        these companies".
        """
        found = _hosts_named_in_app()
        unknown = sorted(set(found) - set(_ALLOWED_HOSTS))

        assert not unknown, (
            'these hosts are named in app/ and are not on the allow-list:\n'
            + '\n'.join(f'  {host} — in {sorted(set(found[host]))}' for host in unknown)
            + '\n\nThe self-hosted claim is that no customer data leaves their network, '
            'and COMPLIANCE.md rests the GDPR position on it. If this host is somewhere '
            'the customer chose to send data, or an example in a schema description, add '
            'it to _ALLOWED_HOSTS with that reason.'
        )

    def test_no_agno_host_is_named(self):
        named = [h for h in _hosts_named_in_app() if h.lower().endswith(_NEVER_NAMED_SUFFIXES)]
        listed = [h for h in _ALLOWED_HOSTS if h.lower().endswith(_NEVER_NAMED_SUFFIXES)]

        assert not named and not listed, f"Agno's servers are named in app/: {named + listed}"

    def test_the_allow_list_has_not_gone_stale(self):
        """An entry for a host nothing names any more is a reason nobody
        checked. Removing it keeps the list readable as documentation."""
        found = _hosts_named_in_app()
        stale = sorted(set(_ALLOWED_HOSTS) - set(found))

        assert not stale, (
            f'these allow-list entries name hosts app/ no longer mentions: {stale}. '
            'Remove them — the list is meant to be a readable inventory of what this '
            'codebase can reach.'
        )

    def test_every_reason_says_something(self):
        empty = sorted(host for host, why in _ALLOWED_HOSTS.items() if len(why.strip()) < 10)

        assert not empty, f'allow-list entries with no real reason: {empty}'


class TestNoTelemetryDependency:
    def test_the_python_lock_has_none(self):
        """`uv.lock` rather than `pyproject.toml`: a telemetry package
        arriving as somebody else's transitive dependency reaches the
        network just as easily as a direct one."""
        lock = (_ROOT / 'uv.lock').read_text()
        present = [pkg for pkg in _TELEMETRY_PACKAGES if re.search(rf'^name = "{re.escape(pkg)}"$', lock, re.M)]

        assert not present, (
            f'telemetry or error-reporting packages in the dependency tree: {present}. '
            'self-hosting.md §8: "a single well-meaning error-reporting SDK would break '
            'the air-gap claim silently."'
        )

    def test_every_exporter_is_one_the_operator_points(self):
        lock = (_ROOT / 'uv.lock').read_text()
        exporters = re.findall(r'^name = "(opentelemetry-exporter-[^"]+)"$', lock, re.M)
        unexplained = sorted(set(exporters) - set(_OTLP_EXPORTERS))

        assert exporters, 'no OTLP exporter in the lock; is the allowance still needed?'
        assert not unexplained, f'OpenTelemetry exporters with no reason to be here: {unexplained}'
        proofs = (_ROOT / 'tests/test_traces_go_only_to_the_operators_collector.py').read_text()
        for proof in (
            'def test_with_no_endpoint_nothing_is_set_up',
            'def test_the_endpoint_comes_from_the_environment_only',
            'def test_a_request_is_traced_without_its_body',
        ):
            assert proof in proofs, f'the reason the OTLP exporters are allowed lost its test: {proof}'

    def test_the_scan_would_notice_one(self):
        """Proving the regex matches the `uv.lock` format, since a pattern
        that never matches passes the check above forever."""
        lock = (_ROOT / 'uv.lock').read_text()

        # A package that is definitely there, matched the same way.
        assert re.search(r'^name = "fastapi"$', lock, re.M), (
            'the lock-file format has changed and the telemetry scan is matching nothing'
        )

    def test_the_portal_has_none(self):
        import json

        manifest = json.loads((_ROOT / 'portal/package.json').read_text())
        declared = {**manifest.get('dependencies', {}), **manifest.get('devDependencies', {})}
        present = [pkg for pkg in _JS_TELEMETRY_PACKAGES if pkg in declared]

        assert not present, f'browser telemetry packages in the portal: {present}'
        assert declared, 'no dependencies parsed — has package.json changed shape?'


class TestThePortalHasNoAnalyticsTag:
    """The portal used to load Google Tag Manager, gated on the vendor's
    own hostname, because the marketing site was the same bundle. The
    public site moved to its own repository and took the tag with it, so
    the rule is now the simpler and stronger one: **the portal ships no
    analytics tag at all**, on any host. A gate is a thing that can be
    written the wrong way round; an absence cannot.
    """

    #: What an analytics or tag-manager loader looks like in markup or
    #: code. Hosts rather than function names where possible — a host is
    #: what actually leaves the network.
    _TAG = re.compile(
        r'googletagmanager\.com|google-analytics\.com|\bgtag\(|plausible\.io|'
        r'static\.cloudflareinsights\.com|cdn\.segment\.com|matomo\.(?:js|php)|'
        r'js\.hs-scripts\.com|cdn\.mxpnl\.com|clarity\.ms|hotjar\.com',
        re.I,
    )

    def _shipped(self) -> dict[str, str]:
        """Everything that ends up in the bundle: the HTML shell, the
        public directory copied verbatim, and the source Vite builds."""
        portal = _ROOT / 'portal'
        files = [portal / 'index.html']
        for directory in ('public', 'src'):
            files += [
                f
                for f in (portal / directory).rglob('*')
                if f.is_file()
                and f.suffix in {'.html', '.ts', '.tsx', '.js', '.jsx', '.svg', '.css', '.json'}
                and '__tests__' not in f.parts
            ]
        return {str(f.relative_to(_ROOT)): f.read_text(errors='ignore') for f in files}

    def test_the_scan_reads_the_bundle(self):
        shipped = self._shipped()
        assert 'portal/index.html' in shipped
        assert any(name.startswith('portal/src/') for name in shipped), 'read no portal source, so it proves nothing'

    def test_the_pattern_sees_the_tag_that_used_to_be_there(self):
        """The snippet the shell carried before the public site moved out.
        A pattern that cannot see its own originating input passes
        everything."""
        old = "<script async src='https://www.googletagmanager.com/gtag/js?id=G-XXXX'></script>"
        assert self._TAG.search(old)

    def test_nothing_in_the_portal_loads_one(self):
        found = sorted(name for name, text in self._shipped().items() if self._TAG.search(text))
        assert not found, (
            f'an analytics tag in the portal: {found}. A self-hosted portal would report '
            "its users' activity off the customer's network on every page load"
        )


class TestThePortalBundleNamesNoCdn:
    """The portal fetches nothing from a CDN, which is the same claim from
    the browser's side.

    B34: the content editor used `@monaco-editor/react`, whose loader
    injects a <script> for cdn.jsdelivr.net unless it is handed a Monaco
    instance. On an air-gapped install the editor never appeared. Nothing
    above could see it — the URL was in a dependency, not in `src/` — so
    the authoritative check reads the build: `portal/scripts/check-no-cdn.mjs`
    scans `dist/`, and the portal image runs it after `npm run build`, so
    an image that names a CDN does not build. These tests hold that wiring
    in place, and repeat the scan here whenever a build is lying around.
    """

    _PORTAL = _ROOT / 'portal'
    _CHECK = _PORTAL / 'scripts/check-no-cdn.mjs'

    def _cdn_hosts(self) -> list[str]:
        """The hosts the build check refuses, read from the check itself so
        there is one list."""
        source = self._CHECK.read_text()
        block = re.search(r'export const CDN_HOSTS = \[(.*?)\]', source, re.S)
        assert block, 'CDN_HOSTS is no longer where this test reads it'
        hosts = re.findall(r"'([^']+)'", block.group(1))
        assert 'cdn.jsdelivr.net' in hosts and 'unpkg.com' in hosts, hosts
        return hosts

    def test_the_portal_image_runs_the_check_on_its_build(self):
        dockerfile = (self._PORTAL / 'Dockerfile').read_text()

        assert re.search(r'npm run build && node scripts/check-no-cdn\.mjs', dockerfile), (
            'portal/Dockerfile no longer runs scripts/check-no-cdn.mjs after the build, '
            'so a CDN URL arriving in a dependency would ship unseen'
        )

    def test_the_editor_hands_the_loader_the_bundled_monaco(self):
        setup = (self._PORTAL / 'src/lib/monaco.ts').read_text()
        wrapper = (self._PORTAL / 'src/components/MonacoEditor.tsx').read_text()

        assert 'loader.config({ monaco })' in setup
        assert re.search(r"^import \* as monaco from 'monaco-editor/", setup, re.M)
        assert "import '../lib/monaco'" in wrapper

    def test_the_portal_source_names_no_cdn(self):
        shipped = TestThePortalHasNoAnalyticsTag()._shipped()
        found = sorted(
            f'{host} in {name}' for name, text in shipped.items() for host in self._cdn_hosts() if host in text
        )

        assert not found, f'the portal names a CDN an air-gapped install cannot reach: {found}'

    def test_a_built_portal_names_no_cdn(self):
        """Only when `portal/dist` exists — CI builds it in the image, where
        the check above already runs; locally this catches it sooner."""
        import pytest

        assets = self._PORTAL / 'dist/assets'
        if not (assets.is_dir() and any(assets.glob('*.js'))):
            pytest.skip('no portal build; the portal image runs the same scan on its own')
        found = sorted(
            f'{host} in {path.name}'
            for path in assets.rglob('*')
            if path.is_file()
            for host in self._cdn_hosts()
            if host in path.read_text(errors='ignore')
        )

        assert not found, f'the built portal names a CDN: {found}'

    def test_the_scan_would_see_the_url_that_broke_the_editor(self):
        old = "vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.55.1/min/vs'"

        assert any(host in old for host in self._cdn_hosts())
