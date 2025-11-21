from .database import Base, engine
from . import models


def init_db():
    print("Создаю таблицы в БД...")
    Base.metadata.create_all(bind=engine)
    print("Готово.")


if __name__ == "__main__":
    init_db()

