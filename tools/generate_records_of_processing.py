#!/usr/bin/env python3
"""Generate `docs/RECORDS_OF_PROCESSING.md` from the code.

B4, closing DP-9's technical half. GDPR Article 30(1) asks a controller
to keep a record of processing activities. The customer is the
controller — the software runs in their cloud and we never see their
data — but they cannot write that record without knowing what the
software stores, why, and for how long. Article 30 is one of the few
places where a self-hosted vendor's obligation is *documentation*.

**This is generated, not written.** F139 is the reason: `COMPLIANCE.md`
was maintained by hand and spent five passes claiming five controls did
not exist, while the register beside it — which has a test — stayed
correct. An Article 30 record listing tables and retention windows is
exactly the kind of document that goes stale on the next migration, so
the categories of personal data come from `PERSONAL_DATA_COLUMNS` (the
classification F115 built and asserts against the live schema), the
retention windows come from `app/config.py`, and
`tests/test_records_of_processing.py` fails if the committed file stops
matching this script's output.

**What is still the controller's to fill in.** Article 30(1)(a) asks
for the controller's and DPO's identity and contact details, and
30(1)(b) asks for the *legal basis* under which they process. Neither
is ours to state: we do not know who deploys this or why. The document
carries marked placeholders for both, which is the honest form — a
vendor-supplied record that invented a legal basis would be worse than
none.

    python tools/generate_records_of_processing.py          # write
    python tools/generate_records_of_processing.py --check  # compare
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import pathlib
import re
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.services.subject_access import (  # noqa: E402
    NOT_PERSONAL_COLUMNS,
    PERSONAL_DATA_COLUMNS,
)

OUT = _ROOT / 'docs/RECORDS_OF_PROCESSING.md'

PLACEHOLDER = '`__CONTROLLER_TO_COMPLETE__`'
"""Marked, not guessed.

