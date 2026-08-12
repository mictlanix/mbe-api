"""Spec 014: the profile catalog's branching logic.

The one thing worth reading the source for rather than trusting: uniqueness compares on
`func.lower(name)`, not on the column. MariaDB's `utf8mb3_unicode_ci` would make a plain `==`
case-insensitive, so a test against MariaDB alone cannot tell the two apart — but
`tests/integration/` runs on SQLite, where `=` on `TEXT` is case-sensitive, and there the
difference is a 409 that either happens or does not (research R4).
"""

import inspect
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.enums import EntityStatus
from app.models.user import UserProfile, UserProfilePrivilege
from app.schemas.user import ProfilePrivilegeUpdate, UserProfileCreate, UserProfileUpdate
from app.services import user_profile_service


def _profile(**kw) -> UserProfile:
    profile = UserProfile(
        user_profile_id=kw.pop('user_profile_id', 1),
        name=kw.pop('name', 'Cashier'),
        description=kw.pop('description', None),
        status=kw.pop('status', EntityStatus.ACTIVE),
    )
    profile.privileges = [
        UserProfilePrivilege(system_object=obj, privileges=mask)
        for obj, mask in (kw.pop('masks', {}) or {}).items()
    ]
    return profile


class TestSparseEntries:
    """FR-003 — "no entry" is the single representation of "denied"."""

    def test_a_zero_mask_entry_is_dropped(self):
        entries = user_profile_service._entries_from(
            [
                ProfilePrivilegeUpdate(system_object=0, privileges=2),
                ProfilePrivilegeUpdate(system_object=7, privileges=0),
            ]
        )
        assert [(e.system_object, e.privileges) for e in entries] == [(0, 2)]

    def test_all_zero_masks_produce_no_entries(self):
        entries = user_profile_service._entries_from(
            [ProfilePrivilegeUpdate(system_object=0, privileges=0)]
        )
        assert entries == []

    def test_no_entries_is_valid(self):
        """A profile granting nothing is a legitimate way to express a suspended role."""
        assert user_profile_service._entries_from(None) == []
        assert user_profile_service._entries_from([]) == []

    def test_a_round_trip_is_stable(self):
        """What a read returns is what a subsequent write would produce."""
        first = user_profile_service._entries_from(
            [
                ProfilePrivilegeUpdate(system_object=0, privileges=2),
                ProfilePrivilegeUpdate(system_object=7, privileges=0),
            ]
        )
        second = user_profile_service._entries_from(
            [
                ProfilePrivilegeUpdate(system_object=e.system_object, privileges=e.privileges)
                for e in first
            ]
        )
        assert [(e.system_object, e.privileges) for e in first] == [
            (e.system_object, e.privileges) for e in second
        ]


class TestPayloadValidation:
    def test_an_unknown_system_object_is_refused(self):
        """FR-010. 104 is commented out in the legacy catalog and absent from SystemObject."""
        with pytest.raises(ValidationError):
            ProfilePrivilegeUpdate(system_object=104, privileges=2)

    def test_object_107_is_accepted(self):
        """Added by spec 014 — before it, this raised (research R9)."""
        assert ProfilePrivilegeUpdate(system_object=107, privileges=2).system_object == 107

    def test_a_mask_above_15_is_refused(self):
        with pytest.raises(ValidationError):
            ProfilePrivilegeUpdate(system_object=0, privileges=16)

    def test_a_negative_mask_is_refused(self):
        with pytest.raises(ValidationError):
            ProfilePrivilegeUpdate(system_object=0, privileges=-1)

    def test_the_same_object_twice_is_refused(self):
        """FR-002. Otherwise the profile's meaning depends on iteration order."""
        with pytest.raises(ValidationError):
            UserProfileCreate(
                name='X',
                privileges=[
                    ProfilePrivilegeUpdate(system_object=0, privileges=2),
                    ProfilePrivilegeUpdate(system_object=0, privileges=15),
                ],
            )

    def test_an_empty_name_is_refused(self):
        with pytest.raises(ValidationError):
            UserProfileCreate(name='')


class TestUniquenessIsCaseInsensitive:
    """FR-004, and the reason research R4 exists."""

    def test_the_comparison_is_lowered_on_both_sides(self):
        source = inspect.getsource(user_profile_service._assert_name_available)
        assert 'func.lower' in source, (
            'without func.lower this relies on the MariaDB collation, SQLite does not share it — '
            'the integration test asserting 409 for "cashier" would fail (research R4)'
        )
        assert 'name.lower()' in source

    @pytest.mark.asyncio
    async def test_create_checks_before_staging(self):
        """A 409 must not leave a half-built profile in the session."""
        source = inspect.getsource(user_profile_service.create_profile)
        assert source.index('_assert_name_available') < source.index('db.add')


class TestUpdateSemantics:
    @pytest.mark.asyncio
    async def test_privileges_present_replaces_the_whole_set(self):
        db = AsyncMock()
        profile = _profile(masks={0: 2, 7: 15})
        await user_profile_service.update_profile(
            db,
            profile,
            UserProfileUpdate(
                privileges=[ProfilePrivilegeUpdate(system_object=0, privileges=2)]
            ),
        )
        assert [(e.system_object, e.privileges) for e in profile.privileges] == [(0, 2)]

    @pytest.mark.asyncio
    async def test_privileges_omitted_leaves_entries_untouched(self):
        """Renaming a profile must not require resending its masks."""
        db = AsyncMock()
        profile = _profile(masks={0: 2, 7: 15})
        await user_profile_service.update_profile(
            db, profile, UserProfileUpdate(status=EntityStatus.INACTIVE)
        )
        assert len(profile.privileges) == 2
        assert profile.status == EntityStatus.INACTIVE


class TestApplyableStatus:
    """FR-017 — an inactive profile stays readable and cannot be applied."""

    def test_active_is_applyable(self):
        user_profile_service.assert_applyable(_profile(status=EntityStatus.ACTIVE))

    def test_inactive_is_refused_with_409(self):
        with pytest.raises(HTTPException) as exc:
            user_profile_service.assert_applyable(_profile(status=EntityStatus.INACTIVE))
        assert exc.value.status_code == 409

    def test_archived_is_refused_too(self):
        """FR-017 draws the line at "not active", not at one named retired state."""
        with pytest.raises(HTTPException) as exc:
            user_profile_service.assert_applyable(_profile(status=EntityStatus.ARCHIVED))
        assert exc.value.status_code == 409


class TestMasksOf:
    def test_maps_object_to_mask(self):
        assert user_profile_service.masks_of(_profile(masks={0: 2, 7: 3})) == {0: 2, 7: 3}

    def test_a_profile_with_no_entries_grants_nothing(self):
        assert user_profile_service.masks_of(_profile()) == {}


class TestDeleteIsGuardedByTheSharedHelper:
    """FR-008 — research R5: the refusal is FK metadata, not code written here."""

    def test_delete_calls_assert_not_referenced(self):
        source = inspect.getsource(user_profile_service.delete_profile)
        assert 'assert_not_referenced' in source

    def test_the_child_table_is_exempt_not_the_user_table(self):
        """`user_profile_privilege` cascades away; `user` is what must block the delete."""
        source = inspect.getsource(user_profile_service.delete_profile)
        assert 'user_profile_privilege' in source
        assert "'user'" not in source, 'exempting `user` would defeat FR-008 entirely'
