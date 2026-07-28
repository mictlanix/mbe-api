"""User request schemas — the employee link is required on create (#127).

The database enforces it too (migration 012), but a rejected request names the missing field
where the caller can see it, instead of surfacing as a constraint violation.
"""

import pytest
from pydantic import ValidationError

from app.schemas.user import UserCreate, UserUpdate


class TestUserCreateRequiresAnEmployee:
    def test_rejects_a_user_with_no_employee(self):
        with pytest.raises(ValidationError) as exc:
            UserCreate(user_id='newuser', password='secret', email='a@b.com')
        assert 'employee_id' in str(exc.value)

    def test_rejects_an_explicit_null(self):
        with pytest.raises(ValidationError):
            UserCreate(user_id='newuser', password='secret', email='a@b.com', employee_id=None)

    def test_accepts_a_linked_user(self):
        data = UserCreate(user_id='newuser', password='secret', email='a@b.com', employee_id=7)
        assert data.employee_id == 7


class TestUserUpdateCannotUnlink:
    def test_omitting_the_employee_leaves_it_unchanged(self):
        # update_user assigns only when the field is not None, so an absent employee_id means
        # "unchanged" — there is no representable request that clears an existing link
        assert UserUpdate(email='a@b.com').employee_id is None
