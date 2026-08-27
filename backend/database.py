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

def column_exists(conn, table_name, column_name):
    """Проверяет наличие колонки через information_schema (для PostgreSQL)."""
    result = conn.execute(
        text("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = :table AND column_name = :column
        """),
        {"table": table_name, "column": column_name}
    ).fetchone()
    return result is not None

def apply_migrations():
    """Добавляет колонки mode, is_live, subject в таблицу sessions, если их нет."""
    with engine.connect() as conn:
        if not column_exists(conn, "sessions", "mode"):
            conn.execute(text("ALTER TABLE sessions ADD COLUMN mode VARCHAR(20) DEFAULT 'psychologist';"))
            conn.commit()
        if not column_exists(conn, "sessions", "is_live"):
            conn.execute(text("ALTER TABLE sessions ADD COLUMN is_live BOOLEAN DEFAULT FALSE;"))
            conn.commit()
        if not column_exists(conn, "sessions", "subject"):
            conn.execute(text("ALTER TABLE sessions ADD COLUMN subject VARCHAR(100);"))
            conn.commit()
        print("✅ Миграция выполнена (колонки добавлены, если отсутствовали)")

def init_db():
    Base.metadata.create_all(bind=engine)
    apply_migrations()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()