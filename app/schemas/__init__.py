from pydantic import BaseModel


class ListResponse[T](BaseModel):
    items: list[T]
    total: int
