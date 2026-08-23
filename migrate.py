from database import engine
from sqlalchemy import text

print("Применяем миграцию...")
with engine.connect() as conn:
    conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255) UNIQUE;"))
    conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(20) UNIQUE;"))
    conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);"))
    conn.execute(text("ALTER TABLE users ALTER COLUMN vk_id DROP NOT NULL;"))
    conn.commit()
    print("✅ Миграция выполнена успешно!")