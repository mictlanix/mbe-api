from pydantic import BaseModel


class SatCatalogResponse(BaseModel):
    id: str
    description: str | None = None
