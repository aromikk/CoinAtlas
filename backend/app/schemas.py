from pydantic import BaseModel
from datetime import datetime


class CoinOut(BaseModel):
    id: int
    section_id: int
    DT: datetime | None = None
    cname: str
    sname: str | None = None
    nominal: str
    metal: str | None = None

    class Config:
        orm_mode = True
