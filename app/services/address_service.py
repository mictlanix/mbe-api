from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AddressType, EntityStatus
from app.models.core import Address
from app.schemas.core import AddressCreate, AddressUpdate


async def list_addresses(
    db: AsyncSession,
    *,
    search: str | None = None,
    type: AddressType | None = None,
    status: EntityStatus | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Address], int]:
    base = select(Address)
    count_q = select(func.count()).select_from(Address)

    if search:
        term = f'%{search}%'
        condition = or_(
            Address.nickname.ilike(term),
            Address.street.ilike(term),
            Address.neighborhood.ilike(term),
            Address.borough.ilike(term),
            Address.city.ilike(term),
            Address.postal_code.ilike(term),
        )
        base = base.where(condition)
        count_q = count_q.where(condition)

    if type is not None:
        base = base.where(Address.type == type)
        count_q = count_q.where(Address.type == type)
    if status is not None:
        base = base.where(Address.status == status)
        count_q = count_q.where(Address.status == status)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_address(db: AsyncSession, address_id: int) -> Address | None:
    return await db.get(Address, address_id)


async def create_address(db: AsyncSession, data: AddressCreate) -> Address:
    address = Address(
        nickname=data.nickname,
        type=data.type,
        street=data.street,
        exterior_number=data.exterior_number,
        interior_number=data.interior_number,
        postal_code=data.postal_code,
        neighborhood=data.neighborhood,
        locality=data.locality,
        borough=data.borough,
        state=data.state,
        city=data.city,
        country=data.country,
        url_address=data.url_address,
        comment=data.comment,
        status=data.status,
    )
    db.add(address)
    await db.commit()
    await db.refresh(address)
    return address


async def update_address(db: AsyncSession, address: Address, data: AddressUpdate) -> Address:
    if data.nickname is not None:
        address.nickname = data.nickname
    if data.type is not None:
        address.type = data.type
    if data.street is not None:
        address.street = data.street
    if data.exterior_number is not None:
        address.exterior_number = data.exterior_number
    if data.interior_number is not None:
        address.interior_number = data.interior_number
    if data.postal_code is not None:
        address.postal_code = data.postal_code
    if data.neighborhood is not None:
        address.neighborhood = data.neighborhood
    if data.locality is not None:
        address.locality = data.locality
    if data.borough is not None:
        address.borough = data.borough
    if data.state is not None:
        address.state = data.state
    if data.city is not None:
        address.city = data.city
    if data.country is not None:
        address.country = data.country
    if data.url_address is not None:
        address.url_address = data.url_address
    if data.comment is not None:
        address.comment = data.comment
    if data.status is not None:
        address.status = data.status
    await db.commit()
    await db.refresh(address)
    return address


async def delete_address(db: AsyncSession, address: Address) -> None:
    await db.delete(address)
    await db.commit()
