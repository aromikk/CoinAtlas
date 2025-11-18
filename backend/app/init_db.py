from .database import engine, Base
from . import models  # важно: чтобы Section и Coin зарегистрировались в Base


def init_db():
    # Создаёт таблицы для всех моделей, унаследованных от Base
    Base.metadata.create_all(bind=engine)