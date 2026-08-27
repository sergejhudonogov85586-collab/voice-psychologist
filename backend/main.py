import os
import re
import uuid
import logging
import smtplib
import random
import requests
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import desc, func, text
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from database import get_db, init_db   # init_db теперь включает миграцию
from models import (
    User, Session, Emotion, SupportMessage,
    Payment, CarouselTip, SystemSetting, UserLog, UserLimit,
    Note, Mark
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI(title="Самопознание")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Инициализация БД и применение миграций при старте
init_db()

app = FastAPI(title="Самопознание")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkeychangeinproduction")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 43200  # 30 дней

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

# SMTP
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.yandex.ru")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")

# Yandex API
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")

# === Pydantic модели ===
class UserCreate(BaseModel):
    email: str
    password: str
    name: str = "Пользователь"

class Token(BaseModel):
    access_token: str
    token_type: str

class UserOut(BaseModel):
    id: int
    email: str | None
    phone: str | None
    name: str
    psychologist_subscription: str
    psychologist_end: datetime | None
    tutor_subscription: str
    tutor_end: datetime | None
    tutor_minutes_balance: int
    has_seen_welcome: bool
    voice_responses_enabled: bool
    partner_code: str | None
    partner_id: int | None
    session_count: int = 0
    avg_mood: float | None = None
    has_access: bool = True

# === Вспомогательные функции ===
def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except:
        return False

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme), db = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception
    return user

def get_or_create_vk_user(vk_id: str, db):
    user = db.query(User).filter(User.vk_id == vk_id).first()
    if not user:
        user = User(
            vk_id=vk_id,
            name="Пользователь",
            psychologist_subscription="trial",
            psychologist_end=datetime.utcnow() + timedelta(days=3),
            tutor_subscription="trial",
            tutor_end=datetime.utcnow() + timedelta(days=3)
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user

def has_psychologist_access(user: User) -> bool:
    if user.psychologist_subscription == "premium":
        return True
    if user.psychologist_end and datetime.utcnow() < user.psychologist_end:
        return True
    return False

def has_tutor_access(user: User) -> bool:
    if user.tutor_subscription == "premium":
        return True
    if user.tutor_end and datetime.utcnow() < user.tutor_end:
        return True
    return False

def send_verification_email(email: str, code: str):
    if not SMTP_USER or not SMTP_PASSWORD:
        logger.error("SMTP не настроен. Письмо не отправлено.")
        return False
    try:
        msg = MIMEText(f"Ваш код подтверждения: {code}\nКод действует 10 минут.")
        msg['Subject'] = 'Подтверждение email'
        msg['From'] = SMTP_FROM
        msg['To'] = email

        logger.info(f"Попытка отправить письмо на {email} через {SMTP_HOST}:{SMTP_PORT}")
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [email], msg.as_string())
        logger.info(f"Письмо с кодом успешно отправлено на {email}")
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки письма: {e}")
        return False

# === ЛИМИТЫ (БД) ===
DAILY_VOICE_LIMIT = 20
DAILY_UPLOAD_LIMIT = 10

def get_today():
    return datetime.utcnow().date()

def reset_limits_if_needed(user_limit: UserLimit):
    today = get_today()
    if user_limit.last_reset.date() != today:
        user_limit.voice_used = 0
        user_limit.upload_used = 0
        user_limit.last_reset = datetime.utcnow()

def check_voice_limit(user_id: int, db: Session):
    limit = db.query(UserLimit).filter(UserLimit.user_id == user_id).first()
    if not limit:
        limit = UserLimit(user_id=user_id)
        db.add(limit)
        db.commit()
        db.refresh(limit)
    reset_limits_if_needed(limit)
    return limit.voice_used < DAILY_VOICE_LIMIT, limit.voice_used, DAILY_VOICE_LIMIT

def increment_voice_usage(user_id: int, db: Session):
    limit = db.query(UserLimit).filter(UserLimit.user_id == user_id).first()
    if not limit:
        limit = UserLimit(user_id=user_id)
        db.add(limit)
    reset_limits_if_needed(limit)
    limit.voice_used += 1
    db.commit()

def check_upload_limit(user_id: int, db: Session):
    limit = db.query(UserLimit).filter(UserLimit.user_id == user_id).first()
    if not limit:
        limit = UserLimit(user_id=user_id)
        db.add(limit)
        db.commit()
        db.refresh(limit)
    reset_limits_if_needed(limit)
    return limit.upload_used < DAILY_UPLOAD_LIMIT, limit.upload_used, DAILY_UPLOAD_LIMIT

def increment_upload_usage(user_id: int, db: Session):
    limit = db.query(UserLimit).filter(UserLimit.user_id == user_id).first()
    if not limit:
        limit = UserLimit(user_id=user_id)
        db.add(limit)
    reset_limits_if_needed(limit)
    limit.upload_used += 1
    db.commit()

