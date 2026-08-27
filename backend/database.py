from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from models import Base
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./voice_psychologist.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)
    # После создания таблиц применяем миграцию для добавления недостающих колонок
    apply_migrations()

def apply_migrations():
    """Добавляет колонки mode, is_live, subject в таблицу sessions, если их нет."""
    with engine.connect() as conn:
        # Проверяем наличие колонки mode (если её нет – добавляем все три)
        try:
            conn.execute(text("SELECT mode FROM sessions LIMIT 1"))
        except Exception:
            # Колонки нет – добавляем
            conn.execute(text("ALTER TABLE sessions ADD COLUMN mode VARCHAR(20) DEFAULT 'psychologist';"))
            conn.execute(text("ALTER TABLE sessions ADD COLUMN is_live BOOLEAN DEFAULT FALSE;"))
            conn.execute(text("ALTER TABLE sessions ADD COLUMN subject VARCHAR(100);"))
            conn.commit()
            print("✅ Миграция выполнена – добавлены колонки mode, is_live, subject")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()