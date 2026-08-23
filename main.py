import os
import re
import uuid
import hashlib
import secrets
import logging
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import desc, func
from dotenv import load_dotenv
from jose import JWTError, jwt
from pydantic import BaseModel
from database import get_db, init_db
from models import User, SessionModel, Emotion, SupportMessage

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI(title="Самопознание - Голосовой психолог")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()

# === Аутентификация ===
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkeychangeinproduction")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

# === Хеширование пароля (через hashlib) ===
def hash_password(password: str) -> str:
    # Используем соль из 16 случайных байт
    salt = secrets.token_hex(16)
    # Хешируем пароль с солью
    hashed = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
    # Возвращаем соль + хеш
    return f"{salt}:{hashed}"

def verify_password(password: str, hashed_password: str) -> bool:
    try:
        salt, stored_hash = hashed_password.split(':')
        # Вычисляем хеш для введённого пароля с той же солью
        computed_hash = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
        return secrets.compare_digest(computed_hash, stored_hash)
    except Exception:
        return False

# === Pydantic модели ===
class UserCreate(BaseModel):
    email: str | None = None
    phone: str | None = None
    password: str
    name: str = "Пользователь"

class UserLogin(BaseModel):
    login: str
    password: str

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
    has_seen_welcome: bool
    voice_responses_enabled: bool
    partner_code: str | None
    partner_id: int | None
    session_count: int = 0
    avg_mood: float | None = None
    has_access: bool = True

# === Вспомогательные функции ===
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
            psychologist_end=datetime.utcnow() + timedelta(days=3)
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

# === YANDEX API ===
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")

# === ЛИМИТЫ ===
voice_usage = {}
upload_usage = {}
DAILY_VOICE_LIMIT = 20
DAILY_UPLOAD_LIMIT = 10

def get_today():
    return datetime.utcnow().date().isoformat()

def check_voice_limit(user_id: int):
    today = get_today()
    data = voice_usage.get(user_id, {})
    if data.get("date") != today:
        voice_usage[user_id] = {"date": today, "count": 0}
        return True, 0, DAILY_VOICE_LIMIT
    count = data.get("count", 0)
    return count < DAILY_VOICE_LIMIT, count, DAILY_VOICE_LIMIT

def increment_voice_usage(user_id: int):
    today = get_today()
    data = voice_usage.get(user_id, {})
    if data.get("date") != today:
        voice_usage[user_id] = {"date": today, "count": 1}
    else:
        voice_usage[user_id]["count"] = data.get("count", 0) + 1

def check_upload_limit(user_id: int):
    today = get_today()
    data = upload_usage.get(user_id, {})
    if data.get("date") != today:
        upload_usage[user_id] = {"date": today, "count": 0}
        return True, 0, DAILY_UPLOAD_LIMIT
    count = data.get("count", 0)
    return count < DAILY_UPLOAD_LIMIT, count, DAILY_UPLOAD_LIMIT

def increment_upload_usage(user_id: int):
    today = get_today()
    data = upload_usage.get(user_id, {})
    if data.get("date") != today:
        upload_usage[user_id] = {"date": today, "count": 1}
    else:
        upload_usage[user_id]["count"] = data.get("count", 0) + 1

# === ПРОМПТ ===
PSYCHOLOGIST_PROMPT = """Ты — профессиональный психолог с 20-летним стажем. Твоё имя — "Вероника".
Обращайся к пользователю по имени, если оно известно.
Говори просто, с эмпатией и лёгким юмором. Будь мягкой, безоценочной.
Никогда не ставь диагнозы. В конце сессии дай лёгкое задание.
В конце каждого ответа добавь оценку настроения: [Оценка настроения: X/10]
Отвечай на русском языке."""

def call_yandex_gpt(text: str, user_name: str = "") -> str:
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {"Authorization": f"Api-Key {YANDEX_API_KEY}", "Content-Type": "application/json"}
    name_prefix = f"Обращайся к пользователю по имени: {user_name}.\n" if user_name else ""
    full_text = f"{PSYCHOLOGIST_PROMPT}\n{name_prefix}\nПользователь: {text}\n\nВероника:"
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

