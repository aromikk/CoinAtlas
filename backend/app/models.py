from sqlalchemy import Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import relationship

from .database import Base


class Section(Base):
    __tablename__ = "sections"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True)
    title = Column(String, nullable=False)
    subtitle = Column(String, nullable=True)

    coins = relationship("Coin", back_populates="section")


class Coin(Base):
    __tablename__ = "coins"

    id = Column(Integer, primary_key=True, index=True)
    section_id = Column(Integer, ForeignKey("sections.id"), nullable=False)

    DT = Column(String, nullable=False)
    cname = Column(String, nullable=False)
    sname = Column(String, nullable=True)
    nominal = Column(String, nullable=False)

    metal = Column(String, nullable=False)
    #fineness = Column(String, nullable=True)
    #weight_g = Column(Float, nullable=True)
    #diameter_mm = Column(Float, nullable=True)
    #edge = Column(String, nullable=True)
    #mint = Column(String, nullable=True)
    #mintage = Column(Integer, nullable=True)
    #catalog_code = Column(String, nullable=True)

    section = relationship("Section", back_populates="coins")