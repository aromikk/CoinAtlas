from sqlalchemy import Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import relationship

from .database import Base


class Section(Base):
    __tablename__ = "sections"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True)      # rsfsr, ussr_regular и т.п.
    title = Column(String, nullable=False)              # "Монеты СССР"
    subtitle = Column(String, nullable=True)            # "(1961–1991)"

    coins = relationship("Coin", back_populates="section")


class Coin(Base):
    __tablename__ = "coins"

    id = Column(Integer, primary_key=True, index=True)
    section_id = Column(Integer, ForeignKey("sections.id"), nullable=False)

    DT = Column(String, nullable=False)               # полное название
    cname = Column(String, nullable=False)
    sname = Column(String, nullable=True)            # "10 копеек", "2 рубля" и т.п.
    nominal = Column(String, nullable=False)               # "Регулярный чекан", "ГВС", ...

    metal = Column(String, nullable=False)
    #fineness = Column(String, nullable=True)
    #weight_g = Column(Float, nullable=True)
    #diameter_mm = Column(Float, nullable=True)
    #edge = Column(String, nullable=True)                # гурт
    #mint = Column(String, nullable=True)                # монетный двор
    #mintage = Column(Integer, nullable=True)            # тираж
    #catalog_code = Column(String, nullable=True)        # обозначение в каталоге (если есть)

    section = relationship("Section", back_populates="coins")