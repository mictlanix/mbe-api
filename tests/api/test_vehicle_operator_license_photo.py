"""A vehicle operator's driver license photo is private: stored outside /images, served only
behind authentication, and its file is removed whenever the row stops referencing it."""

import io
from collections.abc import Generator
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app

_URL = '/api/v1/vehicle-operators'


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    photos = tmp_path / 'license-photos'
    images = tmp_path / 'images'
    photos.mkdir()
    images.mkdir()
    monkeypatch.setattr(settings, 'license_photos_dir', str(photos))
    monkeypatch.setattr(settings, 'images_dir', str(images))
    return SimpleNamespace(photos=photos, images=images)


def _auth() -> MagicMock:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester', session_version=1, administrator=True, facility_id=None, employee_id=7
    )
    db = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.delete = AsyncMock()

    async def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    return db


def _png(width: int = 200, height: int = 100) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', (width, height), (255, 0, 0)).save(buf, format='PNG')
    return buf.getvalue()


def _employee() -> SimpleNamespace:
    return SimpleNamespace(
        employee_id=1,
        first_name='Ana',
        last_name='Perez',
        nickname='Ana',
        gender=0,
        birthday=date(1990, 1, 1),
        taxpayer_id=None,
        sales_person=False,
        status=0,
        personal_id=None,
        start_job_date=date(2020, 1, 1),
        enroll_number=None,
        comment=None,
    )


def _operator(photo: str | None = None) -> SimpleNamespace:
    emp = _employee()
    return SimpleNamespace(
        vehicle_operator_id=1,
        driver_detail=emp,
        creator_detail=emp,
        updater_detail=emp,
        license_type='A',
        driver_license_number='X1',
        issue_date=date(2026, 1, 1),
        expiration_date=date(2030, 1, 1),
        issuing_location='CDMX',
        creation_time=datetime(2026, 1, 1),
        modification_time=datetime(2026, 1, 1),
        status=0,
        license_photo=photo,
        has_license_photo=photo is not None,
    )


class _Operator(SimpleNamespace):
    """Keeps the derived flag in step with the column, as the model property does."""

    @property
    def has_license_photo(self) -> bool:
        return self.license_photo is not None


def _live_operator(photo: str | None = None) -> _Operator:
    data = vars(_operator(photo))
    data.pop('has_license_photo')
    return _Operator(**data)


def _found(vo):
    return patch(
        'app.services.vehicle_operator_service.get_vehicle_operator',
        new=AsyncMock(return_value=vo),
    )


async def _post(content: bytes, name: str = 'license.png'):
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        return await c.post(
            f'{_URL}/1/license-photo', files={'file': (name, content, 'image/png')}
        )


async def _call(method: str, path: str = '/1/license-photo'):
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        return await c.request(method, f'{_URL}{path}')


def _files(directory) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


@pytest.mark.asyncio
async def test_upload_stores_the_photo_and_flags_it(dirs) -> None:
    _auth()
    vo = _live_operator()
    with _found(vo):
        r = await _post(_png())

    assert r.status_code == 200
    assert r.json()['has_license_photo'] is True
    assert _files(dirs.photos) == [vo.license_photo]
    assert _files(dirs.images) == []


@pytest.mark.asyncio
async def test_the_response_never_carries_the_filename(dirs) -> None:
    _auth()
    vo = _live_operator()
    with _found(vo):
        r = await _post(_png())

    assert vo.license_photo not in r.text
    assert 'license_photo' not in r.json()
    props = app.openapi()['components']['schemas']['VehicleOperatorResponse']['properties']
    assert 'license_photo' not in props
    assert 'has_license_photo' in props


@pytest.mark.asyncio
async def test_upload_for_an_unknown_operator_is_404(dirs) -> None:
    _auth()
    with _found(None):
        r = await _post(_png())

    assert r.status_code == 404
    assert _files(dirs.photos) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('content', [b'not an image', b'x' * (2 * 1024 * 1024 + 1)])
async def test_a_bad_upload_is_422_and_keeps_the_existing_photo(dirs, content) -> None:
    _auth()
    (dirs.photos / 'keep.png').write_bytes(_png())
    vo = _live_operator('keep.png')
    with _found(vo):
        r = await _post(content)

    assert r.status_code == 422
    assert vo.license_photo == 'keep.png'
    assert _files(dirs.photos) == ['keep.png']


@pytest.mark.asyncio
async def test_a_second_upload_removes_the_first_file(dirs) -> None:
    _auth()
    vo = _live_operator()
    with _found(vo):
        await _post(_png())
        first = vo.license_photo
        await _post(_png(300, 300))

    assert vo.license_photo != first
    assert _files(dirs.photos) == [vo.license_photo]


@pytest.mark.asyncio
async def test_get_returns_the_png(dirs) -> None:
    _auth()
    (dirs.photos / 'p.png').write_bytes(_png())
    with _found(_live_operator('p.png')):
        r = await _call('GET')

    assert r.status_code == 200
    assert r.headers['content-type'] == 'image/png'
    assert r.content == _png()


@pytest.mark.asyncio
async def test_get_requires_a_token(dirs) -> None:
    (dirs.photos / 'p.png').write_bytes(_png())

    r = await _call('GET')

    assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_is_404_without_a_photo(dirs) -> None:
    _auth()
    with _found(_live_operator()):
        r = await _call('GET')

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_is_404_when_the_file_is_missing(dirs) -> None:
    _auth()
    with _found(_live_operator('ghost.png')):
        r = await _call('GET')

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_photo_removes_the_file(dirs) -> None:
    _auth()
    (dirs.photos / 'p.png').write_bytes(_png())
    vo = _live_operator('p.png')
    with _found(vo):
        r = await _call('DELETE')

    assert r.status_code == 204
    assert vo.license_photo is None
    assert _files(dirs.photos) == []


@pytest.mark.asyncio
async def test_delete_photo_is_404_without_one(dirs) -> None:
    _auth()
    with _found(_live_operator()):
        r = await _call('DELETE')

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_deleting_the_operator_removes_the_photo_file(dirs) -> None:
    _auth()
    (dirs.photos / 'p.png').write_bytes(_png())
    with (
        _found(_live_operator('p.png')),
        patch('app.services.vehicle_operator_service.assert_not_referenced', AsyncMock()),
    ):
        r = await _call('DELETE', '/1')

    assert r.status_code == 204
    assert _files(dirs.photos) == []


@pytest.mark.parametrize(
    'path',
    [
        '/api/v1/vehicle-operators/{vehicle_operator_id}/license-photo',
        '/api/v1/delivery-orders/{delivery_order_id}/proof/image',
    ],
)
def test_image_routes_declare_a_png_body(path: str) -> None:
    # Undeclared, these advertised `application/json` and the generated client lost the bytes (#257)
    content = app.openapi()['paths'][path]['get']['responses']['200']['content']
    assert content == {'image/png': {'schema': {'type': 'string', 'format': 'binary'}}}
