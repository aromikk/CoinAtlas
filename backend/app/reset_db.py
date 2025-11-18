from .database import engine, Base
from . import models  # чтобы Section / Coin были зарегистрированы в Base


def reset_db():
    # Удаляем все таблицы, описанные в моделях
    Base.metadata.drop_all(bind=engine)
    # Заново создаём по текущим моделям
    Base.metadata.create_all(bind=engine)
    print("База пересоздана (drop_all + create_all).")