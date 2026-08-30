"""`SystemObject` is the width of the permission matrix, so its size is an assertion (spec 014).

A user's `access_privilege` rows are written one per member by `user_service`, and an applied
profile replaces all of them. If a member is added or removed without anyone noticing, every count
those code paths produce shifts silently and the tests asserting a row count start failing for a
reason that has nothing to do with the change being made.

**The width is 103, and was 107 until spec 016.** Four entries left the catalog when the monolith
dropped the technical service and vehicle service order modules — their screens and their
`access_privilege` rows went in the same migration (`mictlanix/mbe#37`), so keeping the members
would have meant re-creating four rows per account for menu entries that no longer exist. Their
numbers are retired, never recycled: the catalog mirrors the legacy application's identifiers and a
number that changed meaning would re-point every stored grant naming it.

**Do not confuse the width with `PRODUCTION_SITES`.** Both were `107` until spec 016, which is
coincidence and not a relationship — identifiers run to 113, so the count and the highest identifier
were never the same number. The width is now 103; `PRODUCTION_SITES` is still 107.

`PRODUCTION_SITES = 107` is pinned by name because it was missing until spec 014, behind a
`# 107 absent` comment the data contradicted: the legacy catalog
(`../mbe/Model/Constants/SystemObjects.cs`) declares `ProductionSites = 107` uncommented, and 29 of
31 accounts already held a row for it. See research R9.
"""

from app.enums import AccessRight, SystemObject


def test_the_matrix_is_103_objects_wide() -> None:
    """The number every full-replace write produces. Changing it is a deliberate act."""
    assert len(list(SystemObject)) == 103


def test_production_sites_is_107() -> None:
    """Added by spec 014. It gates nothing in this API — no production-sites endpoint exists —
    but a profile has to be able to express it, and full replace has to cover it."""
    assert SystemObject.PRODUCTION_SITES == 107
    assert SystemObject(107).name == 'PRODUCTION_SITES'


def test_the_retired_service_objects_are_gone_and_their_neighbours_are_not() -> None:
    """Spec 016. 58, 64, 65 and 90 lost their tables, their screens and their rows.

    The neighbours are asserted beside them because 90 sat inside a live run — 88, 89 and 91 are
    fleet objects that survive, and an off-by-one while removing 90 would take one of them with it.
    """
    values = {int(obj) for obj in SystemObject}

    assert values.isdisjoint({58, 64, 65, 90})
    assert SystemObject.VEHICLE == 88
    assert SystemObject.VEHICLE_OPERATORS == 89
    assert SystemObject.FOR_DELIVER == 91


def test_the_declared_absences_stay_absent() -> None:
    """31, 70, 76-78, 104 and 105 mirror the legacy catalog's commented-out entries.

    Unlike 107, these are correct omissions. `access_privilege` holds rows against 70, 104 and 105,
    but they are grants that outlived their features — which is why an apply deletes them rather
    than preserving them (research R9).
    """
    values = {int(obj) for obj in SystemObject}
    assert values.isdisjoint({31, 70, 76, 77, 78, 104, 105})


def test_every_value_fits_the_mask_vocabulary() -> None:
    """A profile entry's mask is validated against 0-15; the objects it keys are these."""
    assert max(int(obj) for obj in SystemObject) == 113
    full = AccessRight.CREATE | AccessRight.READ | AccessRight.UPDATE | AccessRight.DELETE
    assert int(full) == 15
