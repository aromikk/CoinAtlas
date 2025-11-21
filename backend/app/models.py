from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    ForeignKey,
)
from sqlalchemy.orm import relationship

from .database import Base


class Section(Base):
    __tablename__ = "sections"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, unique=True, index=True)
    name = Column(String, nullable=False)

    coins = relationship("Coin", back_populates="section")


class Coin(Base):
    __tablename__ = "coins"

    id = Column(Integer, primary_key=True, index=True)

    section_id = Column(Integer, ForeignKey("sections.id"), nullable=False)
    section = relationship("Section", back_populates="coins")

    # из JSON'ов
    source_url = Column(String, unique=True, index=True)

    title = Column(String)
    nominal = Column(String)
    year = Column(Integer)

    letters = Column(String)
    edge = Column(String)
    quality = Column(String)
    ruler = Column(String)
    mintage = Column(String)

    material = Column(String)
    weight_g = Column(Float)
    diameter_mm = Column(Float)
    thickness_mm = Column(Float)

    catalogs = Column(String)

    image_obverse = Column(String)
    image_reverse = Column(String)

