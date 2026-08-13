"""Spec 014 driven against a real database: profiles authored, applied, and read back.

Two things only this layer can prove.

**The 107-row write.** A mocked session accepts any number of appended rows without complaint. Here
the inserts reach tables, so "a profile granting three objects denies the other 104" is a fact about
rows rather than about a list in memory.

**Case-insensitive uniqueness.** MariaDB's `utf8mb3_unicode_ci` makes a plain `=` case-insensitive,
so a test run only against MariaDB cannot distinguish `func.lower(name) == name.lower()` from
`name == name`. SQLite's `=` on `TEXT` is case-sensitive, so here the difference is a 409 that
either happens or does not. Research R4 exists because of this asymmetry, and
`test_a_name_differing_only_in_case_is_a_conflict` is the assertion that pins it.
"""

from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import AccessPrivilege, User

OBJECT_COUNT = 107

CASHIER = {
    'name': 'Cashier',
    'description': 'Till operator',
    # Sparse: three entries, not 107. The user this is applied to gets all 107.
    'privileges': [
        {'system_object': 0, 'privileges': 2},
        {'system_object': 7, 'privileges': 3},
        {'system_object': 44, 'privileges': 3},
    ],
}


async def _new_profile(client: AsyncClient, **overrides) -> dict:
    created = await client.post('/api/v1/user-profiles', json={**CASHIER, **overrides})
    assert created.status_code == 201, created.text
    return created.json()


async def _new_user(client: AsyncClient, user_id: str, **extra) -> dict:
    created = await client.post(
        '/api/v1/users',
        json={
            'user_id': user_id,
            'password': 'secret1',
            'email': f'{user_id}@example.com',
            'employee_id': 1,
            **extra,
        },
    )
    return created


