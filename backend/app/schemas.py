from typing import Optional

from pydantic import BaseModel


class SectionOut(BaseModel):
    id: int
    slug: str
    name: str

    class Config:
        from_attributes = True


class CoinOut(BaseModel):
    id: int
    section_id: int
    source_url: str

    title: str
    nominal: str
    year: Optional[int]

    letters: str
    edge: str
    quality: str
    ruler: str
    mintage: str

    material: str
    weight_g: Optional[float]
    diameter_mm: Optional[float]
    thickness_mm: Optional[float]

    catalogs: str
    image_obverse: Optional[str]
    image_reverse: Optional[str]

    class Config:
        from_attributes = True

