"""Bedrock's signing keys: from the server's own IAM role, a role it assumes, or stored access keys.

A Bedrock request is signed (SigV4) rather than given a bearer token, so
these sources hand out `AwsKeys`. boto3 is a declared dependency (SES and
SigV4 use it), imported at the top; its calls are synchronous network
calls — the instance metadata service, STS — so they run in a worker
thread.
"""

from __future__ import annotations

from abc import abstractmethod
import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
import time
from typing import Any

import boto3

from app.services.ai.providers.tokens import AccessToken, TokenSource

#: How long the server's own identity is reused before it is read again.
#: boto3 renews the role's credentials itself; re-reading them this often
#: picks up its renewal without relying on its private expiry.
AMBIENT_REREAD_SECONDS = 300

#: A role session, as long as STS gives by default.
ROLE_SESSION_SECONDS = 3600


@dataclass(frozen=True)
class AwsKeys:
    """What a Bedrock request is signed with. Never in a repr: these are the secret."""

    access_key_id: str
    secret_access_key: str = field(repr=False)
    session_token: str | None = field(default=None, repr=False)


class AwsKeySource(TokenSource[AwsKeys]):
    """AWS keys, obtained in a worker thread."""

    async def fetch(self) -> AccessToken[AwsKeys]:
        return await asyncio.to_thread(self._obtained)

    @abstractmethod
    def _obtained(self) -> AccessToken[AwsKeys]:
        """The keys, and when to ask again."""


class AwsDefaultSource(AwsKeySource):
    """The server's own IAM role: an instance profile, an ECS task role, IRSA — AWS's default chain."""

    def __init__(self, *, settings: Mapping[str, str], secret: str | None) -> None:
        super().__init__(settings=settings, secret=secret)
        #: The chain's credentials object, which renews itself; built on the first fetch.
        self._chain: Any = None

    def _obtained(self) -> AccessToken[AwsKeys]:
        if self._chain is None:
            self._chain = boto3.Session().get_credentials()
        if self._chain is None:
            raise RuntimeError("AWS's default chain found no credentials for the server")
        frozen = self._chain.get_frozen_credentials()
        return AccessToken(
            value=AwsKeys(
                access_key_id=frozen.access_key, secret_access_key=frozen.secret_key, session_token=frozen.token
            ),
            expires_at=time.time() + AMBIENT_REREAD_SECONDS,
        )


class AwsAssumedRoleSource(AwsKeySource):
    """The server's own identity assuming the named role, with its external ID when one is set."""

    def _obtained(self) -> AccessToken[AwsKeys]:
        request: dict[str, Any] = {
            'RoleArn': self._settings['role_arn'],
            'RoleSessionName': 'igni-server',
            'DurationSeconds': ROLE_SESSION_SECONDS,
        }
        if self._settings.get('external_id'):
            request['ExternalId'] = self._settings['external_id']
        issued = boto3.Session().client('sts').assume_role(**request)['Credentials']
        return AccessToken(
            value=AwsKeys(
                access_key_id=issued['AccessKeyId'],
                secret_access_key=issued['SecretAccessKey'],
                session_token=issued['SessionToken'],
            ),
            expires_at=issued['Expiration'].timestamp(),
        )


class AwsAccessKeysSource(AwsKeySource):
    """An IAM user's access key: its id a setting, its secret stored encrypted. They do not expire."""

    def _obtained(self) -> AccessToken[AwsKeys]:
        keys = AwsKeys(access_key_id=self._settings['access_key_id'], secret_access_key=self._secret or '')
        return AccessToken(value=keys, expires_at=float('inf'))
