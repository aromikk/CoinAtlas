from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session

from .database import SessionLocal
from . import models
from .schemas import SectionOut, CoinOut


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app = FastAPI(title="CoinAtlas API")


@app.get("/sections", response_model=List[SectionOut])
def list_sections(db: Session = Depends(get_db)):
    return db.query(models.Section).order_by(models.Section.id).all()


@app.get("/coins", response_model=List[CoinOut])
def list_coins(
    section_slug: Optional[str] = None,
    year: Optional[int] = None,
    nominal: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(models.Coin)

    if section_slug is not None:
        q = q.join(models.Section).filter(models.Section.slug == section_slug)

    if year is not None:
        q = q.filter(models.Coin.year == year)

    if nominal is not None:
        q = q.filter(models.Coin.nominal.ilike(f"%{nominal}%"))

    q = q.order_by(models.Coin.year, models.Coin.nominal, models.Coin.id)
    return q.offset(offset).limit(limit).all()


@app.get("/coins/{coin_id}", response_model=CoinOut)
def get_coin(coin_id: int, db: Session = Depends(get_db)):
    coin = db.query(models.Coin).filter(models.Coin.id == coin_id).first()
    if coin is None:
        raise HTTPException(status_code=404, detail="Монета не найдена")
    return coin