class TestCatalog:
    async def test_a_profile_is_created_read_edited_and_deleted(
        self, client: AsyncClient, seeded: None
    ) -> None:
        profile = await _new_profile(client)
        profile_id = profile['user_profile_id']
        # Sparse on the way out as well as in (FR-003)
        assert len(profile['privileges']) == 3

        read = await client.get(f'/api/v1/user-profiles/{profile_id}')
        assert read.status_code == 200, read.text
        assert read.json()['name'] == 'Cashier'
        assert {e['system_object'] for e in read.json()['privileges']} == {0, 7, 44}

        listed = await client.get('/api/v1/user-profiles')
        assert listed.status_code == 200, listed.text
        assert listed.json()['total'] == 1
        # The list item deliberately omits entries
        assert 'privileges' not in listed.json()['items'][0]

        renamed = await client.put(
            f'/api/v1/user-profiles/{profile_id}',
            json={'name': 'Head Cashier', 'privileges': [{'system_object': 0, 'privileges': 15}]},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()['name'] == 'Head Cashier'
        entries = [(e['system_object'], e['privileges']) for e in renamed.json()['privileges']]
        assert entries == [(0, 15)]

        deleted = await client.delete(f'/api/v1/user-profiles/{profile_id}')
        assert deleted.status_code == 204, deleted.text
        assert (await client.get(f'/api/v1/user-profiles/{profile_id}')).status_code == 404

    async def test_a_name_differing_only_in_case_is_a_conflict(
        self, client: AsyncClient, seeded: None
    ) -> None:
        """FR-004 under SQLite, where the collation gives nothing away (research R4)."""
        await _new_profile(client)
        clash = await client.post('/api/v1/user-profiles', json={'name': 'cashier'})
        assert clash.status_code == 409, clash.text
        assert 'already exists' in clash.json()['detail']

    async def test_a_zero_mask_entry_is_not_stored(
        self, client: AsyncClient, seeded: None
    ) -> None:
        profile = await _new_profile(
            client,
            name='Sparse',
            privileges=[
                {'system_object': 0, 'privileges': 2},
                {'system_object': 7, 'privileges': 0},
            ],
        )
        assert [e['system_object'] for e in profile['privileges']] == [0]

    async def test_an_unknown_system_object_is_refused(
        self, client: AsyncClient, seeded: None
    ) -> None:
        """104 is commented out in the legacy catalog, so `SystemObject` omits it (research R9)."""
        bad = await client.post(
            '/api/v1/user-profiles',
            json={'name': 'Bad', 'privileges': [{'system_object': 104, 'privileges': 2}]},
        )
        assert bad.status_code == 422, bad.text


class TestApply:
    async def test_provisioning_at_creation_writes_all_107_rows(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        profile = await _new_profile(client)
        created = await _new_user(client, 'qsone', profile_id=profile['user_profile_id'])
        assert created.status_code == 201, created.text

        body = created.json()
        assert len(body['privileges']) == OBJECT_COUNT
        assert body['profile_id'] == profile['user_profile_id']
        assert body['profile_name'] == 'Cashier'

        by_object = {e['system_object']: e['privileges'] for e in body['privileges']}
        assert by_object[0] == 2
        assert by_object[7] == 3
        assert by_object[44] == 3
        # Every object the profile omits is denied — the difference between restrictive and partial
        assert sum(1 for mask in by_object.values() if mask == 0) == OBJECT_COUNT - 3
        # Including 107, which the enum was missing before this feature
        assert by_object[107] == 0

        rows = (
            await db.execute(select(AccessPrivilege).where(AccessPrivilege.user_id == 'qsone'))
        ).scalars().all()
        assert len(rows) == OBJECT_COUNT

    async def test_applying_replaces_everything_the_account_held(
        self, client: AsyncClient, seeded: None
    ) -> None:
        profile = await _new_profile(client)
        assert (await _new_user(client, 'qstwo')).status_code == 201

        granted = await client.put(
            '/api/v1/users/qstwo', json={'privileges': [{'system_object': 4, 'privileges': 15}]}
        )
        assert granted.status_code == 200, granted.text
        assert {e['system_object']: e['privileges'] for e in granted.json()['privileges']}[4] == 15

        applied = await client.post(
            f'/api/v1/user-profiles/{profile["user_profile_id"]}/apply/qstwo'
        )
        assert applied.status_code == 200, applied.text
        by_object = {e['system_object']: e['privileges'] for e in applied.json()['privileges']}
        # FR-013: the profile does not name warehouses, so the hand-granted permission is gone
        assert by_object[4] == 0
        assert by_object[0] == 2

    async def test_applying_removes_rows_on_objects_the_enum_omits(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """Research R9 decision 2, and the reason it is a blanket delete.

        70, 104 and 105 are commented out in the legacy catalog; 88 rows of grants outlived the
        features. An apply removes them. If this inverts, the superseded scoped-delete version of
        research R3 has been restored.
        """
        profile = await _new_profile(client)
        assert (await _new_user(client, 'qsthree')).status_code == 201

        for retired in (70, 104, 105):
            db.add(AccessPrivilege(user_id='qsthree', system_object=retired, privileges=15))
        await db.commit()

        applied = await client.post(
            f'/api/v1/user-profiles/{profile["user_profile_id"]}/apply/qsthree'
        )
        assert applied.status_code == 200, applied.text

        rows = (
            await db.execute(select(AccessPrivilege).where(AccessPrivilege.user_id == 'qsthree'))
        ).scalars().all()
        objects = {row.system_object for row in rows}
        assert objects.isdisjoint({70, 104, 105})
        assert len(rows) == OBJECT_COUNT

    async def test_applying_twice_does_not_violate_the_unique_constraint(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """Regression for issue #160's constraint meeting spec 014's apply.

        `UNIQUE (user, object)` and a clear-then-re-append implementation are incompatible:
        SQLAlchemy emits INSERTs before DELETEs within one flush, so re-inserting the same pairs
        collides with the rows being deleted and every apply raises `IntegrityError`. That is why
        `_write_privileges_from` updates in place. Applying twice is the shortest reproduction —
        the second apply is entirely re-writes of rows that already exist.
        """
        profile = await _new_profile(client)
        pid = profile['user_profile_id']
        assert (await _new_user(client, 'qsuniq', profile_id=pid)).status_code == 201

        for attempt in range(3):
            applied = await client.post(f'/api/v1/user-profiles/{pid}/apply/qsuniq')
            assert applied.status_code == 200, f'apply #{attempt + 2}: {applied.text}'

        rows = (
            await db.execute(select(AccessPrivilege).where(AccessPrivilege.user_id == 'qsuniq'))
        ).scalars().all()
        assert len(rows) == OBJECT_COUNT
        pairs = {(r.user_id, r.system_object) for r in rows}
        assert len(pairs) == OBJECT_COUNT, 'a duplicate pair survived the constraint'

    async def test_applying_invalidates_sessions(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        profile = await _new_profile(client)
        created = await _new_user(client, 'qsfour')
        before = created.json()['session_version']

        applied = await client.post(
            f'/api/v1/user-profiles/{profile["user_profile_id"]}/apply/qsfour'
        )
        assert applied.json()['session_version'] == before + 1

    async def test_an_inactive_profile_cannot_be_applied(
        self, client: AsyncClient, seeded: None
    ) -> None:
        profile = await _new_profile(client, status=1)
        assert (await _new_user(client, 'qsfive')).status_code == 201
        refused = await client.post(
            f'/api/v1/user-profiles/{profile["user_profile_id"]}/apply/qsfive'
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()['detail'] == 'Profile is not active'


class TestCreationIsAtomic:
    """FR-011 — the #154 failure shape: a 4xx that had already written the row."""

    async def test_an_unknown_profile_leaves_no_user(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        refused = await _new_user(client, 'qsghost', profile_id=99999)
        assert refused.status_code == 404, refused.text
        assert (await client.get('/api/v1/users/qsghost')).status_code == 404
        assert (await db.execute(select(User).where(User.user_id == 'qsghost'))).first() is None

    async def test_an_inactive_profile_leaves_no_user(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        profile = await _new_profile(client, status=1)
        refused = await _new_user(client, 'qsghost2', profile_id=profile['user_profile_id'])
        assert refused.status_code == 409, refused.text
        assert (await db.execute(select(User).where(User.user_id == 'qsghost2'))).first() is None

    async def test_creating_without_a_profile_is_unchanged(
        self, client: AsyncClient, seeded: None
    ) -> None:
        """FR-027 — an account created with no profile denies everything and records no origin."""
        created = await _new_user(client, 'qsplain')
        assert created.status_code == 201, created.text
        body = created.json()
        assert body['profile_id'] is None
        assert body['profile_name'] is None
        assert len(body['privileges']) == OBJECT_COUNT
        assert all(e['privileges'] == 0 for e in body['privileges'])


class TestOriginIsVisibleAndFilterable:
    async def test_editing_a_profile_changes_nobody(
        self, client: AsyncClient, seeded: None
    ) -> None:
        """SC-006, and the part administrators most expect to work the other way."""
        profile = await _new_profile(client)
        pid = profile['user_profile_id']
        assert (await _new_user(client, 'qssix', profile_id=pid)).status_code == 201

        await client.put(
            f'/api/v1/user-profiles/{pid}',
            json={'privileges': [{'system_object': 7, 'privileges': 15}]},
        )
        read = await client.get('/api/v1/users/qssix')
        by_object = {e['system_object']: e['privileges'] for e in read.json()['privileges']}
        assert by_object[7] == 3, 'the edit must not propagate to a provisioned account'

        # Re-applying is what carries the correction across
        await client.post(f'/api/v1/user-profiles/{pid}/apply/qssix')
        read = await client.get('/api/v1/users/qssix')
        by_object = {e['system_object']: e['privileges'] for e in read.json()['privileges']}
        assert by_object[7] == 15

    async def test_the_user_list_filters_by_profile_and_names_it(
        self, client: AsyncClient, seeded: None
    ) -> None:
        profile = await _new_profile(client)
        pid = profile['user_profile_id']
        assert (await _new_user(client, 'qsseven', profile_id=pid)).status_code == 201
        assert (await _new_user(client, 'qseight', profile_id=pid)).status_code == 201
        assert (await _new_user(client, 'qsnine')).status_code == 201

        filtered = await client.get(f'/api/v1/users?profile_id={pid}')
        assert filtered.status_code == 200, filtered.text
        rows = filtered.json()['items']
        assert {r['user_id'] for r in rows} == {'qsseven', 'qseight'}
        assert all(r['profile_name'] == 'Cashier' for r in rows)

        unfiltered = await client.get('/api/v1/users')
        by_id = {r['user_id']: r for r in unfiltered.json()['items']}
        assert by_id['qsnine']['profile_id'] is None
        assert by_id['qsnine']['profile_name'] is None

    async def test_profile_names_cost_one_query_for_the_whole_page(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """The N+1 rule `fk_expansion` exists to enforce, asserted by count rather than by eye.

        Three provisioned accounts must not cost three profile lookups. Counting statements is the
        only way to tell a batched resolve from a per-row one — the response body looks identical
        either way, which is exactly how an N+1 survives review.
        """
        profile = await _new_profile(client)
        pid = profile['user_profile_id']
        for name in ('qsn1', 'qsn2', 'qsn3'):
            assert (await _new_user(client, name, profile_id=pid)).status_code == 201

        selects: list[str] = []

        def _record(_conn, _cursor, statement, _params, _context, _many) -> None:  # noqa: ANN001
            if 'user_profile' in statement and statement.lstrip().upper().startswith('SELECT'):
                selects.append(statement)

        # AsyncSession.get_bind() hands back the underlying sync Engine, which is the one the
        # client's own session also runs on — so this sees the request's statements, not just this
        # session's.
        engine = db.get_bind()
        event.listen(engine, 'before_cursor_execute', _record)
        try:
            listed = await client.get('/api/v1/users')
            assert listed.status_code == 200, listed.text
        finally:
            event.remove(engine, 'before_cursor_execute', _record)

        rows = [r for r in listed.json()['items'] if r['profile_id'] == pid]
        assert len(rows) == 3
        assert all(r['profile_name'] == 'Cashier' for r in rows)
        # `batch_fetch` issues exactly one IN(...) lookup for the page. Three would be the N+1.
        assert len(selects) == 1, f'expected 1 profile lookup for the page, got {len(selects)}'

    async def test_a_hand_edit_does_not_clear_the_origin(
        self, client: AsyncClient, seeded: None
    ) -> None:
        """FR-022 — the origin records where an account came from, not what it holds."""
        profile = await _new_profile(client)
        pid = profile['user_profile_id']
        assert (await _new_user(client, 'qsten', profile_id=pid)).status_code == 201

        edited = await client.put(
            '/api/v1/users/qsten', json={'privileges': [{'system_object': 4, 'privileges': 15}]}
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()['profile_id'] == pid


class TestDeletionIsRefusedWhileReferenced:
    async def test_a_referenced_profile_cannot_be_deleted(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """FR-008 — produced by `assert_not_referenced` off FK metadata (research R5)."""
        profile = await _new_profile(client)
        pid = profile['user_profile_id']
        assert (await _new_user(client, 'qseleven', profile_id=pid)).status_code == 201

        refused = await client.delete(f'/api/v1/user-profiles/{pid}')
        assert refused.status_code == 409, refused.text
        assert 'user' in refused.json()['detail']

        # The refusal wrote nothing
        rows = (
            await db.execute(select(AccessPrivilege).where(AccessPrivilege.user_id == 'qseleven'))
        ).scalars().all()
        assert len(rows) == OBJECT_COUNT
        assert (await client.get(f'/api/v1/user-profiles/{pid}')).status_code == 200