# === ПРОМПТЫ ===
PSYCHOLOGIST_PROMPT = """Ты — профессиональный психолог с 20-летним стажем. Твоё имя — "Вероника".
Обращайся к пользователю по имени, если оно известно.
Говори просто, с эмпатией и лёгким юмором. Будь мягкой, безоценочной.
Никогда не ставь диагнозы. В конце сессии дай лёгкое задание.
В конце каждого ответа добавь оценку настроения: [Оценка настроения: X/10]
Отвечай на русском языке."""

TUTOR_PROMPT = """Ты — профессиональный репетитор с 15-летним стажем. Твоё имя — "Вероника".
Ты помогаешь школьникам и студентам по всем предметам: математика, физика, химия, биология, история, литература, английский, программирование и другим.
Твоя задача — объяснять сложные вещи простым и разговорным языком, использовать примеры из жизни, задавать наводящие вопросы.
Если нужно решить задачу — дай пошаговый алгоритм. Если нужно написать реферат или диплом — помоги со структурой, идеями и аргументацией.
Ты должен быть терпеливым, поддерживающим и вдохновляющим. Отвечай на русском языке. НЕ ставь оценку настроения."""

def call_yandex_gpt(text: str, mode: str, user_name: str = "", context: str = "") -> str:
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    prompt = PSYCHOLOGIST_PROMPT if mode == "psychologist" else TUTOR_PROMPT
    name_prefix = f"Обращайся к пользователю по имени: {user_name}.\n" if user_name else ""
    full_text = f"{prompt}\n{name_prefix}{context}\nПользователь: {text}\n\nВероника:"
    data = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {"stream": False, "temperature": 0.7, "maxTokens": 1000},
        "messages": [{"role": "user", "text": full_text}]
    }
    try:
        response = requests.post(url, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            return response.json()["result"]["alternatives"][0]["message"]["text"]
        return f"Ошибка GPT: {response.status_code}"
    except Exception as e:
        return f"Ошибка: {str(e)}"

def recognize_speech(audio_bytes: bytes, filename: str) -> str:
    url = f"https://stt.api.cloud.yandex.net/speech/v1/stt:recognize?folderId={YANDEX_FOLDER_ID}&lang=ru-RU"
    content_type = "audio/ogg;codecs=opus"
    if filename.endswith('.wav'): content_type = "audio/wav"
    elif filename.endswith('.mp3'): content_type = "audio/mpeg"
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": content_type}
    try:
        response = requests.post(url, headers=headers, data=audio_bytes, timeout=30)
        if response.status_code == 200:
            return response.json().get("result", "")
        return f"Ошибка SpeechKit: {response.status_code}"
    except Exception as e:
        return f"Ошибка: {str(e)}"

def synthesize_speech(text: str) -> bytes:
    url = "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize"
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}"}
    data = {"text": text, "lang": "ru-RU", "voice": "oksana", "emotion": "good", "speed": 1.0, "format": "mp3"}
    try:
        response = requests.post(url, headers=headers, data=data, timeout=30)
        if response.status_code == 200:
            return response.content
        return None
    except Exception as e:
        return None

def extract_mood_score(text: str) -> int:
    match = re.search(r'\[Оценка настроения:\s*(\d+)\s*/10\]', text)
    if match:
        return min(10, max(1, int(match.group(1))))
    return None

# === ЭНДПОИНТЫ АУТЕНТИФИКАЦИИ ===

@app.post("/auth/register")
async def register(user_data: UserCreate, db = Depends(get_db)):
    try:
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', user_data.email):
            raise HTTPException(status_code=400, detail="Неверный формат email")
        existing = db.query(User).filter(User.email == user_data.email).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email уже зарегистрирован")
        
        hashed = get_password_hash(user_data.password)
        user = User(
            email=user_data.email,
            password_hash=hashed,
            name=user_data.name,
            psychologist_subscription="trial",
            psychologist_end=datetime.utcnow() + timedelta(days=3),
            tutor_subscription="trial",
            tutor_end=datetime.utcnow() + timedelta(days=3),
            is_email_verified=False
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"Пользователь создан с id={user.id}")

        code = str(random.randint(100000, 999999))
        user.verification_code = code
        user.verification_code_expires = datetime.utcnow() + timedelta(minutes=10)
        db.commit()

        send_verification_email(user.email, code)
        return {"message": "Код подтверждения отправлен на почту", "email": user.email}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при регистрации: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка: {str(e)}")