@app.post("/auth/register", response_model=Token)
async def register(user_data: UserCreate, db = Depends(get_db)):
    try:
        logger.info(f"Регистрация: email={user_data.email}, phone={user_data.phone}")
        # Валидация email
        if user_data.email:
            if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', user_data.email):
                raise HTTPException(status_code=400, detail="Неверный формат email")
            existing = db.query(User).filter(User.email == user_data.email).first()
            if existing:
                raise HTTPException(status_code=400, detail="Email уже зарегистрирован")
        if user_data.phone:
            if not re.match(r'^\+?[0-9]{10,15}$', user_data.phone):
                raise HTTPException(status_code=400, detail="Неверный формат телефона")
            existing = db.query(User).filter(User.phone == user_data.phone).first()
            if existing:
                raise HTTPException(status_code=400, detail="Телефон уже зарегистрирован")
        if not user_data.email and not user_data.phone:
            raise HTTPException(status_code=400, detail="Укажите email или телефон")
        
        # Хеширование пароля
        hashed = hash_password(user_data.password)
        user = User(
            email=user_data.email,
            phone=user_data.phone,
            password_hash=hashed,
            name=user_data.name,
            psychologist_subscription="trial",
            psychologist_end=datetime.utcnow() + timedelta(days=3)
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"Пользователь создан с id={user.id}")
        access_token = create_access_token(data={"sub": str(user.id)})
        return {"access_token": access_token, "token_type": "bearer"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка при регистрации: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка: {str(e)}")

@app.post("/auth/login", response_model=Token)
async def login(
    login: str = Form(...),
    password: str = Form(...),
    db = Depends(get_db)
):
    try:
        logger.info(f"Вход: login={login}")
        user = None
        if "@" in login:
            user = db.query(User).filter(User.email == login).first()
        else:
            user = db.query(User).filter(User.phone == login).first()
        if not user:
            logger.warning(f"Пользователь не найден: {login}")
            raise HTTPException(status_code=401, detail="Неверные учётные данные")
        
        if not user.password_hash:
            logger.warning("У пользователя нет хеша пароля")
            raise HTTPException(status_code=401, detail="Для этого аккаунта не установлен пароль (возможно, вход через ВК)")
        
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
    session_count = db.query(SessionModel).filter(SessionModel.user_id == current_user.id).count()
    avg_mood = db.query(func.avg(SessionModel.mood_score)).filter(SessionModel.user_id == current_user.id).scalar()
    return UserOut(
        id=current_user.id,
        email=current_user.email,
        phone=current_user.phone,
        name=current_user.name,
        psychologist_subscription=current_user.psychologist_subscription,
        psychologist_end=current_user.psychologist_end,
        has_seen_welcome=current_user.has_seen_welcome,
        voice_responses_enabled=current_user.voice_responses_enabled,
        partner_code=current_user.partner_code,
        partner_id=current_user.partner_id,
        session_count=session_count,
        avg_mood=round(avg_mood, 1) if avg_mood else None,
        has_access=has_psychologist_access(current_user)
    )

@app.post("/auth/link")
async def link_account(
    email: str | None = Form(None),
    phone: str | None = Form(None),
    password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db = Depends(get_db)
):
    if not email and not phone:
        raise HTTPException(status_code=400, detail="Укажите email или телефон")
    if email:
        existing = db.query(User).filter(User.email == email).first()
        if existing and existing.id != current_user.id:
            raise HTTPException(status_code=400, detail="Email уже занят")
        current_user.email = email
    if phone:
        existing = db.query(User).filter(User.phone == phone).first()
        if existing and existing.id != current_user.id:
            raise HTTPException(status_code=400, detail="Телефон уже занят")
        current_user.phone = phone
    current_user.password_hash = hash_password(password)
    db.commit()
    return {"status": "linked"}

# === ОСНОВНЫЕ ЭНДПОИНТЫ (защищённые) ===

@app.get("/")
def root():
    return {"status": "ok", "message": "Самопознание API работает!"}

@app.get("/profile", response_model=UserOut)
async def get_profile(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    session_count = db.query(SessionModel).filter(SessionModel.user_id == current_user.id).count()
    avg_mood = db.query(func.avg(SessionModel.mood_score)).filter(SessionModel.user_id == current_user.id).scalar()
    return UserOut(
        id=current_user.id,
        email=current_user.email,
        phone=current_user.phone,
        name=current_user.name,
        psychologist_subscription=current_user.psychologist_subscription,
        psychologist_end=current_user.psychologist_end,
        has_seen_welcome=current_user.has_seen_welcome,
        voice_responses_enabled=current_user.voice_responses_enabled,
        partner_code=current_user.partner_code,
        partner_id=current_user.partner_id,
        session_count=session_count,
        avg_mood=round(avg_mood, 1) if avg_mood else None,
        has_access=has_psychologist_access(current_user)
    )

@app.get("/limits")
async def get_limits(current_user: User = Depends(get_current_user)):
    _, voice_used, voice_limit = check_voice_limit(current_user.id)
    _, upload_used, upload_limit = check_upload_limit(current_user.id)
    return {
        "voice_used": voice_used,
        "voice_limit": voice_limit,
        "upload_used": upload_used,
        "upload_limit": upload_limit
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
        "psychologist_year": {"name": "Психолог (год)", "price": 3350, "period": "365 дней"}
    }}

@app.post("/subscribe/psychologist")
async def subscribe_psychologist(plan: str = Form(...), current_user: User = Depends(get_current_user), db = Depends(get_db)):
    current_user.psychologist_subscription = "premium"
    days = 30 if "month" in plan else 365
    current_user.psychologist_end = datetime.utcnow() + timedelta(days=days)
    db.commit()
    return {"subscription": "premium", "end": current_user.psychologist_end.isoformat()}

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
    db.query(SessionModel).filter(SessionModel.user_id == current_user.id).delete()
    db.query(Emotion).filter(Emotion.user_id == current_user.id).delete()
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
    return {"messages": [{"text": m.message, "date": m.created_at.isoformat(), "is_from_user": m.is_from_user} for m in messages]}

@app.post("/reset_trial")
async def reset_trial(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    current_user.psychologist_subscription = "trial"
    current_user.psychologist_end = datetime.utcnow() + timedelta(days=3)
    db.commit()
    return {"status": "ok"}

@app.post("/chat")
async def chat(text: str = Form(...), current_user: User = Depends(get_current_user), db = Depends(get_db)):
    if not has_psychologist_access(current_user):
        return {"error": "Доступ заблокирован. Оформите подписку.", "requires_payment": True}
    
    response_text = call_yandex_gpt(text, current_user.name)
    mood_score = extract_mood_score(response_text)
    if mood_score:
        response_text = re.sub(r'\s*\[Оценка настроения:\s*\d+\s*/10\]\s*', '', response_text).strip()
    
    session = SessionModel(user_id=current_user.id, text=text, response=response_text, mood_score=mood_score)
    db.add(session)
    db.commit()
    
    audio_data = None
    if current_user.voice_responses_enabled:
        audio_data = synthesize_speech(response_text)
    
    _, voice_used, voice_limit = check_voice_limit(current_user.id)
    _, upload_used, upload_limit = check_upload_limit(current_user.id)
    
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

@app.post("/voice")
async def voice(audio: UploadFile = File(...), current_user: User = Depends(get_current_user), db = Depends(get_db)):
    if not has_psychologist_access(current_user):
        return {"error": "Доступ заблокирован. Оформите подписку.", "requires_payment": True}
    
    is_allowed, used, limit = check_voice_limit(current_user.id)
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
    
    response_text = call_yandex_gpt(recognized_text, current_user.name)
    mood_score = extract_mood_score(response_text)
    if mood_score:
        response_text = re.sub(r'\s*\[Оценка настроения:\s*\d+\s*/10\]\s*', '', response_text).strip()
    
    session = SessionModel(user_id=current_user.id, text=recognized_text, response=response_text, mood_score=mood_score)
    db.add(session)
    db.commit()
    
    increment_voice_usage(current_user.id)
    _, voice_used, voice_limit = check_voice_limit(current_user.id)
    _, upload_used, upload_limit = check_upload_limit(current_user.id)
    
    audio_data = None
    if current_user.voice_responses_enabled:
        audio_data = synthesize_speech(response_text)
    
    return {
        "recognized_text": recognized_text,
        "response": response_text,
        "audio": audio_data.hex() if audio_data else None,
        "limits": {
            "voice_used": voice_used,
            "voice_limit": voice_limit,
            "upload_used": upload_used,
            "upload_limit": upload_limit
        }
    }

@app.post("/upload")
async def upload_audio(audio: UploadFile = File(...), current_user: User = Depends(get_current_user), db = Depends(get_db)):
    if not has_psychologist_access(current_user):
        return {"error": "Доступ заблокирован. Оформите подписку.", "requires_payment": True}
    
    is_allowed, used, limit = check_upload_limit(current_user.id)
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
    
    response_text = call_yandex_gpt(recognized_text, current_user.name)
    mood_score = extract_mood_score(response_text)
    if mood_score:
        response_text = re.sub(r'\s*\[Оценка настроения:\s*\d+\s*/10\]\s*', '', response_text).strip()
    
    session = SessionModel(user_id=current_user.id, text=recognized_text, response=response_text, mood_score=mood_score)
    db.add(session)
    db.commit()
    
    increment_upload_usage(current_user.id)
    _, voice_used, voice_limit = check_voice_limit(current_user.id)
    _, upload_used, upload_limit = check_upload_limit(current_user.id)
    
    audio_data = None
    if current_user.voice_responses_enabled:
        audio_data = synthesize_speech(response_text)
        if audio_data:
            increment_voice_usage(current_user.id)
            _, voice_used, voice_limit = check_voice_limit(current_user.id)
    
    return {
        "recognized_text": recognized_text,
        "response": response_text,
        "audio": audio_data.hex() if audio_data else None,
        "limits": {
            "voice_used": voice_used,
            "voice_limit": voice_limit,
            "upload_used": upload_used,
            "upload_limit": upload_limit
        }
    }

@app.get("/history")
async def get_history(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    sessions = db.query(SessionModel).filter(
        SessionModel.user_id == current_user.id,
        SessionModel.is_pair_session == False
    ).order_by(desc(SessionModel.created_at)).all()
    return {
        "sessions": [
            {
                "text": s.text,
                "response": s.response,
                "mood_score": s.mood_score,
                "date": s.created_at.isoformat()
            }
            for s in sessions
        ]
    }

# === ПАРЫ (защищённые) ===

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
    if current_user.partner_id:
        current_user.partner_id = None
        current_user.partner_code = None
        db.commit()
    return {"status": "disconnected"}

@app.post("/pair/session")
async def pair_session(text: str = Form(...), current_user: User = Depends(get_current_user), db = Depends(get_db)):
    if not has_psychologist_access(current_user):
        return {"error": "Доступ заблокирован.", "requires_payment": True}
    if not current_user.partner_id:
        raise HTTPException(status_code=400, detail="Нет связанного партнёра")
    
    partner = db.query(User).filter(User.id == current_user.partner_id).first()
    if not partner:
        raise HTTPException(status_code=404, detail="Партнёр не найден")
    
    response_text = call_yandex_gpt(
        f"Помоги паре. Партнёр 1 ({current_user.name}): {text}. Партнёр 2: {partner.name}",
        ""
    )
    session = SessionModel(user_id=current_user.id, text=text, response=response_text, is_pair_session=True)
    db.add(session)
    db.commit()
    return {"response": response_text}

@app.get("/pair/history")
async def get_pair_history(current_user: User = Depends(get_current_user), db = Depends(get_db)):
    sessions = db.query(SessionModel).filter(
        SessionModel.user_id == current_user.id,
        SessionModel.is_pair_session == True
    ).order_by(desc(SessionModel.created_at)).all()
    return {
        "sessions": [
            {
                "text": s.text,
                "response": s.response,
                "date": s.created_at.isoformat()
            }
            for s in sessions
        ]
    }

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
    task_text = call_yandex_gpt(f"Придумай короткое задание для пары (партнёры: {current_user.name} и {partner.name}).")
    return {"task": task_text}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)