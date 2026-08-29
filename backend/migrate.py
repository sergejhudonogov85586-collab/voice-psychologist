import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не найден в .env")

engine = create_engine(DATABASE_URL)

def run_migration():
    with engine.connect() as conn:
        # Добавляем колонки для наставника в users
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS tutor_subscription VARCHAR(50) DEFAULT 'trial';"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS tutor_end TIMESTAMP;"))
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS tutor_minutes_balance INTEGER DEFAULT 0;"))
        
        # Добавляем колонки в sessions
        conn.execute(text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS subject VARCHAR(100);"))
        conn.execute(text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS is_live BOOLEAN DEFAULT FALSE;"))
        
        # Создаём таблицы notes, marks, user_limits
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS notes (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                session_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                selection TEXT,
                color VARCHAR(20) DEFAULT 'yellow',
                created_at TIMESTAMP DEFAULT NOW()
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS marks (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                session_id INTEGER NOT NULL,
                color VARCHAR(20) DEFAULT 'blue',
                label VARCHAR(100),
                created_at TIMESTAMP DEFAULT NOW()
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_limits (
                id SERIAL PRIMARY KEY,
                user_id INTEGER UNIQUE NOT NULL,
                voice_used INTEGER DEFAULT 0,
                upload_used INTEGER DEFAULT 0,
                last_reset TIMESTAMP DEFAULT NOW()
            );
        """))
        
        conn.commit()
        print("✅ Миграция выполнена успешно!")

if __name__ == "__main__":
    run_migration()