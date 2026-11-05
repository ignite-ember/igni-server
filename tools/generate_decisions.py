"""Generate `docs/DECISIONS.md` — the calls that are not engineering's.

B6. The road row said "the nine long-standing open decisions […] listed
at the end of the review log". Auditing that turned up three things
wrong with it at once:

* **there are not nine.** Five decisions were ever recorded, in two
  different notations — `### DEC-nn` headings for three of them, and
  "left as a decision" paragraphs inside findings F55 and F94 for the
  other two;
* **they are not all at the end.** Two are buried mid-document, which is
  why a reader looking where the row points finds three;
* **one of them had already been answered.** DEC-10 asked for real token
  revocation. B5 built it. It sat in the document reading as an open
  question for a pass, which is F139's failure aimed at the one list
  whose whole purpose is to be read by somebody deciding what to do
  next.

So this file exists, and it is generated because of that third point.
**Each decision declares what would resolve it, as a check against the
code**, and the state is derived: `answered` when the check passes,
`open` when it does not. A decision cannot go on asking a question the
code has already settled, and cannot be marked settled before it is.

That is the only part of a decision register a machine can own. What
each option costs, and which to take, is written out and left to a
person — with a recommendation, because "here are three options" without
one is a way of not helping.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_TRACKER = _ROOT / 'docs/COMPLIANCE_TRACKER.md'
_READINESS = _ROOT / 'docs/REVIEW_FINDINGS.md'
_IGNI = _ROOT.parent / 'igni'
_CODE_INDEX = _ROOT.parent / 'igni__code-index'
_OUTPUT = _ROOT / 'docs/DECISIONS.md'


@dataclass(frozen=True)
class Decision:
    ref: str
    question: str
    #: Where it was raised. Checked to exist.
    origin: tuple[str, ...]
    #: What it costs to leave undecided. The reason a register of
    #: decisions is not just a backlog.
    cost_of_waiting: str
    options: tuple[tuple[str, str], ...]
    recommendation: str
    #: Name of the resolver below. Returns True when the code has
    #: answered the question.
    resolver: str
    #: What a person decided, and when. A decision can be decided and
    #: not yet built, which is neither `open` nor `answered` — see
    #: `state_of`.
    decided: str = ''
    #: Set when resolution cannot be checked from this repository.
    unverifiable: str = ''
    #: Name of a sibling checkout the evidence lives in, when it does.
    #:
    #: Such a decision's state is taken from ``decided`` and never from
    #: its resolver, because the resolver's answer depends on whether
    #: somebody happens to have that repository next to this one — and
    #: on which branch it has checked out. A document that changes with
    #: the reader's filesystem cannot be committed and checked, which is
    #: exactly what happened: the generator is green on a developer's
    #: machine, where ``../igni`` exists, and red in CI, where it
    #: never does. The resolver is still run by ``--check-siblings``,
    #: which reports without touching the document.
    evidence_in: str = ''
    notes: str = ''
    #: Register rows this touches.
    rows: tuple[str, ...] = field(default_factory=tuple)


# ── Resolvers: what it would look like for the code to have answered ──


def _portal_routes() -> str:
    path = _ROOT / 'portal/src/App.tsx'
    return path.read_text() if path.exists() else ''


def revocation_is_built() -> bool:
    """DEC-10. Answered when a route withdraws sessions and AC-11 is
    `Verified` — both, because either alone could be true while the
    other was not."""
    from app.main import app

    served = set(app.openapi()['paths'])
    return (
        '/v1/portal/me/revoke-sessions' in served
        and '/v1/portal/team/{user_id}/revoke-sessions' in served
        and register_rows().get('AC-11') == 'Verified'
    )


def updater_endpoint_is_configurable() -> bool:
    """DEC-11. Answered when the app reads a release endpoint from
    settings.

    This checked ``'github.com' not in tauri.conf.json`` — which is not
    what the decision asked for, and the resolver's own docstring said
    so: it named two conditions ("no longer hardcodes a single release
    host, **or** the app reads an endpoint from settings") and the code
    checked one. The chosen option was a configurable endpoint with the
    compiled one as the fallback, so the config still names a default
    and correctly so. The old check would have called that `open`
    forever.

    Read from the Rust side, because that is the process that makes the
    request: a check that consults a setting one process away can be
    reached without consulting it.
    """
    lib = _IGNI / 'clients/tauri/src-tauri/src/lib.rs'
    if not lib.exists():
        return False
    source = lib.read_text()
    return 'update_endpoint' in source and 'endpoints(' in source


def old_landing_route_is_gone() -> bool:
    """DEC-12. Answered when `/old` no longer renders the retired page."""
    routes = _portal_routes()
    if not routes:
        return False
    return 'LandingPageOld' not in routes


def celery_question_is_settled() -> bool:
    """F55. Answered either way: a dispatcher exists, or the worker and
    Flower are gone from the compose file. Both are resolutions; what
    cannot persist is a worker serving a queue nothing writes to."""
    dispatched = any(
        re.search(r'\.(delay|apply_async)\(|send_task\(', py.read_text()) for py in (_ROOT / 'app').rglob('*.py')
    )
    compose = (_ROOT / 'containers/docker-compose.prod.yml').read_text()
    removed = 'celery_worker:' not in compose and 'flower:' not in compose
    return dispatched or removed


def cli_api_url_default_is_gone() -> bool:
    """DP-5a. Answered when the CLI no longer defaults `api_url` to a
    vendor host."""
    for candidate in ('src/ember_code/core/config/settings.py', 'src/ember_code/config.py'):
        path = _IGNI / candidate
        if path.exists():
            text = path.read_text()
            match = re.search(r'api_url[^=\n]*=\s*[\'"]([^\'"]*)[\'"]', text)
            if match:
                return match.group(1) == ''
    return False


def token_lifetime_was_revisited() -> bool:
    """DEC-15. Answered when the default stops being thirty days, or a
    setting exists to change it. Neither is true yet, and the honest
    resolution may be "thirty days, deliberately" — which this cannot
    detect, so it is marked unverifiable rather than pretending."""
    source = (_ROOT / 'app/services/auth/jwt_service.py').read_text()
    return 'timedelta(days=30)' not in source


RESOLVERS = {
    'revocation_is_built': revocation_is_built,
    'updater_endpoint_is_configurable': updater_endpoint_is_configurable,
    'old_landing_route_is_gone': old_landing_route_is_gone,
    'celery_question_is_settled': celery_question_is_settled,
    'cli_api_url_default_is_gone': cli_api_url_default_is_gone,
    'token_lifetime_was_revisited': token_lifetime_was_revisited,
}


DECISIONS: tuple[Decision, ...] = (
    Decision(
        'DEC-10',
        'Should a signed token be revocable while the account exists?',
        ('§89', 'F122'),
        'None any more. This is the entry that made the register worth generating: B5 built it '
        'and the document went on asking the question for a pass.',
        (
            (
                'A deny list in Redis',
                'Closes it completely, adds a round trip to every request and a new failure mode when Redis is away.',
            ),
            (
                'Short-lived access tokens with refresh',
                "The standard answer, and a change to every client including the CLI's stored credential.",
            ),
            ('A `tokens_valid_after` column', 'One column, one comparison, on a row the request already reads.'),
        ),
        'The third, and it was built — §89. `POST /v1/portal/me/revoke-sessions` and an '
        "org-scoped admin twin, compared against the token's `iat` at F122's single choke "
        'point.',
        'revocation_is_built',
        rows=('AC-11',),
    ),
    Decision(
        'DEC-11',
        'Where does a customer get their updates?',
        ('F126',),
        'A self-hosted install contacts `github.com/ignite-ember/igni/releases` on launch unless '
        'the customer turns checking off. It is the one outbound host a default install has, '
        'which sits awkwardly beside DP-5 — and an air-gapped customer cannot update at all.',
        (
            (
                'A configurable endpoint',
                'Tauri 2 supports it. The customer mirrors releases and '
                'points the app at their own host; signature verification is unchanged because the '
                'pubkey is separate from the address, so a mirror cannot substitute a build.',
            ),
            (
                "Updates through the customer's own igni server",
                'Tidier for the operator, and it makes the server a release distribution point it currently is not.',
            ),
            ('Leave it and document it', 'One outbound host, off by one setting.'),
        ),
        'The first. It changes what a release *is* — a mirrored artefact somebody publishes — '
        'which is why it is a decision rather than a change.',
        'updater_endpoint_is_configurable',
        decided='A configurable endpoint. It lives in `igni`, so this reads `decided` '
        'until the Tauri config stops naming a single host — the state that did not exist '
        'before this decision was taken.',
        unverifiable='The evidence is in `igni`, a separate repository. Run '
        '`make docs-check-siblings` beside a checkout of it to see what the code says today.',
        evidence_in='igni',
        rows=('DP-5',),
    ),
    Decision(
        'DEC-12',
        'Should the retired landing page stay routed?',
        ('§75',),
        'Two landing pages that disagree about the product, one reachable by anyone who guesses '
        'the URL: `/old` offers a "Public Beta" badge, a "Download Windows" button to a macOS '
        'visitor and a "Pricing" nav item, against a live page that says igni itself is free.',
        (
            (
                'Delete it',
                'What "drop all the backward compat" points at. Nothing links to `/old` from anywhere in the app.',
            ),
            (
                'Keep it deliberately',
                'As a comparison during design work — and fix its copy so the two pages stop contradicting each other.',
            ),
            (
                'Keep the route, drop the page',
                'Redirect `/old` to `/`, the way `/alt` already '
                'does, so an old shared link still lands somewhere correct.',
            ),
        ),
        'The third, then the first once nobody is opening it. A public surface disappearing is '
        'not a side effect of a baselining task.',
        'old_landing_route_is_gone',
        decided='Redirect now, delete later. `/old` sends a visitor to `/`, so the contradiction '
        'was gone without a public URL vanishing in the same change. The "later" half is done: '
        'the page file was deleted on 2026-10-31, before this repository was published, and '
        'the redirect stays.',
    ),
    Decision(
        'DEC-13',
        'The Celery worker and Flower serve a queue nothing writes to. Wire it or remove it?',
        ('F55', '§91'),
        'Production runs a worker at `--concurrency=200`, a Flower container, a Redis database '
        'and a Postgres result backend for a queue that is always empty. Re-checked in B8: still '
        'no dispatcher, no schedule, and the two services still in the compose file. Meanwhile '
        'the docstring\'s promise — "on a schedule to recover from missed webhooks" — is a real '
        'need that nothing meets.',
        (
            (
                'Wire the triggers',
                'An admin endpoint and a schedule. Recovering from missed '
                'webhooks is a genuine gap, and the task itself is sound — bounded retries, '
                'idempotent upsert, safe to requeue.',
            ),
            (
                'Remove the worker and Flower',
                'The pipeline moved to code-index with the rest of '
                'the indexing work. Two containers, a Redis database and a result backend come out '
                'of the compose file.',
            ),
        ),
        'No recommendation, and that is the point: the two answers are opposite and both '
        'plausible. Deleting a worker on the assumption that a feature was abandoned is not a '
        'call to make from the outside.',
        'celery_question_is_settled',
        decided='Remove the worker and Flower. Two containers, a Redis database, a Postgres '
        'result backend, `celery` and `flower` as dependencies, and a guide to operating all of '
        'it came out; the indexing work it was written for lives in `igni__code-index`.\n\n'
        'It does not close the gap. Recovering from missed webhooks is still a real need that '
        'nothing meets — the same as before, except that the code no longer implies otherwise. '
        'It is CodeIndex\'s to meet: igni server no longer receives webhooks, and repository '
        'integration is CodeIndex\'s (`docs/ARCHITECTURE.md`). '
        '`tests/test_the_worker_is_gone.py` keeps the removal complete and keeps that sentence '
        'from being quietly dropped along with the worker.',
    ),
    Decision(
        'DEC-14',
        'Should the CLI keep defaulting `api_url` to a vendor host?',
        ('F94',),
        'DP-5a. The decision is already made — "there is to be no vendor endpoint" — and the '
        'change is not: flipping the default to empty would produce malformed requests instead '
        'of a clear refusal, so doing it properly means a refusal path at the call sites. It is '
        'allow-listed as an open question in the code itself rather than passing quietly.',
        (
            (
                'Empty default plus a refusal path',
                'The F69 pattern: a misconfiguration fails '
                'loudly instead of doing something silently wrong. Affects every installed CLI.',
            ),
            (
                'Keep the default until the cloud routing exists',
                'The stated future plan is a URL '
                "pointing at the customer's own cloud, at which point there is no vendor to "
                'default to.',
            ),
        ),
        'The first, and it lives in `igni` — which is why B3 is `blocked` here rather than open.',
        'cli_api_url_default_is_gone',
        decided='Empty default plus a refusal path, in `igni`.',
        unverifiable='The evidence is in `igni`, a separate repository. Run '
        '`make docs-check-siblings` beside a checkout of it to see what the code says today.',
        evidence_in='igni',
        rows=('DP-5',),
    ),
    Decision(
        'DEC-15',
        'Is thirty days the right token lifetime, now that revocation exists?',
        ('§89',),
        'DEC-10 paired the two questions and answered one. Thirty days was the *only* bound on a '
        'leaked credential before B5; it is now the second line behind withdrawal, which is a '
        'different trade-off — and nobody has revisited the number since it stopped being the '
        'only defence.',
        (
            (
                'Leave it at thirty days',
                'Revocation is the control now, and a shorter window '
                'costs every developer a re-authentication they did not need.',
            ),
            (
                'Shorten it',
                'A smaller window for a token nobody notices has leaked. The cost is '
                'sign-in friction, which for a CLI stored credential is felt.',
            ),
            (
                'Make it configurable',
                'The customer decides. Adds a setting to get wrong, and the startup checks would have to bound it.',
            ),
        ),
        'Leave it, and record that it was reconsidered rather than merely inherited. The '
        'argument for thirty days is stronger *with* revocation than it was without.',
        'token_lifetime_was_revisited',
        decided='Thirty days, deliberately. The argument is stronger with revocation than it was '
        'without: withdrawal is the control now, and a shorter window buys a smaller leak '
        'exposure at the cost of a re-authentication every developer would feel. This entry is '
        'the record that it was reconsidered rather than inherited — which is the whole of what '
        'was missing, and why `decided` is the right state for it rather than `answered`.',
        unverifiable='A decision to keep thirty days deliberately looks identical in the code to '
        'never having asked. This is the one entry whose `answered` state a check cannot '
        'establish, and saying so is better than a resolver that lies.',
    ),
)


def register_rows() -> dict[str, str]:
    rows: dict[str, str] = {}
    for match in re.finditer(
        r'^\|\s*([A-Z]{2}-\d+)\s*\|([^|]*)\|([^|]*)\|\s*`?([A-Za-z]+)`?\s*\|', _TRACKER.read_text(), re.M
    ):
        rows[match.group(1)] = match.group(4)
    return rows


def review_references() -> set[str]:
    """Findings and sections `REVIEW_FINDINGS.md` indexes."""
    text = _READINESS.read_text()
    return set(re.findall(r'^\| (F\d+) \|', text, re.M)) | {f'§{n}' for n in re.findall(r'^\| §(\d+) \|', text, re.M)}


def state_of(decision: Decision) -> str:
    """Four states, and the third is the one this file learned it needed.

    The original design had two: the check passes, or it does not. That
    conflates "nobody has decided" with "somebody decided and the work
    is not done" — and it is the *first* of those a reader acts on, by
    going and asking for a decision that has already been made. Which is
    F139 again, in the file written to fix F139.

    `decided` outranks `unknown` deliberately. A person choosing is a
    fact about the world, not about this repository, so a resolver that
    cannot reach a sibling checkout does not make the choice unknown.

    A decision whose evidence lives in a sibling checkout does not
    consult its resolver at all. The intent used to be handled by the
    ``except`` below — a resolver that could not look returned `False`,
    which reads as "checked, and the code has not done it" rather than
    "could not check". But even fixing that leaves the document a
    function of the reader's filesystem: with the sibling present the
    answer also depends on which branch it has checked out. So the
    committed document is generated from this repository alone, and
    ``--check-siblings`` reports the live answer separately.
    """
    if decision.evidence_in:
        return 'decided' if decision.decided else 'open'
    resolver = RESOLVERS[decision.resolver]
    try:
        built = resolver()
    except Exception:  # noqa: BLE001 — a resolver that cannot look must not crash the render
        return 'decided' if decision.decided else 'unknown'
    if built:
        return 'answered'
    return 'decided' if decision.decided else 'open'


def unknown_origins() -> list[str]:
    known = review_references()
    return sorted({o for d in DECISIONS for o in d.origin if o not in known})


def unknown_rows() -> list[str]:
    known = register_rows()
    return sorted({r for d in DECISIONS for r in d.rows if r not in known})


def decisions_without_options() -> list[str]:
    """A decision with one option is not a decision."""
    return sorted(d.ref for d in DECISIONS if len(d.options) < 2)


def decisions_without_a_cost() -> list[str]:
    """The field that stops this being a backlog: what it costs to keep
    waiting. A decision nobody can price is one nobody prioritises."""
    return sorted(d.ref for d in DECISIONS if len(d.cost_of_waiting) < 60)


def render() -> str:
    states = {d.ref: state_of(d) for d in DECISIONS}
    open_ones = [d for d in DECISIONS if states[d.ref] == 'open']
    answered = [d for d in DECISIONS if states[d.ref] == 'answered']
    decided = [d for d in DECISIONS if states[d.ref] == 'decided']
    unknown = [d for d in DECISIONS if states[d.ref] == 'unknown']

    out: list[str] = []
    out.append('# Decisions')
    out.append('')
    out.append('**GENERATED by tools/generate_decisions.py — do not edit.**')
    out.append('Regenerate with `make docs`; `tests/test_decisions.py` fails if the committed')
    out.append('file and the generator disagree.')
    out.append('')
    out.append('B6. Questions this review reached and deliberately did not answer, because the')
    out.append('answer is a product call rather than an engineering one.')
    out.append('')
    out.append('## Why this is generated')
    out.append('')
    out.append('The roadmap said there were **nine**, "listed at the end of" the review log.')
    out.append('Auditing that found three things wrong at once: there were five, they were')
    out.append('recorded in two different notations, two of them sat mid-document rather than at')
    out.append('the end — and **one had already been answered.** DEC-10 asked for real token')
    out.append('revocation; B5 built it; the document went on asking for a pass.')
    out.append('')
    out.append('That last one is why this file is generated rather than written. **Each decision')
    out.append('declares what would resolve it, as a check against the code**, and the state')
    out.append('below is derived. A decision cannot go on asking a question the code has settled,')
    out.append('and cannot be marked settled before it is.')
    out.append('')
    out.append('It is the only part of this a machine can own. What each option costs, and which')
    out.append('to take, is written out and left to a person — with a recommendation, because')
    out.append('three options and no opinion is a way of not helping.')
    out.append('')
    out.append('## The state `decided`, and why it had to be added')
    out.append('')
    out.append('The first version of this file derived two states from the check: it passes, or')
    out.append('it does not. Then these questions started getting answered, and answering one is')
    out.append('not the same as building it — a decision can live in a sibling checkout, or be a')
    out.append('choice to change nothing, or deliberately leave code in the tree. Each of those')
    out.append('rendered as `open`.')
    out.append('')
    out.append('Which is the same failure the file was written to fix, one level up. `open` sends')
    out.append('a reader to ask for a decision; a reader who acts on it goes and asks for one')
    out.append('that has already been made. So there is a fourth state, and it outranks')
    out.append('`unknown` on purpose: a person choosing is a fact about the world, not about this')
    out.append('repository, and a check that cannot reach a sibling checkout does not make the')
    out.append('choice unknown.')
    out.append('')
    out.append('A `decided` entry carries a **Decided** paragraph saying what was chosen. The')
    out.append('recommendation above it is left as it was written, including where it declined')
    out.append('to recommend — what engineering thought before the call is worth more than a')
    out.append('register that reads as though it had always agreed.')
    out.append('')
    out.append('| State | Count | Meaning |')
    out.append('|---|---|---|')
    out.append(f'| `open` | {len(open_ones)} | Nobody has decided |')
    out.append(f'| `decided` | {len(decided)} | A person chose; the code does not show it yet |')
    out.append(f'| `answered` | {len(answered)} | The check says the code has settled it |')
    out.append(f'| `unknown` | {len(unknown)} | The check could not look, or cannot tell |')
    out.append('')

    out.append('| | Question | State | Touches |')
    out.append('|---|---|---|---|')
    for decision in DECISIONS:
        rows = ', '.join(f'`{r}`' for r in decision.rows) or '—'
        out.append(f'| {decision.ref} | {decision.question} | `{states[decision.ref]}` | {rows} |')
    out.append('')

    for decision in DECISIONS:
        state = states[decision.ref]
        out.append(f'## {decision.ref} — {decision.question}')
        out.append('')
        out.append(f'**`{state}`.** Raised in {", ".join(decision.origin)}.')
        out.append('')
        out.append(f'**What waiting costs.** {decision.cost_of_waiting}')
        out.append('')
        out.append('**Options.**')
        out.append('')
        for name, cost in decision.options:
            out.append(f'* **{name}** — {cost}')
        out.append('')
        out.append(f'**Recommendation.** {decision.recommendation}')
        if decision.decided:
            out.append('')
            out.append(f'**Decided.** {decision.decided}')
        if decision.unverifiable:
            out.append('')
            out.append(f'**On checking this one.** {decision.unverifiable}')
        if decision.notes:
            out.append('')
            out.append(decision.notes)
        out.append('')

    out.append('## What is not here')
    out.append('')
    out.append("**Decisions that are the customer's.** Where inference runs, whether the audit")
    out.append('trail amounts to employee monitoring, what the retention windows should be: the')
    out.append('DPIA and the Article 30 record ask those of a controller, and they are not the')
    out.append("vendor's to make.")
    out.append('')
    out.append('**Signatures.** Every `Process` row in the register needs a person, and section C')
    out.append('of the road drafted seven documents to the point of one. That is a different')
    out.append('kind of waiting from a decision: the content exists and nobody has adopted it.')
    out.append('')
    out.append('**Anything an engineer can settle.** If a question has a right answer that')
    out.append('evidence establishes, it belongs in the review as a finding rather than here as a')
    out.append('choice. Five of the six entries above were re-checked while writing this file and')
    out.append('one of them turned out to have been settled by code — which is the failure mode a')
    out.append('list of open questions has, and the reason this one is checked.')
    out.append('')
    return '\n'.join(out)


def check_siblings() -> int:
    """Report what the sibling-backed resolvers say right now.

    Deliberately not part of the document. These answers depend on
    whether the sibling checkout is present and which branch it is on,
    so they are something you ask on purpose, not something a committed
    file records. Exit code is 0 whatever they say — this reports, it
    does not gate.
    """
    sibling_backed = [d for d in DECISIONS if d.evidence_in]
    if not sibling_backed:
        print('no decisions carry evidence in a sibling checkout')
        return 0
    for decision in sibling_backed:
        root = _ROOT.parent / decision.evidence_in
        if not root.is_dir():
            print(f'{decision.ref}: {decision.evidence_in} is not checked out beside this repo — cannot say')
            continue
        try:
            built = RESOLVERS[decision.resolver]()
        except Exception as exc:  # noqa: BLE001 — reporting, not gating
            print(f'{decision.ref}: resolver raised: {exc}')
            continue
        verdict = 'the code has done it' if built else 'the code has not done it yet'
        print(f'{decision.ref}: {verdict} ({decision.evidence_in})')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='exit non-zero if the file is stale')
    parser.add_argument(
        '--check-siblings',
        action='store_true',
        help='report what the cross-repo resolvers say (never changes the document)',
    )
    args = parser.parse_args()

    if args.check_siblings:
        if str(_ROOT) not in sys.path:
            sys.path.insert(0, str(_ROOT))
        return check_siblings()

    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

    for label, offenders in (
        ('cite a finding or section that does not exist', unknown_origins()),
        ('cite a register row that does not exist', unknown_rows()),
        ('offer fewer than two options', decisions_without_options()),
        ('do not say what waiting costs', decisions_without_a_cost()),
    ):
        if offenders:
            print(f'these decisions {label}: {offenders}', file=sys.stderr)
            return 2

    missing = sorted(d.ref for d in DECISIONS if d.resolver not in RESOLVERS)
    if missing:
        print(f'these decisions name a resolver that does not exist: {missing}', file=sys.stderr)
        return 2

    rendered = render()
    if args.check:
        if not _OUTPUT.exists() or _OUTPUT.read_text() != rendered:
            print(f'{_OUTPUT} is stale; regenerate it', file=sys.stderr)
            return 1
        print(f'{_OUTPUT} is current')
        return 0

    _OUTPUT.write_text(rendered)
    print(f'wrote {_OUTPUT} ({len(rendered.splitlines())} lines, {len(DECISIONS)} decisions)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
