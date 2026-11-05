"""Steps 4–6: the audit trail, SCIM from the directory, and an app's VM
profile request approved in the portal."""

import base64
import hashlib
import json
import re
import time
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

from tools.e2e_journey.harness import Journey, Outcome, Step, StepFailed


def audit_trail(j: Journey) -> Outcome | str:
    events = j.audit()
    actions = sorted({f"{e.get('method')} {e.get('route')}" for e in events})
    wanted = [
        r'POST /v1/portal/team/invite',
        r'POST /v1/portal/groups$',
        r'POST /v1/portal/groups/\{group_id\}/members',
    ]
    missing = [w for w in wanted if not any(re.search(w, x or '') for x in actions)]
    elsewhere = [r'POST /v1/portal/organizations$', r'POST /v1/cli/token']
    not_in_org = [w for w in elsewhere if not any(re.search(w, x) for x in actions)]
    j.state.audit_count = len(events)
    export = j.admin('GET', '/v1/portal/audit-events/export')
    if missing:
        return Outcome(status='FAIL', detail=f'{len(events)} rows; no action matching {missing}; actions: {actions}')
    return (
        f'{len(events)} rows; export {len(export.content)} bytes; '
        f"in the deployment's trail, not the org's: {not_in_org}; actions: {actions}"
    )


def scim(j: Journey) -> Outcome | str:
    headers = {'Authorization': f'Bearer {j.o.scim_token}', 'Content-Type': 'application/scim+json'}
    j.call('GET', '/scim/v2/ServiceProviderConfig', headers=dict(headers))
    email = f'grace@{j.o.domain}'
    body = {
        'schemas': ['urn:ietf:params:scim:schemas:core:2.0:User'],
        'userName': email,
        'externalId': f'directory-{j.o.domain}',
        'name': {'givenName': 'Grace', 'familyName': 'Hopper'},
        'emails': [{'value': email, 'primary': True}],
        'active': True,
    }
    uid = j.call('POST', '/scim/v2/Users', headers=dict(headers), json=body).json()['id']
    listed = j.call('GET', '/scim/v2/Users', headers=dict(headers), params={'filter': f'userName eq "{email}"'}).json()
    joined = next((m for m in j.team() if m['email'] == email), None)
    j.call(
        'PATCH',
        f'/scim/v2/Users/{uid}',
        headers=dict(headers),
        json={
            'schemas': ['urn:ietf:params:scim:api:messages:2.0:PatchOp'],
            'Operations': [{'op': 'replace', 'value': {'active': False}}],
        },
    )
    after = next((m for m in j.team() if m['email'] == email), None)
    refused = j.call('GET', '/scim/v2/Users', headers={'Authorization': 'Bearer wrong'}, expect=(401, 403))
    got = j.call('GET', f'/scim/v2/Users/{uid}', headers=dict(headers)).json()
    scim_rows = [e for e in j.audit() if '/scim' in (e.get('route') or '')]
    if not joined:
        return Outcome(status='FAIL', detail=f"SCIM user {uid} not in the org's team list")
    problems = []
    if got.get('active') is not False:
        problems.append(f"SCIM GET says active={got.get('active')}")
    if after and after['status'] == 'active':
        problems.append(f"deactivated, but the Team list still shows status={after['status']}")
    if not scim_rows:
        problems.append('no audit row for the SCIM create or deactivate')
    if problems:
        return Outcome(status='FAIL', detail='; '.join(problems))
    status_after = after['status'] if after else 'removed'
    return (
        f"user {uid}; totalResults={listed.get('totalResults')}; team status {joined['status']} -> {status_after}; "
        f'wrong token {refused.status_code}; {len(scim_rows)} audit rows'
    )


def _b64u(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, 'big')
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def register_app(j: Journey) -> str:
    s = j.state
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_numbers()
    jwk = {'kty': 'RSA', 'n': _b64u(public.n), 'e': _b64u(public.e), 'alg': 'RS256', 'use': 'sig'}
    canonical = json.dumps({'e': jwk['e'], 'kty': 'RSA', 'n': jwk['n']}, separators=(',', ':'), sort_keys=True)
    thumbprint = base64.urlsafe_b64encode(hashlib.sha256(canonical.encode()).digest()).rstrip(b'=').decode()
    jwk['kid'] = thumbprint
    app = j.admin(
        'POST',
        '/v1/portal/apps',
        json={'name': f'Reviewer bot {j.o.domain}', 'owner_user_id': s.ada_id, 'jwks': {'keys': [jwk]}},
    ).json()
    s.app_id = app.get('app_id') or app.get('client_id')
    s.app_key, s.app_kid = key, thumbprint
    return f"app {s.app_id} thumbprint={app.get('thumbprint')}"


