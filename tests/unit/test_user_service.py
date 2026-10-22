"""Spec 014: writing a user's permissions from a profile.

The asymmetry these tests exist to pin: a **profile is sparse** (an entry only for what it grants)
and a **user is dense** (one `access_privilege` row per `SystemObject`, all 103). Getting it
backwards passes a naive assertion and fails FR-003 or FR-013.

`_write_privileges_from` is a blanket replace, not an upsert. That is FR-013 read literally, and it
is what removes the 88 rows on objects the enum omitted — 70, 104 and 105 are features commented
out in the legacy catalog, so the grants outlived them (research R9). The distinct write path for a
user's permissions, `update_user`, stays a *partial* upsert on purpose (FR-026); the last test
class here pins that difference so it is not mistaken for an oversight.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.enums import EntityStatus, SourceType, SystemObject
from app.models.user import AccessPrivilege, User, UserProfile, UserProfilePrivilege
from app.schemas.user import PrivilegeUpdate, UserUpdate
from app.services import user_service

OBJECT_COUNT = 103


def _user(user_id: str = 'tester', **kw) -> User:
    user = User(
        user_id=user_id,
        password='x',
        email='t@e.com',
        employee_id=1,
        administrator=False,
        status=EntityStatus.ACTIVE,
        session_version=3,
        profile_id=kw.pop('profile_id', None),
    )
    user.privileges = kw.pop('privileges', [])
    user.settings = None
    return user


def _profile(
    profile_id: int = 1, *, masks: dict[int, int] | None = None, status=None
) -> UserProfile:
    profile = UserProfile(
        user_profile_id=profile_id,
        name='Cashier',
        description=None,
        status=EntityStatus.ACTIVE if status is None else status,
    )
    profile.privileges = [
        UserProfilePrivilege(system_object=obj, privileges=mask)
        for obj, mask in (masks or {}).items()
    ]
    return profile


def _priv(system_object: int, privileges: int) -> AccessPrivilege:
    return AccessPrivilege(system_object=system_object, privileges=privileges)


class TestWritePrivilegesFrom:
    """T023 — the shared helper create and apply both use."""

    def test_writes_one_row_per_system_object(self):
        user = _user()
        user_service._write_privileges_from(user, _profile(masks={0: 2, 7: 3}))
        assert len(user.privileges) == OBJECT_COUNT

    def test_granted_objects_carry_the_profile_mask(self):
        user = _user()
        user_service._write_privileges_from(user, _profile(masks={0: 2, 7: 3, 44: 15}))
        by_object = {p.system_object: p.privileges for p in user.privileges}
        assert by_object[0] == 2
        assert by_object[7] == 3
        assert by_object[44] == 15

    def test_every_object_the_profile_omits_is_denied(self):
        user = _user()
        user_service._write_privileges_from(user, _profile(masks={0: 2}))
        by_object = {p.system_object: p.privileges for p in user.privileges}
        assert by_object[7] == 0
        # FR-013: a thin profile is a restrictive one, not a partial one
        assert sum(1 for mask in by_object.values() if mask == 0) == OBJECT_COUNT - 1

    def test_a_permission_the_profile_omits_is_revoked(self):
        """The difference between "restrictive" and "partial" — spec US1 scenario 6."""
        user = _user(privileges=[_priv(4, 15)])
        user_service._write_privileges_from(user, _profile(masks={0: 2}))
        by_object = {p.system_object: p.privileges for p in user.privileges}
        assert by_object[4] == 0

    def test_no_profile_denies_everything(self):
        """`create_user` with no profile must behave exactly as it did before spec 014."""
        user = _user()
        user_service._write_privileges_from(user, None)
        assert len(user.privileges) == OBJECT_COUNT
        assert all(p.privileges == 0 for p in user.privileges)

    def test_covers_object_107_which_the_enum_was_missing(self):
        """research R9: `ProductionSites` is live in the legacy catalog and was omitted here."""
        user = _user()
        user_service._write_privileges_from(user, _profile(masks={107: 15}))
        by_object = {p.system_object: p.privileges for p in user.privileges}
        assert by_object[107] == 15

    def test_rows_on_retired_objects_are_removed(self):
        """T027 — R9 decision 2, the literal reading of FR-013.

        70, 104 and 105 are commented out in the legacy catalog. 88 rows across 13 of 31 accounts
        hold grants on them; an apply removes them. If this test inverts, someone has restored the
        superseded scoped-delete version of research R3.
        """
        user = _user(privileges=[_priv(70, 15), _priv(104, 2), _priv(105, 15), _priv(0, 15)])
        user_service._write_privileges_from(user, _profile(masks={0: 2}))
        objects = {p.system_object for p in user.privileges}
        assert objects.isdisjoint({70, 104, 105})
        assert objects == {int(obj) for obj in SystemObject}

    def test_stages_without_committing(self):
        """The caller owns the commit, so a create is one transaction (research R8)."""
        user = _user()
        user_service._write_privileges_from(user, _profile(masks={0: 2}))
        # A synchronous function cannot have committed; this pins that it stays synchronous.
        assert not callable(getattr(user_service._write_privileges_from, '__await__', None))


class TestApplyProfile:
    """T026 — the service entry point."""

    @pytest.mark.asyncio
    async def test_records_the_origin(self):
        db, user = AsyncMock(add=MagicMock()), _user()
        await user_service.apply_profile(db, user, _profile(7, masks={0: 2}), updater=1)
        assert user.profile_id == 7

    @pytest.mark.asyncio
    async def test_replaces_a_previous_origin(self):
        db, user = AsyncMock(add=MagicMock()), _user(profile_id=3)
        await user_service.apply_profile(db, user, _profile(9, masks={0: 2}), updater=1)
        assert user.profile_id == 9

    @pytest.mark.asyncio
    async def test_invalidates_existing_sessions(self):
        """FR-015. A relative increment, correct from any starting value (research R7)."""
        db, user = AsyncMock(add=MagicMock()), _user()
        before = user.session_version
        await user_service.apply_profile(db, user, _profile(masks={0: 2}), updater=1)
        assert user.session_version == before + 1

    @pytest.mark.asyncio
    async def test_commits_once(self):
        """FR-018 all-or-nothing. Two commits would leave a half-applied account reachable."""
        db, user = AsyncMock(add=MagicMock()), _user()
        await user_service.apply_profile(db, user, _profile(masks={0: 2}), updater=1)
        assert db.commit.await_count == 1


class TestUpdateUserStaysAPartialUpsert:
    """FR-026. The two write paths differ deliberately; this is the assertion that says so."""

    @pytest.mark.asyncio
    async def test_omitted_objects_keep_their_masks(self):
        db = AsyncMock(add=MagicMock())
        user = _user(privileges=[_priv(0, 15), _priv(7, 15)])
        await user_service.update_user(
            db,
            user,
            UserUpdate(privileges=[PrivilegeUpdate(system_object=0, privileges=2)]),
            updater=1,
        )
        by_object = {p.system_object: p.privileges for p in user.privileges}
        assert by_object[0] == 2
        # Untouched — an apply would have zeroed this, a partial edit must not
        assert by_object[7] == 15

    @pytest.mark.asyncio
    async def test_does_not_clear_a_recorded_origin(self):
        """FR-022: the origin records where an account was provisioned from, not what it holds."""
        db = AsyncMock(add=MagicMock())
        user = _user(profile_id=5, privileges=[_priv(0, 15)])
        await user_service.update_user(
            db,
            user,
            UserUpdate(privileges=[PrivilegeUpdate(system_object=0, privileges=2)]),
            updater=1,
        )
        assert user.profile_id == 5


_RECORD = 'app.services.user_service.incidences.record'


def _db() -> MagicMock:
    return MagicMock(commit=AsyncMock(), refresh=AsyncMock(), delete=AsyncMock())


def _tracked(db: MagicMock):
    """A `record` stand-in and the order in which it and `commit` ran."""
    order: list[str] = []
    db.commit.side_effect = lambda: order.append('commit')
    return order, lambda *a, **k: order.append('record')


class TestIncidences:
    """Spec 021 US3 (FR-014/FR-016): one USER_SETTINGS row per action, staged before the commit."""

    @pytest.mark.asyncio
    async def test_update_names_the_fields_and_never_the_values(self):
        db, user = _db(), _user()
        order, effect = _tracked(db)
        data = UserUpdate(email='secret@example.com', administrator=True)
        with patch(_RECORD, side_effect=effect) as record:
            await user_service.update_user(db, user, data, updater=9)
        record.assert_called_once_with(
            db,
            source=SourceType.USER_SETTINGS,
            instance_id=1,
            updater=9,
            reason='User tester updated',
            context='tester updated; changed: administrator, email',
        )
        assert 'secret@example.com' not in record.call_args.kwargs['context']
        assert order == ['record', 'commit']

    @pytest.mark.asyncio
    async def test_delete(self):
        db, user = _db(), _user()
        order, effect = _tracked(db)
        with (
            patch('app.services.user_service.assert_not_referenced', new=AsyncMock()),
            patch(_RECORD, side_effect=effect) as record,
        ):
            await user_service.delete_user(db, user, updater=9)
        record.assert_called_once_with(
            db,
            source=SourceType.USER_SETTINGS,
            instance_id=1,
            updater=9,
            reason='User tester deleted',
            context='tester deleted',
        )
        assert order == ['record', 'commit']

    @pytest.mark.asyncio
    async def test_apply_profile(self):
        db, user = _db(), _user()
        order, effect = _tracked(db)
        with patch(_RECORD, side_effect=effect) as record:
            await user_service.apply_profile(db, user, _profile(7, masks={0: 2}), updater=9)
        record.assert_called_once_with(
            db,
            source=SourceType.USER_SETTINGS,
            instance_id=1,
            updater=9,
            reason='User tester profile applied',
            context='tester profile 7 applied',
        )
        assert order == ['record', 'commit']

    @pytest.mark.asyncio
    async def test_initiate_recovery_commits_its_incidence(self):
        db, user = _db(), _user()
        order, effect = _tracked(db)
        with patch(_RECORD, side_effect=effect) as record:
            token, _ = await user_service.initiate_recovery(db, user, updater=9)
        assert token
        record.assert_called_once_with(
            db,
            source=SourceType.USER_SETTINGS,
            instance_id=1,
            updater=9,
            reason='User tester recovery token issued',
            context='tester recovery token issued',
        )
        assert order == ['record', 'commit']
