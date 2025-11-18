from .database import engine, Base
from . import models


def init_db():
    Base.metadata.create_all(bind=engine)