def _app_token(j: Journey) -> str:
    s = j.state
    issuer = j.http.get('/.well-known/openid-configuration').json()['issuer']
    now = int(time.time())
    assertion = jwt.encode(
        {'iss': s.app_id, 'sub': s.app_id, 'aud': issuer, 'jti': uuid.uuid4().hex, 'iat': now, 'exp': now + 120},
        s.app_key,
        algorithm='RS256',
        headers={'kid': s.app_kid},
    )
    r = j.call(
        'POST',
        '/v1/oauth/token',
        data={
            'grant_type': 'client_credentials',
            'client_id': s.app_id,
            'client_assertion_type': 'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
            'client_assertion': assertion,
        },
    )
    return r.json()['access_token']


PROFILE = {
    'memory_mib': 2048,
    'normal_memory_mib': 1024,
    'vcpus': 2,
    'packages': {'apt': ['ripgrep']},
    'setup_script': 'echo e2e',
}


def request_profile(j: Journey) -> str:
    s = j.state
    s.app_tok = _app_token(j)
    path = f'/v1/apps/{s.app_id}/vm-profile-request'
    req = j.call('PUT', path, token=s.app_tok, json=PROFILE, expect=(201,)).json()
    s.vmreq_id = req['id']
    again = j.call('PUT', path, token=s.app_tok, json=PROFILE, expect=(200,))
    other = j.call('PUT', path, token=s.ada, json=PROFILE, expect=(403,))
    return (
        f"request {req['id']} state={req['state']} version={req['version'][:12]}; "
        f"same again -> {again.status_code}; admin's session -> {other.status_code}"
    )


def approve_in_portal(j: Journey) -> Outcome | str:
    s = j.state
    listing = f'/v1/portal/orgs/{s.org}/vm-profile-requests'
    mine = next((x for x in j.admin('GET', listing).json()['requests'] if x['id'] == s.vmreq_id), None)
    if not mine or mine['state'] != 'pending':
        raise StepFailed(f'not pending in the portal list: {mine}')
    j.call(
        'POST',
        f"{listing}/{mine['id']}/approve",
        token=s.max,
        org=s.org,
        json={'version': mine['version']},
        expect=(403,),
    )
    answer = j.browser(
        'approve-vm-profile',
        portal=j.o.portal,
        token=s.ada,
        org=s.org,
        app=f'Reviewer bot {j.o.domain}',
        shot=j.shot('portal-vm-profiles'),
    )
    if not answer.get('ok'):
        return Outcome(status='FAIL', detail=f'portal UI approve failed: {json.dumps(answer)[:500]}')
    state = next(x for x in j.admin('GET', listing).json()['requests'] if x['id'] == mine['id'])['state']
    profile = j.call('GET', f'/v1/apps/{s.app_id}/vm-profile', token=s.app_tok).json()
    rows = [e for e in j.audit() if e.get('target_type') == 'vm_profile_request']
    if profile.get('memory_mib') != 2048 or 'ripgrep' not in ((profile.get('packages') or {}).get('apt') or []):
        return Outcome(status='FAIL', detail=f"approved, but the app's effective profile is {profile}")
    return (
        f"approved in the portal UI -> state={state}; profile '{profile.get('profile_name')}' "
        f"mem={profile['memory_mib']}; member's approve -> 403; audit rows on vm_profile_request: {len(rows)}"
    )


STEPS = [
    Step(name='4 the audit trail has rows for what just happened', area='audit', run=audit_trail),
    Step(name="5 SCIM: provision, list, deactivate a user on the org's domain", area='scim', run=scim),
    Step(name='6a register an app (portal Apps) with its own public key', area='vm-profile', run=register_app),
    Step(
        name='6b the app gets a token (client_credentials + private_key_jwt) and asks for a VM profile',
        area='vm-profile',
        run=request_profile,
    ),
    Step(
        name='6c portal (Chromium): the org admin sees the pending request and approves it',
        area='vm-profile',
        run=approve_in_portal,
    ),
]
