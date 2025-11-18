from .database import engine, Base
from . import models


def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("База пересоздана (drop_all + create_all).")