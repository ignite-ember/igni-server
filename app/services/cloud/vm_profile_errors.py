"""Why a VM profile cannot be saved, deleted, assigned or found for a producer.

`VmProfileRefusals` decides what HTTP makes of each.
"""

from __future__ import annotations


class VmProfileRefused(Exception):
    """The profile operation cannot go ahead; the message is the caller's to read."""


class ProfileNotFound(VmProfileRefused):
    def __init__(self) -> None:
        super().__init__('VM profile not found')


class ProfileBeyondCaps(VmProfileRefused):
    """The profile is more than the platform's caps allow; the message says which."""


class ProfileNameTaken(VmProfileRefused):
    def __init__(self, name: str) -> None:
        super().__init__(f"A VM profile named '{name}' already exists")


class DefaultProfileKept(VmProfileRefused):
    """The organisation's default profile is never deleted."""

    def __init__(self) -> None:
        super().__init__('The default profile cannot be deleted: every group and app without one gets it')


class GroupNotFound(VmProfileRefused):
    def __init__(self) -> None:
        super().__init__('Group not found')


class AppNotFound(VmProfileRefused):
    def __init__(self) -> None:
        super().__init__('App not found')


class ProducerNotInOrg(VmProfileRefused):
    """The producer a profile is looked up for is not in the organisation; the message says which kind."""


class SpaceNotInOrg(ProducerNotInOrg):
    def __init__(self) -> None:
        super().__init__('No such space in this organisation')


class AppNotInOrg(ProducerNotInOrg):
    def __init__(self) -> None:
        super().__init__('No such app in this organisation')


class MemberNotInOrg(ProducerNotInOrg):
    def __init__(self) -> None:
        super().__init__('No such active member in this organisation')
