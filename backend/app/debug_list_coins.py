from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Coin


def main():
    db: Session = SessionLocal()
    try:
        coins = db.query(Coin).limit(5).all()
        for c in coins:
            print(c.id, c.DT, c.cname, c.sname, c.nominal, c.metal)
    finally:
        db.close()


if __name__ == "__main__":
    main()