An Article 30 record with a plausible-looking legal basis already
filled in is a liability: it reads as advice, and the one thing a
vendor cannot know is why *this* customer processes *this* data.
"""


class Activity:
    """One processing activity, in Article 30's sense.

    Grouped by *why the data is there* rather than by table, because
    that is the question the article asks and because several tables
    serve one purpose. The tables listed decide which categories of
    personal data appear, and the classification decides which columns.
    """

    def __init__(
        self,
        key: str,
        name: str,
        purpose: str,
        subjects: str,
        tables: tuple[str, ...],
        retention: str,
        json_payload: bool = False,
    ) -> None:
        self.key = key
        self.name = name
        self.purpose = purpose
        self.subjects = subjects
        self.tables = tables
        self.retention = retention
        # `json_payload` says the personal data is inside a JSON column
        # rather than in named ones, so `PERSONAL_DATA_COLUMNS` — which
        # classifies *columns* — has nothing to say about it and the
        # table is absent from there by design, not by omission. I
        # nearly filed that absence as a finding; subject access covers
        # this table through a purpose-built section that returns only
        # the subject's own commits, because a push payload names the
        # other authors too.
        self.json_payload = json_payload


ACTIVITIES: tuple[Activity, ...] = (
    Activity(
        key='accounts',
        name='Accounts and authentication',
        purpose=(
            'Identify a person across sessions so the deployment can decide what '
            'they may see and do, and so an administrator can offboard them. '
            'Without an account record there is no access control and no way to '
            'answer a subject-access or erasure request about anybody.'
        ),
        subjects='Developers and administrators given access to the deployment.',
        tables=('users', 'user_roles', 'org_memberships', 'org_invitations'),
        retention=(
            'For the life of the account. Deleting it removes the row and '
            'degrades provenance elsewhere to NULL rather than cascading '
            '(F60, F85), and every session it issued stops working (F122).'
        ),
    ),
    Activity(
        key='organisations',
        name='Organisations and groups',
        purpose=(
            'Scope every request to one organisation and decide which model '
            'and tools a member may reach. Group membership is what makes a '
            'configuration apply to a person rather than to a whole '
            'deployment.'
        ),
        subjects='Members of an organisation in the deployment.',
        tables=(
            'organizations',
            'org_groups',
            'org_group_members',
            'org_group_entries',
            'org_group_extensions',
        ),
        retention='For the life of the organisation or the membership.',
    ),
    Activity(
        key='notifications',
        name='Telling somebody that something concerns them',
        purpose=(
            'Tell people about something that happened in an extension they '
            'use — a comment on their document, a request waiting on them — '
            "when the extension's service reports it. The service says what "
            'happened and who it concerns, by user id or address; this server '
            'resolves those to active members of the organisation the '
            "service's token is for, drops everybody else and the person who "
            'did it, writes one row per person once per event, and records '
            'when it reached their machine and when they read it. The source '
            'is recorded by this server from the extension the service '
            'uploaded, never taken from the request, so one service cannot '
            'speak as another.'
        ),
        subjects=(
            'People who are told — active members of the organisation a '
            'service names — and the people whose action is being reported.'
        ),
        tables=('notifications',),
        retention=(
            'For the life of the organisation. A notification that has been '
            'read is marked rather than deleted: deleting it would make '
            '"what have I already seen" unanswerable the first time two '
            'machines are open, and there is nothing in the row worth '
            'reclaiming.'
        ),
    ),
    Activity(
        key='usage',
        name='Usage accounting',
        purpose=(
            'Attribute token and request counts to a person so an operator can '
            'see what the deployment is spending and on whose behalf. This is '
            'the data behind the dashboard and the per-member figures on the '
            'team page.'
        ),
        subjects='Developers whose requests the deployment served.',
        tables=('chat_usage',),
        retention=('No automatic window. Rows persist for the life of the deployment unless the operator prunes them.'),
    ),
    Activity(
        key='audit',
        name='Security audit trail',
        purpose=(
            'Record who did what, to what, from where, and whether it '
            'succeeded — for every mutation on an authenticated surface and '
            'every authentication attempt including the failures. This is what '
            'makes an incident scopable and a breach notification possible, '
            'and it is required by SOC 2 CC7.2 and ISO A 8.15.'
        ),
        subjects=('Anybody who acts on the deployment, including a failed sign-in by somebody who is not a member.'),
        tables=('audit_events',),
        retention=(
            '`AUDIT_RETENTION_DAYS`, pruned by `app/services/retention.py`. '
            'Defaults to 0 — keep everything — because silently discarding an '
            'audit history would be the wrong default; setting a window is the '
            "operator's policy decision."
        ),
    ),
    Activity(
        key='inference',
        name='Inference routing',
        purpose=(
            'Route a completion to a GPU node the operator registered, and '
            'record who registered it. The node addresses and credentials stay '
            'server-side; a client never learns them.'
        ),
        subjects='The operator or administrator who added a node.',
        tables=('inference_nodes',),
        retention='Until the node is removed from the deployment.',
    ),
    Activity(
        key='cloud',
        name="The cloud's producers and their VMs",
        purpose=(
            'Run each producer — a person, an app an org admin registered, or '
            "a space the app declared for one of its own users — in a microVM "
            'of its own: which apps an organisation has and who '
            'owns them, which extensions each app has on, which VM profile '
            'each group and app gets and who defined it, the commands run as '
            "root in a producer's VM with who asked and what came of them, the "
            "messages, chats, actions and events services asked to deliver to a "
            "producer's agent or extension, with the thread key and the files "
            'that came with them, and which MCP '
            "servers a person approved, and the credential each producer's VM "
            'holds of its own — hashed, rotated on every use — so the platform '
            'never mints a token for anybody. Without these the platform cannot '
            'say who may run what in whose VM, or answer for it afterwards.'
        ),
        subjects=(
            'Members of an organisation who own or registered an app, defined '
            'a VM profile, asked for or were the target of a command, were '
            'sent an event, or approved an MCP server; the super admin who set '
            "the platform's caps or an organisation's quota; and an app's own "
            'users, named only by the id the app gives each of their spaces.'
        ),
        tables=(
            'org_apps',
            'org_app_extensions',
            'vm_profiles',
            'vm_commands',
            'vm_jobs',
            'producer_events',
            'service_attachments',
            'producer_feed',
            'producer_feed_marks',
            'producer_volumes',
            'legal_holds',
            'revocations',
            'producer_exports',
            'platform_retention',
            'org_retention',
            'vm_credentials',
            'person_mcp_approvals',
            'platform_vm_caps',
            'org_vm_quotas',
            'app_spaces',
            'platform_space_caps',
            'org_space_quotas',
        ),
        retention=(
            'For the life of the organisation. Commands are the audit log of '
            'what ran in whose VM and are kept with it. An event waits at most '
            '`SERVICE_EVENT_MAX_WAIT_SECONDS` (14 days) to be delivered and is '
            'deleted `SERVICE_EVENT_RETENTION_DAYS` (30) after it was delivered, '
            'expired or dropped, by `app/services/retention.py`. A file a service '
            'sends with an item is deleted a day after upload if no item names it, '
            'and otherwise when its item expires. A VM credential '
            'is kept, revoked, for the life of the organisation as the record of '
            'who could reach a VM; it stops working after 90 days unused. The '
            "gateway's feed keeps one row per producer — whether they are one "
            'still, and when that changed. A member removed from an organisation '
            "has their VM's state, volume and keys erased after the organisation's "
            '`removed_member_retention_days` (default 30, at most the platform\'s '
            'maximum; 0 is at once), or at once on an erasure request; a legal hold '
            'an admin places stops that clock until it is released, and an erasure '
            'request is refused while it stands. Every erasure scheduled, moved or '
            'stopped is audited. An export of a removed member\'s data an admin '
            'asks for during retention is kept `PRODUCER_EXPORT_RETENTION_DAYS` (7) '
            'for the organisation to download. A feed row is kept `PRODUCER_FEED_RETENTION_DAYS` '
            '(30) after the erasure, by `app/services/retention.py`. A member who '
            'was only deactivated keeps their row while their suspended VM exists. '
            'A revocation telling the gateway and services to stop accepting '
            "a person's tokens is kept 31 days, past the longest a token lives. "
            'A mark naming a changed producer is '
            "deleted at the gateway's next poll."
        ),
    ),
    Activity(
        key='services',
        name='Registered services and apps',
        purpose=(
            'Decide which services and apps may obtain tokens from this '
            'deployment, and record which super admin approved or revoked '
            'each one. A registration describes software — its name, where '
            'it is reached, its public key — and the one column naming a '
            'person is who made the decision. The extension a service ships '
            'is held beside its registration and holds no personal data; '
            'see *Tables that hold no personal data* below.'
        ),
        subjects=(
            'The super admin who approved or revoked a registration, or set '
            'the priority order of extensions that decides which of two '
            'colliding extensions has a contested part of igni.'
        ),
        tables=('registered_clients', 'extension_priority'),
        retention=(
            'Until the registration is removed. Deleting the account that '
            'decided sets the column to NULL rather than removing the '
            'registration, and leaves the priority order standing, set by '
            'nobody in particular. The address shown with either is read '
            'from the account, never copied.'
        ),
    ),
)


#: Shared-plane tables that hold no personal data, and why.
#:
#: The coverage rule above runs from the column classification: a table
#: appears in an activity only if `app/services/subject_access.py`
#: classifies it, and it is classified only if a column could name a
#: person. These have no such column, so no activity can claim them — and
#: a record that left them out without saying so would read, to somebody
#: comparing it with the schema, as a record that had missed them.
NO_PERSONAL_DATA: tuple[tuple[str, str], ...] = (
    (
        'service_extensions',
        "The extension each registered service ships to people's machines: "
        'the bundle archive itself (up to 400 MiB of wheels), its manifest, '
        "a summary of its plugin, its digest and the service's signature "
        'over it. Software, uploaded by a service and distributed to every '
        'signed-in machine once a super admin approves the service. Removed '
        'with the registration it belongs to.',
    ),
    (
        'extension_assets',
        "The files a registered service's extension runs that are not Python — "
        'a database distribution, a Java runtime, a model — one row per digest: '
        "the bytes (up to 1 GiB each), their size and the service's signature "
        'over them. Software, uploaded by a service beside its extension and '
        'distributed with it. Removed when no version the service holds '
        'declares it, or with the registration it belongs to.',
    ),
    (
        'org_domains',
        'The email domains each organisation claims — `acme.com`, not '
        "anybody's address — which decide the organisation a new account "
        'joins.',
    ),
    (
        'organisation_deletion_notices',
        'Which registered services have yet to confirm that an organisation '
        "was deleted: the deleted organisation's id, the service, how many "
        'calls went unconfirmed, when to call again and what the last call '
        'got back. No person is named. A row goes when its service confirms, '
        "or with the service's registration.",
    ),
    (
        'revoked_grants',
        'Sign-ins a registered service has ended (RFC 7009 revocation): a '
        'random sign-in id, the client it was at, and when it was revoked '
        'and stops mattering. No person is named — the id is only in the '
        'refresh tokens the service held. Pruned once no refresh token of '
        'the sign-in can still be valid, 30 days at most.',
    ),
    (
        'signing_keys',
        "The deployment's own token-signing keys: a key id, the public half, "
        'and the private half encrypted under `SECRET_ENCRYPTION_KEY`. '
        'Superseded keys are pruned once no token they signed can still be '
        'alive.',
    ),
    (
        'tenants',
        "Which database holds each organisation's rows: an organisation id, "
        'a number, a database name and a state. The organisation itself is '
        'recorded under *Organisations and groups*.',
    ),
)


def _tables_in_classification() -> dict[str, list[tuple[str, str]]]:
    """Every classified table, with its columns and dispositions."""
    tables: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for key, disposition in PERSONAL_DATA_COLUMNS.items():
        table, column = key.split('.', 1)
        tables[table].append((column, disposition))
    for key in NOT_PERSONAL_COLUMNS:
        table, column = key.split('.', 1)
        tables[table].append((column, 'not personal'))
    for columns in tables.values():
        columns.sort()
    return dict(tables)


def unassigned_tables() -> list[str]:
    """Classified tables no activity claims.

    The test turns this into a failure: a migration that adds a table
    holding personal data has to say which activity it serves before the
    record can be regenerated, which is the same shape as F115's rule
    that a new column must be classified before the schema check passes.
    """
    claimed = {table for activity in ACTIVITIES for table in activity.tables}
    return sorted(set(_tables_in_classification()) - claimed)


def unknown_tables() -> list[str]:
    """Tables an activity names that the classification does not have.

    The other direction, and the one that rots quietly: a table renamed
    or dropped leaves an activity describing something that is not there.
    """
    known = set(_tables_in_classification())
    named = {table for activity in ACTIVITIES if not activity.json_payload for table in activity.tables}
    return sorted(named - known)


#: Where every table is created: the shared plane's migrations and each
#: organisation's.
_MIGRATIONS = (_ROOT / 'alembic/versions', _ROOT / 'alembic_tenant/versions')
#: What an upgrade does to a table, in the order it does it: created,
#: dropped, or renamed from one name to another.
_TABLE_CHANGE = re.compile(
    r"op\.(create_table|drop_table)\(\s*'([a-z_]+)'|op\.rename_table\(\s*'([a-z_]+)'\s*,\s*'([a-z_]+)'"
)


def _upgrade_of(migration: str) -> str:
    """The `upgrade()` of a migration: what running it forward does, not undoing it."""
    _, _, after = migration.partition('def upgrade(')
    return after.partition('def downgrade(')[0]


def _migrated_tables() -> set[str]:
    """The tables there are after every migration, applied in order."""
    tables: set[str] = set()
    for directory in _MIGRATIONS:
        for migration in sorted(directory.glob('*.py')):
            for change, name, old_name, new_name in _TABLE_CHANGE.findall(_upgrade_of(migration.read_text())):
                if change == 'create_table':
                    tables.add(name)
                elif change == 'drop_table':
                    tables.discard(name)
                else:
                    tables.discard(old_name)
                    tables.add(new_name)
    return tables


def unaccounted_tables() -> list[str]:
    """Tables a migration creates that this record says nothing about.

    The classification only reaches tables with a column that could name
    a person, so a table without one — `service_extensions` was the
    first to arrive after the rule — would otherwise be absent with
    nothing to say whether anybody had looked. It has to be classified,
    or listed in `NO_PERSONAL_DATA` with what it holds.
    """
    accounted = set(_tables_in_classification()) | {table for table, _ in NO_PERSONAL_DATA}
    return sorted(_migrated_tables() - accounted)


def misdescribed_tables() -> list[str]:
    """Tables listed as holding no personal data that are either gone
    or classified as holding some — either way, the sentence about them
    is wrong."""
    listed = {table for table, _ in NO_PERSONAL_DATA}
    return sorted((listed - _migrated_tables()) | (listed & set(_tables_in_classification())))


def render() -> str:
    tables = _tables_in_classification()
    personal_total = len(PERSONAL_DATA_COLUMNS)

    out: list[str] = []
    w = out.append

    w('<!--')
    w('  GENERATED by tools/generate_records_of_processing.py — do not edit.')
    w('')
    w('  The categories below come from the column classification in')
    w('  app/services/subject_access.py, which is itself asserted against')
    w('  the live schema (F115). Edit the activities in the generator, or')
    w('  the classification, and regenerate:')
    w('')
    w('      python tools/generate_records_of_processing.py')
    w('')
    w('  tests/test_records_of_processing.py fails if this file and the')
    w('  generator disagree. F139 is why: the hand-maintained compliance')
    w('  document spent five passes describing a product that had moved.')
    w('-->')
    w('')
    w('# Record of processing activities')
    w('')
    w('**GDPR Article 30(1).** This is the vendor-supplied half of a record the *customer* keeps.')
    w('')
    w(
        "igni runs in the customer's own infrastructure and no data reaches "
        'the vendor, so the customer is the controller and there is no '
        'processor relationship to describe. What a controller cannot write '
        'without the vendor is the inventory: what the software stores, why, '
        'and for how long. That is what follows.'
    )
    w('')
    w(
        f'It covers **{personal_total} classified personal-data columns across '
        f'{len(tables)} tables**, plus one table whose personal data lives in '
        f'a JSON payload, grouped into {len(ACTIVITIES)} processing '
        'activities.'
    )
    w('')
    w('## Documents are processed by a paired service')
    w('')
    w(
        'Shared documents, their versions, the comments on them, their drafts '
        'and the requests to be let into them are **not** in this record. That '
        'processing moved to a separate deployment — the artifacts service — '
        'which holds those tables in its own database.'
    )
    w('')
    w(
        'Said here rather than left as an absence, because a record of '
        'processing that quietly omits a category reads as a record that the '
        'category does not exist. A deployment running both services needs '
        'both records; one running only this service does not process that '
        'data at all.'
    )
    w('')
    w(
        'What remains here about documents is the identity this server answers '
        'for the artifacts service — who somebody is and whether they are '
        "still a member, read from this server's directory with a token "
        'scoped to one organisation — and the notifications a document can '
        "cause, which are stored in this database because a person's "
        'notifications span more than documents. The artifacts service is a '
        'registered service like any other: it shares no secret with this '
        'server, and it is trusted only once a super admin approves it. An '
        'erasure or subject-access request made here is passed on to it and '
        'to every other approved service.'
    )
    w('')
    w('## What the controller must add')
    w('')
    w('Two things here are deliberately blank, because a vendor filling them in would be inventing them:')
    w('')
    w('| Article 30(1) | Field | Why it is not here |')
    w('|---|---|---|')
    w(f'| (a) | Controller name and contact details | {PLACEHOLDER} — whoever runs the deployment |')
    w(f'| (a) | Data protection officer, if one is appointed | {PLACEHOLDER} — an organisational question |')
    w(
        f'| (b) | Legal basis for each activity below | {PLACEHOLDER} — '
        'legitimate interests, contract and consent are all plausible and only '
        'the controller knows which applies |'
    )
    w('')
    w('## Recipients and transfers')
    w('')
    w(
        '**Article 30(1)(d) recipients: none by default.** Nothing in the server '
        'sends personal data to a third party. Rules in each repository defend '
        'this rather than a promise: every outbound host named in code is on '
        'an allow-list with a reason, no vendor telemetry package may appear in a '
        "lock file (the OTLP exporters only, which send to the customer's own "
        'collector and have no default endpoint), and the check fails on its own staleness '
        '(`tests/test_nothing_phones_home.py`); a runtime test fails on any '
        'connection outside the network '
        '(`tests/test_igni_reaches_nothing_outside_the_network.py`). The scan '
        'has caught two real outbound requests since it was written (F94, '
        "F126). The recipients a deployment can acquire are the customer's "
        'choice: the model endpoint, a mail relay, an OpenTelemetry collector, and the web pages and search '
        "engine the client's agent may reach unless `air_gapped: true` is set "
        '(`docs/SUPPLIERS.md`, `docs/NETWORK_DIAGRAM.md`).'
    )
    w('')
    w(
        'Services a super admin has approved — the artifacts service, '
        "CodeIndex — read members' names and addresses from this server's "
        'directory, one organisation at a time. They are deployments the '
        'customer runs, not third parties, so they belong in the '
        "controller's own record as further processing rather than as "
        'recipients; each keeps its own.'
    )
    w('')
    w(
        '**Article 30(1)(e) third-country transfers: none,** for the same '
        'reason. The deployment talks to the source-control host the customer '
        'configured and to GPU nodes they registered — both their own choices, '
        'and both recorded in their own record as recipients if they wish.'
    )
    w('')
    w('## Article 30(1)(g) security measures')
    w('')
    w(
        'Not restated here. `COMPLIANCE_TRACKER.md` is the register, one row '
        'per control with a status and the evidence behind it, and it is '
        'asserted by a test rather than maintained by hand.'
    )
    w('')
    w('## The activities')
    w('')

    for activity in ACTIVITIES:
        w(f'### {activity.name}')
        w('')
        w(f'**Purpose (30(1)(b)).** {activity.purpose}')
        w('')
        w(f'**Legal basis (30(1)(b)).** {PLACEHOLDER}')
        w('')
        w(f'**Categories of data subject (30(1)(c)).** {activity.subjects}')
        w('')
        w(f'**Retention (30(1)(f)).** {activity.retention}')
        w('')
        w('**Categories of personal data (30(1)(c)).**')
        w('')
        w('| Table | Held | Returned on a subject-access request | Withheld | Not personal |')
        w('|---|---|---|---|---|')
        if activity.json_payload:
            w(
                'The personal data here is inside a JSON payload rather than in '
                'named columns, so the column classification does not describe '
                "it. What it contains is the provider's push payload: a commit "
                'message, an author name and an author email per commit. A '
                'subject-access request reports the commits attributable to the '
                'subject and counts the rest without describing them, because '
                'the same payload names the other authors of the same push.'
            )
            w('')
            continue
        for table in activity.tables:
            columns = tables.get(table, [])
            included = [c for c, d in columns if d == 'included']
            withheld = [c for c, d in columns if d == 'withheld']
            dead = [c for c, d in columns if d == 'dead']
            neutral = [c for c, d in columns if d == 'not personal']
            held = len(included) + len(withheld) + len(dead)
            w(
                f'| `{table}` | {held} | '
                f'{", ".join(f"`{c}`" for c in included) or "—"} | '
                f'{", ".join(f"`{c}`" for c in withheld) or "—"} | '
                f'{len(neutral)} |'
            )
        w('')

    w('## Tables that hold no personal data')
    w('')
    w(
        'The shared database holds these as well. None has a column that '
        'could name a person, so none is classified and no activity above '
        'claims it; they are listed so that this record accounts for every '
        'table rather than only the ones that hold personal data. All of '
        "them are in the shared plane's backup."
    )
    w('')
    w('| Table | What it holds |')
    w('|---|---|')
    for table, holds in NO_PERSONAL_DATA:
        w(f'| `{table}` | {holds} |')
    w('')

    w('## A note on the columns marked withheld')
    w('')
    w(
        'A withheld column holds personal data and is deliberately not '
        'returned to the subject: a password hash, an OAuth token, the '
        'free-text detail of an audit entry that may name a third party. '
        'Withholding is a decision recorded column by column in '
        '`app/services/subject_access.py`, with the reason, and the '
        'subject-access report says that a value was withheld rather than '
        'pretending the column does not exist.'
    )
    w('')
    w('## A note on what is not in this record')
    w('')
    w(
        '**Repository content.** Source code, commit messages and the '
        'index built from them are not held by this server. Repository '
        'integration belongs to CodeIndex (igni__code-index), and the record '
        'of what it processes belongs with it.'
    )
    w('')
    w(
        '**Model prompts and completions.** Not stored. A completion passes '
        'through the deployment to a GPU node the customer registered; what '
        'persists is the token count in `chat_usage`, not the text.'
    )
    w('')

    return '\n'.join(out) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        '--check',
        action='store_true',
        help='exit non-zero if the committed file differs, and print the reason',
    )
    args = parser.parse_args()

    problems = []
    if unassigned := unassigned_tables():
        problems.append(f'these classified tables belong to no activity, so the record would omit them: {unassigned}')
    if unknown := unknown_tables():
        problems.append(f'these activities name tables the classification does not have: {unknown}')
    if unaccounted := unaccounted_tables():
        problems.append(
            'these tables are created by a migration and neither classified nor listed in '
            f'NO_PERSONAL_DATA: {unaccounted}'
        )
    if misdescribed := misdescribed_tables():
        problems.append(f'NO_PERSONAL_DATA lists tables that are gone or hold classified personal data: {misdescribed}')
    if problems:
        print('\n'.join(problems), file=sys.stderr)
        return 1

    rendered = render()
    if args.check:
        current = OUT.read_text() if OUT.exists() else ''
        if current != rendered:
            print(
                f'{OUT.relative_to(_ROOT)} is out of date — regenerate with\n'
                '    python tools/generate_records_of_processing.py',
                file=sys.stderr,
            )
            return 1
        print(f'{OUT.relative_to(_ROOT)} matches the generator')
        return 0

    OUT.write_text(rendered)
    print(f'wrote {OUT.relative_to(_ROOT)} ({len(rendered.splitlines())} lines)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
