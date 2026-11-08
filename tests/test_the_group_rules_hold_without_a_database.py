"""The group services' rules, with their stores substituted, so no database is needed.

The portal answers these through HTTP in `test_portal_groups.py` and its
neighbours. Here each rule is asked directly: which refusal comes first,
what an entry is stored as, which group a request may reach, and what
`GroupRefusals` turns each refusal into — status and words, which are
the portal's contract.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app.api.base_v1.schema.portal.group_entries import CreateEntryRequest
from app.db.sql.schemas.group import (
    EntryNameTaken,
    EntryNotFound,
    GroupNameTaken,
    GroupNotFound,
    TemplateGroupNotFound,
)
from app.services.portal.entry_rules import EntryRules, PluginSource
from app.services.portal.group_access import GroupAccess
from app.services.portal.group_errors import (
    GroupIsStandard,
    KindNeedsContentType,
    KindTakesNoModel,
    ModelNotServed,
    NotAnOrgMember,
    UnknownContentType,
    UnknownEntryKind,
    UnsupportedPluginSource,
)
from app.services.portal.group_refusals import GroupRefusals
from app.services.portal.served_models import ServedModels


def _entry(**overrides: object) -> CreateEntryRequest:
    fields: dict[str, object] = {
        'kind': 'agents',
        'entry_name': 'reviewer',
        'content': '# reviewer',
        'content_type': 'markdown',
    }
    return CreateEntryRequest.model_validate({**fields, **overrides})


class TestWhatAnEntryMayBe:
    def test_an_unknown_kind_is_refused_before_anything_else(self):
        with pytest.raises(UnknownEntryKind) as refused:
            EntryRules().refuse_bad_kind(_entry(kind='macros', model='m', content_type='nonsense'))

        assert str(refused.value).startswith('Invalid kind. Must be one of: agents, ')

    def test_only_an_agent_may_name_a_model(self):
        EntryRules().refuse_bad_kind(_entry(model='legal-reviewer'))

        with pytest.raises(KindTakesNoModel) as refused:
            EntryRules().refuse_bad_kind(_entry(kind='skills', model='legal-reviewer'))
        assert str(refused.value) == "Only agents can name a model; 'skills' cannot."

    def test_a_content_type_igni_never_reads_is_refused_before_a_mismatched_one(self):
        with pytest.raises(UnknownContentType):
            EntryRules().entry_from(_entry(content_type='toml'))

        with pytest.raises(KindNeedsContentType) as refused:
            EntryRules().entry_from(_entry(content_type='json'))
        assert str(refused.value) == (
            "Kind 'agents' requires content_type 'markdown' (igni's loader only parses that format)."
        )

    def test_an_empty_model_is_stored_as_none(self):
        assert EntryRules().entry_from(_entry(model='')).model is None
        assert EntryRules().entry_from(_entry(model='legal-reviewer')).model == 'legal-reviewer'


class TestWhereAPluginComesFrom:
    @pytest.mark.parametrize('url', ['https://github.com/o/r', 'http://git.corp/r', 'git://host/r', 'ssh://git@host/r'])
    def test_what_git_clone_takes_is_accepted(self, url):
        assert PluginSource.from_request(url=url, ref=None, subdir=None).url == url

    @pytest.mark.parametrize('url', ['file:///etc/passwd', 'not a url at all', 'http://[', ''])
    def test_anything_else_is_refused(self, url):
        with pytest.raises(UnsupportedPluginSource):
            PluginSource.from_request(url=url, ref=None, subdir=None)

    def test_blank_parts_are_stored_as_absent(self):
        source = PluginSource.from_request(url='  https://github.com/o/r  ', ref='  ', subdir=' plugins/x ')

        assert source == PluginSource(url='https://github.com/o/r', ref=None, subdir='plugins/x')

    def test_without_a_url_the_other_parts_are_kept_as_sent(self):
        assert PluginSource.from_request(url=None, ref='', subdir=None) == PluginSource(ref='', subdir=None)


class _Groups:
    def __init__(self, *groups: SimpleNamespace) -> None:
        self._groups = groups

    async def get_group(self, org_id: str, *, group_id: str) -> SimpleNamespace | None:
        return next((g for g in self._groups if g.id == group_id), None)

    async def get_group_by_name(self, org_id: str, *, name: str) -> SimpleNamespace | None:
        return next((g for g in self._groups if g.name == name), None)


_DEFAULT = SimpleNamespace(id='g-default', name='Default', is_default=True)
_LEGAL = SimpleNamespace(id='g-legal', name='Legal', is_default=False)


def _access() -> GroupAccess:
    return GroupAccess(groups=_Groups(_DEFAULT, _LEGAL))  # type: ignore[arg-type]


class TestWhichGroupARequestReaches:
    async def test_a_group_it_cannot_find_is_not_found(self):
        with pytest.raises(GroupNotFound):
            await _access().in_org(org_id='org-1', group_id='g-elsewhere')

    async def test_any_group_found_may_be_read_but_the_default_one_not_changed(self):
        assert await _access().in_org(org_id='org-1', group_id='g-default') is _DEFAULT
        assert await _access().writable(org_id='org-1', group_id='g-legal') is _LEGAL

        with pytest.raises(GroupIsStandard):
            await _access().writable(org_id='org-1', group_id='g-default')

    async def test_a_name_another_group_has_is_taken_but_a_group_may_keep_its_own(self):
        await _access().refuse_taken_name(org_id='org-1', name='Legal', except_id='g-legal')
        await _access().refuse_taken_name(org_id='org-1', name='Finance')

        with pytest.raises(GroupNameTaken):
            await _access().refuse_taken_name(org_id='org-1', name='Legal', except_id='g-default')


class _Pool:
    def __init__(self, served: list[str]) -> None:
        self._served = served
        self.asked = 0

    async def list_available_models(self) -> list[tuple[str, str]]:
        self.asked += 1
        return [(model, 'http://gpu-01:8000/v1') for model in self._served]


class _Pools:
    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    def get_pool(self) -> _Pool:
        return self._pool


class TestAModelNothingServes:
    async def test_a_served_model_passes_and_an_unserved_one_names_what_is(self):
        served = ServedModels(pools=_Pools(_Pool(['zeta', 'alpha'])))  # type: ignore[arg-type]

        await served.refuse_unserved('alpha')
        with pytest.raises(ModelNotServed) as refused:
            await served.refuse_unserved('legal-reviewr')
        assert str(refused.value) == "No inference node serves 'legal-reviewr'. Available: alpha, zeta."

    async def test_no_model_asks_the_pool_nothing(self):
        pool = _Pool([])

        for model in (None, ''):
            await ServedModels(pools=_Pools(pool)).refuse_unserved(model)  # type: ignore[arg-type]

        assert pool.asked == 0

    async def test_an_empty_pool_says_none(self):
        with pytest.raises(ModelNotServed) as refused:
            await ServedModels(pools=_Pools(_Pool([]))).refuse_unserved('m')  # type: ignore[arg-type]

        assert str(refused.value).endswith('Available: none.')


class TestWhatThePortalIsTold:
    """`GroupRefusals`: the status and the sentence the portal has always shown for each refusal."""

    @pytest.mark.parametrize(
        ('refusal', 'status_code', 'detail'),
        [
            (GroupNotFound(), 404, 'Group not found'),
            (TemplateGroupNotFound(), 404, 'The group to copy from does not exist in this organization.'),
            (EntryNotFound(), 404, 'Entry not found'),
            (NotAnOrgMember(), 404, 'This user is not a member of the organization.'),
            (GroupNameTaken('Legal'), 409, "A group named 'Legal' already exists in this organization."),
            (
                EntryNameTaken(kind='agents', entry_name='reviewer'),
                409,
                "An entry named 'reviewer' of kind 'agents' already exists in this group.",
            ),
            (
                GroupIsStandard(),
                409,
                'This group holds what igni ships and cannot be changed. '
                'Create a group from it to make your own version.',
            ),
            (ModelNotServed('m', {'a'}), 409, "No inference node serves 'm'. Available: a."),
            (KindTakesNoModel('skills'), 422, "Only agents can name a model; 'skills' cannot."),
            (UnsupportedPluginSource(), 422, str(UnsupportedPluginSource())),
        ],
    )
    def test_each_refusal(self, refusal, status_code, detail):
        with pytest.raises(HTTPException) as raised, GroupRefusals():
            raise refusal

        assert (raised.value.status_code, raised.value.detail) == (status_code, detail)

    def test_anything_unanticipated_passes_through(self):
        with pytest.raises(ValueError, match='unrelated'), GroupRefusals():
            raise ValueError('unrelated')

    def test_nothing_raised_is_nothing_answered(self):
        with GroupRefusals():
            pass
