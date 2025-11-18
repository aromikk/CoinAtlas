from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Coin
from .schemas import CoinOut


app = FastAPI(title="CoinAtlas API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # затем ограничишь
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/coins", response_model=list[CoinOut])
def list_coins(
    section_id: int | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(Coin)
    if section_id is not None:
        query = query.filter(Coin.section_id == section_id)
    coins = query.order_by(Coin.DT).all()  # сортировка по дате, потом заменим на год
    return coins


@app.get("/coins/{coin_id}", response_model=CoinOut)
def get_coin(coin_id: int, db: Session = Depends(get_db)):
    coin = db.query(Coin).filter(Coin.id == coin_id).first()
    if coin is None:
        raise HTTPException(status_code=404, detail="Coin not found")
    return coin