@app.post("/auth/verify-email")
async def verify_email(email: str = Form(...), code: str = Form(...), db = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(404, "Пользователь не найден")
    if user.is_email_verified:
        raise HTTPException(400, "Email уже подтверждён")
    if user.verification_code != code:
        raise HTTPException(400, "Неверный код")
    if user.verification_code_expires < datetime.utcnow():
        raise HTTPException(400, "Код истёк")
    user.is_email_verified = True
    user.verification_code = None
    user.verification_code_expires = None
    db.commit()
    access_token = create_access_token(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/auth/login", response_model=Token)
async def login(login: str = Form(...), password: str = Form(...), db = Depends(get_db)):
    try:
        user = db.query(User).filter(User.email == login).first()
        if not user:
            raise HTTPException(status_code=401, detail="Неверные учётные данные")
        if not user.password_hash:
            raise HTTPException(status_code=401, detail="Для этого аккаунта не установлен пароль (возможно, вход через ВК)")
        if not user.is_email_verified:
            raise HTTPException(status_code=401, detail="Email не подтверждён. Проверьте почту.")
        if not verify_password(password, user.password_hash):
            raise HTTPException(status_code=401, detail="Неверные учётные данные")
        access_token = create_access_token(data={"sub": str(user.id)})
        return {"access_token": access_token, "token_type": "bearer"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при входе: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка: {str(e)}")

@app.post("/auth/vk")
async def auth_vk(vk_id: str = Form(...), db = Depends(get_db)):
    user = get_or_create_vk_user(vk_id, db)
    access_token = create_access_token(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/auth/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    session_count = db.query(func.count(Session.id)).filter(Session.user_id == current_user.id).scalar() or 0
    avg_mood = db.query(func.avg(Session.mood_score)).filter(Session.user_id == current_user.id).scalar()
    return UserOut(
        id=current_user.id,
        email=current_user.email,
        phone=current_user.phone,
        name=current_user.name,
        psychologist_subscription=current_user.psychologist_subscription,
        psychologist_end=current_user.psychologist_end,
        tutor_subscription=current_user.tutor_subscription,
        tutor_end=current_user.tutor_end,
        tutor_minutes_balance=current_user.tutor_minutes_balance,
        has_seen_welcome=current_user.has_seen_welcome,
        voice_responses_enabled=current_user.voice_responses_enabled,
        partner_code=current_user.partner_code,
        partner_id=current_user.partner_id,
        session_count=session_count,
        avg_mood=round(avg_mood, 1) if avg_mood else None,
        has_access=has_psychologist_access(current_user) or has_tutor_access(current_user)
    )

# === ОСНОВНЫЕ ЭНДПОИНТЫ ===

@app.get("/")
def root():
    return {"status": "ok", "message": "Самопознание API работает!"}

@app.get("/profile", response_model=UserOut)
async def get_profile(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    session_count = db.query(func.count(Session.id)).filter(Session.user_id == current_user.id).scalar() or 0
    avg_mood = db.query(func.avg(Session.mood_score)).filter(Session.user_id == current_user.id).scalar()
    return UserOut(
        id=current_user.id,
        email=current_user.email,
        phone=current_user.phone,
        name=current_user.name,
        psychologist_subscription=current_user.psychologist_subscription,
        psychologist_end=current_user.psychologist_end,
        tutor_subscription=current_user.tutor_subscription,
        tutor_end=current_user.tutor_end,
        tutor_minutes_balance=current_user.tutor_minutes_balance,
        has_seen_welcome=current_user.has_seen_welcome,
        voice_responses_enabled=current_user.voice_responses_enabled,
        partner_code=current_user.partner_code,
        partner_id=current_user.partner_id,
        session_count=session_count,
        avg_mood=round(avg_mood, 1) if avg_mood else None,
        has_access=has_psychologist_access(current_user) or has_tutor_access(current_user)
    )

@app.get("/limits")
async def get_limits(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _, voice_used, voice_limit = check_voice_limit(current_user.id, db)
    _, upload_used, upload_limit = check_upload_limit(current_user.id, db)
    return {
        "voice_used": voice_used,
        "voice_limit": voice_limit,
        "upload_used": upload_used,
        "upload_limit": upload_limit,
        "tutor_minutes": current_user.tutor_minutes_balance
    }

@app.get("/emotions")
async def get_emotions(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    emotions = db.query(Emotion).filter(Emotion.user_id == current_user.id).order_by(Emotion.created_at).all()
    return {"emotions": [{"date": e.created_at.isoformat(), "score": e.score} for e in emotions]}

@app.post("/welcome/accept")
async def accept_welcome(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    current_user.has_seen_welcome = True
    db.commit()
    return {"status": "ok"}

@app.get("/tariffs")
async def get_tariffs():
    return {"tariffs": {
        "psychologist_month": {"name": "Психолог (месяц)", "price": 399, "period": "30 дней"},
        "psychologist_year": {"name": "Психолог (год)", "price": 3350, "period": "365 дней"},
        "tutor_week": {"name": "Наставник (неделя)", "price": 199, "period": "7 дней"},
        "tutor_month": {"name": "Наставник (месяц)", "price": 599, "period": "30 дней"},
        "tutor_year": {"name": "Наставник (год)", "price": 4990, "period": "365 дней"}
    }}

@app.post("/subscribe/psychologist")
async def subscribe_psychologist(plan: str = Form(...), current_user: User = Depends(get_current_user), db = Depends(get_db)):
    current_user.psychologist_subscription = "premium"
    days = 30 if "month" in plan else 365
    current_user.psychologist_end = datetime.utcnow() + timedelta(days=days)
    db.commit()
    return {"subscription": "premium", "end": current_user.psychologist_end.isoformat()}

@app.post("/subscribe/tutor")
async def subscribe_tutor(plan: str = Form(...), current_user: User = Depends(get_current_user), db = Depends(get_db)):
    current_user.tutor_subscription = "premium"
    days = 7 if "week" in plan else 30 if "month" in plan else 365
    current_user.tutor_end = datetime.utcnow() + timedelta(days=days)
    db.commit()
    return {"subscription": "premium", "end": current_user.tutor_end.isoformat()}

MINUTE_PACKAGES = {
    "60": {"price": 299, "minutes": 60},
    "300": {"price": 1190, "minutes": 300},
    "600": {"price": 1990, "minutes": 600}
}

@app.post("/buy_minutes")
async def buy_minutes(package: str = Form(...), current_user: User = Depends(get_current_user), db = Depends(get_db)):
    if package not in MINUTE_PACKAGES:
        raise HTTPException(status_code=400, detail="Неверный пакет")
    current_user.tutor_minutes_balance += MINUTE_PACKAGES[package]["minutes"]
    db.commit()
    return {"balance": current_user.tutor_minutes_balance}

@app.post("/settings/update")
async def update_settings(
    name: str = Form(None),
    voice_responses: bool = Form(None),
    current_user: User = Depends(get_current_user),
    db = Depends(get_db)
):
    if name:
        current_user.name = name
    if voice_responses is not None:
        current_user.voice_responses_enabled = voice_responses
    db.commit()
    return {"status": "ok"}

@app.post("/settings/delete_all")
async def delete_all_data(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    db.query(Session).filter(Session.user_id == current_user.id).delete()
    db.query(Emotion).filter(Emotion.user_id == current_user.id).delete()
    db.query(Note).filter(Note.user_id == current_user.id).delete()
    db.query(Mark).filter(Mark.user_id == current_user.id).delete()
    db.commit()
    return {"status": "ok"}

@app.post("/support/send")
async def send_support_message(text: str = Form(...), current_user: User = Depends(get_current_user), db = Depends(get_db)):
    msg = SupportMessage(user_id=current_user.id, message=text, is_from_user=True)
    db.add(msg)
    db.commit()
    return {"status": "ok"}

@app.get("/support/messages")
async def get_support_messages(db = Depends(get_db)):
    messages = db.query(SupportMessage).order_by(SupportMessage.created_at).all()
    return {"messages": [{"text": m.message, "date": m.created_at.isoformat(), "is_from_user": m.is_from_user, "reply": m.reply} for m in messages]}

@app.post("/reset_trial")
async def reset_trial(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    current_user.psychologist_subscription = "trial"
    current_user.psychologist_end = datetime.utcnow() + timedelta(days=3)
    current_user.tutor_subscription = "trial"
    current_user.tutor_end = datetime.utcnow() + timedelta(days=3)
    db.commit()
    return {"status": "ok"}

# === ИСТОРИЯ ПСИХОЛОГА (исправленная через сырой SQL) ===
@app.get("/history")
async def get_psychologist_history(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    try:
        sessions = db.execute(
            text("""
                SELECT id, text, response, mood_score, created_at
                FROM sessions
                WHERE user_id = :user_id AND mode = 'psychologist'
                ORDER BY created_at DESC
            """),
            {"user_id": current_user.id}
        ).fetchall()
        return {
            "sessions": [
                {
                    "id": s.id,
                    "text": s.text,
                    "response": s.response,
                    "mood_score": s.mood_score,
                    "date": s.created_at.isoformat()
                }
                for s in sessions
            ]
        }
    except Exception as e:
        logger.error(f"Ошибка в /history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# === ПСИХОЛОГ ===
@app.post("/psychologist/chat")
async def psychologist_chat(text: str = Form(...), current_user: User = Depends(get_current_user), db = Depends(get_db)):
    if not has_psychologist_access(current_user):
        return {"error": "Доступ к психологу ограничен. Оформите подписку.", "requires_payment": True}
    
    response_text = call_yandex_gpt(text, "psychologist", current_user.name)
    mood_score = extract_mood_score(response_text)
    if mood_score:
        response_text = re.sub(r'\s*\[Оценка настроения:\s*\d+\s*/10\]\s*', '', response_text).strip()
    
    session = Session(user_id=current_user.id, mode="psychologist", text=text, response=response_text, mood_score=mood_score)
    db.add(session)
    db.commit()
    
    audio_data = None
    if current_user.voice_responses_enabled:
        audio_data = synthesize_speech(response_text)
        if audio_data:
            increment_voice_usage(current_user.id, db)
    
    _, voice_used, voice_limit = check_voice_limit(current_user.id, db)
    _, upload_used, upload_limit = check_upload_limit(current_user.id, db)
    
    return {
        "response": response_text,
        "audio": audio_data.hex() if audio_data else None,
        "limits": {
            "voice_used": voice_used,
            "voice_limit": voice_limit,
            "upload_used": upload_used,
            "upload_limit": upload_limit
        }
    }

# === НАСТАВНИК ===
@app.post("/tutor/chat")
async def tutor_chat(
    text: str = Form(...),
    subject: str = Form(None),
    is_live: bool = Form(False),
    current_user: User = Depends(get_current_user),
    db = Depends(get_db)
):
    if not has_tutor_access(current_user):
        return {"error": "Доступ к наставнику ограничен. Оформите подписку.", "requires_payment": True}
    
    if is_live:
        if current_user.tutor_minutes_balance <= 0:
            return {"error": "Недостаточно минут для лекции. Купите пакет минут.", "requires_minutes": True}
        current_user.tutor_minutes_balance -= 1
        db.commit()
    
    context = f"Предмет: {subject or 'Общий'}\n" if subject else ""
    response_text = call_yandex_gpt(text, "tutor", current_user.name, context)
    
    session = Session(
        user_id=current_user.id,
        mode="tutor",
        text=text,
        response=response_text,
        is_live=is_live,
        subject=subject
    )
    db.add(session)
    db.commit()
    
    audio_data = None
    if current_user.voice_responses_enabled:
        audio_data = synthesize_speech(response_text)
        if audio_data:
            increment_voice_usage(current_user.id, db)
    
    return {
        "response": response_text,
        "audio": audio_data.hex() if audio_data else None,
        "minutes_balance": current_user.tutor_minutes_balance
    }

@app.post("/tutor/voice")
async def tutor_voice(
    audio: UploadFile = File(...),
    subject: str = Form(None),
    is_live: bool = Form(False),
    current_user: User = Depends(get_current_user),
    db = Depends(get_db)
):
    if not has_tutor_access(current_user):
        return {"error": "Доступ к наставнику ограничен. Оформите подписку.", "requires_payment": True}
    
    is_allowed, used, limit = check_voice_limit(current_user.id, db)
    if not is_allowed:
        return {
            "error": f"Вы исчерпали дневной лимит голосовых сессий ({limit}/{limit}).",
            "requires_payment": True,
            "limits": {"voice_used": used, "voice_limit": limit}
        }
    
    audio_bytes = await audio.read()
    if len(audio_bytes) > 10 * 1024 * 1024:
        return {"error": "Файл слишком большой. Максимум 10 МБ."}
    
    recognized_text = recognize_speech(audio_bytes, audio.filename)
    if recognized_text.startswith("Ошибка") or not recognized_text:
        return {"error": "Не удалось распознать речь."}
    
    if is_live:
        if current_user.tutor_minutes_balance <= 0:
            return {"error": "Недостаточно минут для лекции. Купите пакет минут.", "requires_minutes": True}
        current_user.tutor_minutes_balance -= 1
        db.commit()
    
    context = f"Предмет: {subject or 'Общий'}\n" if subject else ""
    response_text = call_yandex_gpt(recognized_text, "tutor", current_user.name, context)
    
    session = Session(
        user_id=current_user.id,
        mode="tutor",
        text=recognized_text,
        response=response_text,
        is_live=is_live,
        subject=subject
    )
    db.add(session)
    db.commit()
    
    increment_voice_usage(current_user.id, db)
    
    audio_data = None
    if current_user.voice_responses_enabled:
        audio_data = synthesize_speech(response_text)
    
    return {
        "recognized_text": recognized_text,
        "response": response_text,
        "audio": audio_data.hex() if audio_data else None,
        "minutes_balance": current_user.tutor_minutes_balance
    }

@app.post("/tutor/upload")
async def tutor_upload(
    audio: UploadFile = File(...),
    subject: str = Form(None),
    current_user: User = Depends(get_current_user),
    db = Depends(get_db)
):
    if not has_tutor_access(current_user):
        return {"error": "Доступ к наставнику ограничен. Оформите подписку.", "requires_payment": True}
    
    is_allowed, used, limit = check_upload_limit(current_user.id, db)
    if not is_allowed:
        return {
            "error": f"Вы исчерпали дневной лимит загрузок аудио ({limit}/{limit}).",
            "requires_payment": True,
            "limits": {"upload_used": used, "upload_limit": limit}
        }
    
    audio_bytes = await audio.read()
    if len(audio_bytes) > 10 * 1024 * 1024:
        return {"error": "Файл слишком большой. Максимум 10 МБ."}
    
    recognized_text = recognize_speech(audio_bytes, audio.filename)
    if recognized_text.startswith("Ошибка") or not recognized_text:
        return {"error": "Не удалось распознать речь."}
    
    context = f"Предмет: {subject or 'Общий'}\n" if subject else ""
    response_text = call_yandex_gpt(recognized_text, "tutor", current_user.name, context)
    
    session = Session(
        user_id=current_user.id,
        mode="tutor",
        text=recognized_text,
        response=response_text,
        subject=subject
    )
    db.add(session)
    db.commit()
    
    increment_upload_usage(current_user.id, db)
    
    audio_data = None
    if current_user.voice_responses_enabled:
        audio_data = synthesize_speech(response_text)
        if audio_data:
            increment_voice_usage(current_user.id, db)
    
    return {
        "recognized_text": recognized_text,
        "response": response_text,
        "audio": audio_data.hex() if audio_data else None
    }

@app.get("/tutor/history")
async def get_tutor_history(
    current_user: User = Depends(get_current_user),
    subject: str = None,
    date_from: str = None,
    date_to: str = None,
    is_live: bool = None,
    db = Depends(get_db)
):
    query = db.query(Session).filter(Session.user_id == current_user.id, Session.mode == "tutor")
    if subject:
        query = query.filter(Session.subject == subject)
    if date_from:
        query = query.filter(Session.created_at >= datetime.fromisoformat(date_from))
    if date_to:
        query = query.filter(Session.created_at <= datetime.fromisoformat(date_to))
    if is_live is not None:
        query = query.filter(Session.is_live == is_live)
    sessions = query.order_by(desc(Session.created_at)).all()
    return {
        "sessions": [
            {
                "id": s.id,
                "text": s.text,
                "response": s.response,
                "subject": s.subject,
                "is_live": s.is_live,
                "date": s.created_at.isoformat()
            }
            for s in sessions
        ]
    }

@app.post("/tutor/notes")
async def create_note(
    session_id: int = Form(...),
    text: str = Form(...),
    selection: str = Form(None),
    color: str = Form("yellow"),
    current_user: User = Depends(get_current_user),
    db = Depends(get_db)
):
    note = Note(
        user_id=current_user.id,
        session_id=session_id,
        text=text,
        selection=selection,
        color=color
    )
    db.add(note)
    db.commit()
    return {"status": "ok", "note_id": note.id}

@app.get("/tutor/notes")
async def get_notes(
    session_id: int = None,
    current_user: User = Depends(get_current_user),
    db = Depends(get_db)
):
    query = db.query(Note).filter(Note.user_id == current_user.id)
    if session_id:
        query = query.filter(Note.session_id == session_id)
    notes = query.order_by(desc(Note.created_at)).all()
    return {
        "notes": [
            {
                "id": n.id,
                "text": n.text,
                "selection": n.selection,
                "color": n.color,
                "date": n.created_at.isoformat()
            }
            for n in notes
        ]
    }

@app.post("/tutor/marks")
async def create_mark(
    session_id: int = Form(...),
    color: str = Form("blue"),
    label: str = Form(None),
    current_user: User = Depends(get_current_user),
    db = Depends(get_db)
):
    mark = Mark(
        user_id=current_user.id,
        session_id=session_id,
        color=color,
        label=label
    )
    db.add(mark)
    db.commit()
    return {"status": "ok", "mark_id": mark.id}

@app.get("/tutor/marks")
async def get_marks(
    session_id: int = None,
    current_user: User = Depends(get_current_user),
    db = Depends(get_db)
):
    query = db.query(Mark).filter(Mark.user_id == current_user.id)
    if session_id:
        query = query.filter(Mark.session_id == session_id)
    marks = query.order_by(desc(Mark.created_at)).all()
    return {
        "marks": [
            {
                "id": m.id,
                "color": m.color,
                "label": m.label,
                "date": m.created_at.isoformat()
            }
            for m in marks
        ]
    }

@app.post("/tutor/grammar")
async def tutor_grammar(
    text: str = Form(...),
    current_user: User = Depends(get_current_user),
    db = Depends(get_db)
):
    if not has_tutor_access(current_user):
        return {"error": "Доступ к наставнику ограничен. Оформите подписку.", "requires_payment": True}
    
    grammar_prompt = f"""
Ты — преподаватель русского языка и литературы. Твоя задача — помочь пользователю улучшить грамотность.
Проверь следующий текст на ошибки: орфографические, пунктуационные, стилистические.
Выдели ошибки, покажи правильный вариант и кратко объясни правило.
Если текст написан правильно — похвали пользователя и предложи усложнить задание.

Текст пользователя:
{text}
"""
    response_text = call_yandex_gpt(grammar_prompt, "tutor", current_user.name)
    return {"response": response_text}

# === ПАРЫ ===
@app.post("/pair/invite")
async def create_pair_code(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    code = str(uuid.uuid4())[:8].upper()
    current_user.partner_code = code
    db.commit()
    return {"code": code}

@app.post("/pair/connect")
async def connect_pair(code: str = Form(...), current_user: User = Depends(get_current_user), db = Depends(get_db)):
    partner = db.query(User).filter(User.partner_code == code).first()
    if not partner:
        raise HTTPException(status_code=404, detail="Партнёр не найден")
    if partner.id == current_user.id:
        raise HTTPException(status_code=400, detail="Нельзя подключиться к себе")
    current_user.partner_id = partner.id
    db.commit()
    return {"status": "connected", "partner_name": partner.name}

@app.post("/pair/disconnect")
async def disconnect_pair(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    current_user.partner_id = None
    current_user.partner_code = None
    db.commit()
    return {"status": "disconnected"}

@app.post("/pair/session")
async def pair_session(text: str = Form(...), current_user: User = Depends(get_current_user), db = Depends(get_db)):
    if not has_psychologist_access(current_user):
        return {"error": "Доступ к паре ограничен. Оформите подписку.", "requires_payment": True}
    if not current_user.partner_id:
        raise HTTPException(status_code=400, detail="Нет связанного партнёра")
    partner = db.query(User).filter(User.id == current_user.partner_id).first()
    if not partner:
        raise HTTPException(status_code=404, detail="Партнёр не найден")
    response_text = call_yandex_gpt(
        f"Помоги паре. Партнёр 1 ({current_user.name}): {text}. Партнёр 2: {partner.name}",
        "psychologist",
        ""
    )
    session = Session(user_id=current_user.id, mode="psychologist", text=text, response=response_text, is_pair_session=True)
    db.add(session)
    db.commit()
    return {"response": response_text}

@app.get("/pair/history")
async def get_pair_history(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    sessions = db.query(Session).filter(
        Session.user_id == current_user.id,
        Session.is_pair_session == True
    ).order_by(desc(Session.created_at)).all()
    return {"sessions": [{"text": s.text, "response": s.response, "date": s.created_at.isoformat()} for s in sessions]}

@app.get("/pair/dynamics")
async def get_pair_dynamics(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    if not current_user.partner_id:
        return {"error": "Нет связанного партнёра"}
    user_emotions = db.query(Emotion).filter(Emotion.user_id == current_user.id).all()
    partner_emotions = db.query(Emotion).filter(Emotion.user_id == current_user.partner_id).all()
    return {
        "user": [{"date": e.created_at.isoformat(), "score": e.score} for e in user_emotions],
        "partner": [{"date": e.created_at.isoformat(), "score": e.score} for e in partner_emotions]
    }

@app.post("/pair/task")
async def create_pair_task(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    if not current_user.partner_id:
        return {"error": "Нет связанного партнёра"}
    partner = db.query(User).filter(User.id == current_user.partner_id).first()
    if not partner:
        return {"error": "Партнёр не найден"}
    task_text = call_yandex_gpt(
        f"Придумай короткое задание для пары (партнёры: {current_user.name} и {partner.name}).",
        "psychologist",
        ""
    )
    return {"task": task_text}

# === АДМИНКА ===
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "haginu92")

def admin_required(password: str):
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=403, detail="Неверный пароль")

@app.get("/admin/stats")
async def admin_stats(password: str, db: Session = Depends(get_db)):
    admin_required(password)
    return {
        "users": db.query(User).count(),
        "sessions": db.query(Session).count(),
        "payments": db.query(Payment).count(),
        "active_today": db.query(User).filter(User.created_at >= datetime.utcnow() - timedelta(days=1)).count()
    }

@app.get("/admin/users")
async def admin_users(password: str, search: str = "", db: Session = Depends(get_db)):
    admin_required(password)
    query = db.query(User)
    if search:
        query = query.filter((User.email.ilike(f"%{search}%")) | (User.name.ilike(f"%{search}%")))
    return [{"id": u.id, "email": u.email, "name": u.name, "subscription": u.psychologist_subscription, "end": u.psychologist_end} for u in query.all()]

@app.post("/admin/give_premium")
async def give_premium(
    user_id: int = Form(...),
    days: int = Form(30),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    admin_required(password)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Пользователь не найден")
    user.psychologist_subscription = "premium"
    user.tutor_subscription = "premium"
    if user.psychologist_end and user.psychologist_end > datetime.utcnow():
        user.psychologist_end += timedelta(days=days)
    else:
        user.psychologist_end = datetime.utcnow() + timedelta(days=days)
    if user.tutor_end and user.tutor_end > datetime.utcnow():
        user.tutor_end += timedelta(days=days)
    else:
        user.tutor_end = datetime.utcnow() + timedelta(days=days)
    db.commit()
    return {"status": "ok", "new_end": user.psychologist_end.isoformat()}

@app.get("/admin/tips")
async def get_tips(password: str, db: Session = Depends(get_db)):
    admin_required(password)
    return [{"id": t.id, "text": t.text, "is_active": t.is_active} for t in db.query(CarouselTip).order_by(CarouselTip.order).all()]

@app.post("/admin/tips/add")
async def add_tip(text: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    admin_required(password)
    tip = CarouselTip(text=text)
    db.add(tip)
    db.commit()
    return {"status": "ok"}

@app.post("/admin/tips/update")
async def update_tip(
    tip_id: int = Form(...),
    text: str = Form(...),
    is_active: bool = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    admin_required(password)
    tip = db.query(CarouselTip).filter(CarouselTip.id == tip_id).first()
    if not tip:
        raise HTTPException(404, "Подсказка не найдена")
    tip.text = text
    tip.is_active = is_active
    db.commit()
    return {"status": "ok"}

@app.post("/admin/tips/delete")
async def delete_tip(tip_id: int = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    admin_required(password)
    tip = db.query(CarouselTip).filter(CarouselTip.id == tip_id).first()
    if tip:
        db.delete(tip)
        db.commit()
    return {"status": "ok"}

@app.get("/admin/maintenance")
async def get_maintenance(password: str, db: Session = Depends(get_db)):
    admin_required(password)
    setting = db.query(SystemSetting).filter(SystemSetting.key == "maintenance_mode").first()
    return {"enabled": setting.value == "true" if setting else False}

@app.post("/admin/maintenance")
async def set_maintenance(enabled: bool = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    admin_required(password)
    setting = db.query(SystemSetting).filter(SystemSetting.key == "maintenance_mode").first()
    if not setting:
        setting = SystemSetting(key="maintenance_mode", value=str(enabled).lower())
        db.add(setting)
    else:
        setting.value = str(enabled).lower()
    db.commit()
    return {"status": "ok"}

@app.get("/admin/prices")
async def get_prices(password: str, db: Session = Depends(get_db)):
    admin_required(password)
    month = db.query(SystemSetting).filter(SystemSetting.key == "price_psychologist_month").first()
    year = db.query(SystemSetting).filter(SystemSetting.key == "price_psychologist_year").first()
    tutor_week = db.query(SystemSetting).filter(SystemSetting.key == "price_tutor_week").first()
    tutor_month = db.query(SystemSetting).filter(SystemSetting.key == "price_tutor_month").first()
    tutor_year = db.query(SystemSetting).filter(SystemSetting.key == "price_tutor_year").first()
    return {
        "psychologist_month": int(month.value) if month else 399,
        "psychologist_year": int(year.value) if year else 3350,
        "tutor_week": int(tutor_week.value) if tutor_week else 199,
        "tutor_month": int(tutor_month.value) if tutor_month else 599,
        "tutor_year": int(tutor_year.value) if tutor_year else 4990
    }

@app.post("/admin/prices")
async def set_prices(
    password: str = Form(...),
    psychologist_month: int = Form(...),
    psychologist_year: int = Form(...),
    tutor_week: int = Form(...),
    tutor_month: int = Form(...),
    tutor_year: int = Form(...),
    db: Session = Depends(get_db)
):
    admin_required(password)
    for key, value in [
        ("price_psychologist_month", psychologist_month),
        ("price_psychologist_year", psychologist_year),
        ("price_tutor_week", tutor_week),
        ("price_tutor_month", tutor_month),
        ("price_tutor_year", tutor_year)
    ]:
        setting = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if not setting:
            setting = SystemSetting(key=key, value=str(value))
            db.add(setting)
        else:
            setting.value = str(value)
    db.commit()
    return {"status": "ok"}

@app.get("/admin/logs")
async def get_logs(password: str, limit: int = 100, db: Session = Depends(get_db)):
    admin_required(password)
    logs = db.query(UserLog).order_by(UserLog.created_at.desc()).limit(limit).all()
    return [{
        "user_id": l.user_id,
        "action": l.action,
        "details": l.details,
        "time": l.created_at.isoformat()
    } for l in logs]

@app.post("/admin/support/reply")
async def admin_reply(
    message_id: int = Form(...),
    reply_text: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    admin_required(password)
    original = db.query(SupportMessage).filter(SupportMessage.id == message_id).first()
    if not original:
        raise HTTPException(404, "Сообщение не найдено")
    original.reply = reply_text
    db.commit()
    return {"status": "ok", "reply": reply_text}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)