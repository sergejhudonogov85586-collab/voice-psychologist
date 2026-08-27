from database import engine
from sqlalchemy import text

def upgrade():
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS mode VARCHAR(20) DEFAULT 'psychologist';"))
        conn.execute(text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS is_live BOOLEAN DEFAULT FALSE;"))
        conn.execute(text("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS subject VARCHAR(100);"))
        conn.commit()
        print("✅ Колонки добавлены в таблицу sessions")

if __name__ == "__main__":
    upgrade()