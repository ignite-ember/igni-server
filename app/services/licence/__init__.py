"""igni's offline licence: the file, its signature, and the lapse sequence.

Specification: igni server's `docs/licence-file.md`. The modules in this
package are copied byte for byte into each paid module; keep them free
of imports from outside the package.
"""

from .format import Bands, Entitlement, LicenceEnvelope, LicenceKind, LicencePayload, Module
from .keys import RELEASE_KEYS, TEST_KEY_ID, TrustedKeys
from .signer import LicenceSigner
from .source import Clock, DEFAULT_LICENCE_PATH, LicenceFile, SystemClock
from .status import GRACE, LicenceReading, LicenceState, LicenceStatus, ModuleStatus, Operation, Reason
from .verifier import (
    Base64Url,
    LicenceError,
    LicenceMalformed,
    LicenceSignatureInvalid,
    LicenceUnknownKey,
    LicenceVerifier,
)

__all__ = [
    'Bands',
    'Base64Url',
    'Clock',
    'DEFAULT_LICENCE_PATH',
    'Entitlement',
    'GRACE',
    'LicenceEnvelope',
    'LicenceError',
    'LicenceFile',
    'LicenceKind',
    'LicenceMalformed',
    'LicencePayload',
    'LicenceReading',
    'LicenceSignatureInvalid',
    'LicenceSigner',
    'LicenceState',
    'LicenceStatus',
    'LicenceUnknownKey',
    'LicenceVerifier',
    'Module',
    'ModuleStatus',
    'Operation',
    'RELEASE_KEYS',
    'Reason',
    'SystemClock',
    'TEST_KEY_ID',
    'TrustedKeys',
]
