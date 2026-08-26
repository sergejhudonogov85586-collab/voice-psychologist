from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, Float
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    vk_id = Column(String(50), unique=True, index=True, nullable=True)
    email = Column(String(255), unique=True, index=True, nullable=True)
    phone = Column(String(20), unique=True, index=True, nullable=True)
    password_hash = Column(String(255), nullable=True)
    name = Column(String(255), default="Пользователь")
    psychologist_subscription = Column(String(50), default="trial")
    psychologist_end = Column(DateTime, nullable=True)
    has_seen_welcome = Column(Boolean, default=False)
    partner_code = Column(String(20), nullable=True)
    partner_id = Column(Integer, nullable=True)
    voice_responses_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Поля для подтверждения email
    is_email_verified = Column(Boolean, default=False)
    verification_code = Column(String(6), nullable=True)
    verification_code_expires = Column(DateTime, nullable=True)

class SessionModel(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    text = Column(Text, nullable=True)
    response = Column(Text, nullable=True)
    mood_score = Column(Integer, nullable=True)
    is_pair_session = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Emotion(Base):
    __tablename__ = "emotions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    score = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

class SupportMessage(Base):
    __tablename__ = "support_messages"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    message = Column(Text)
    is_from_user = Column(Boolean, default=True)
    reply = Column(Text, nullable=True)   # ответ администратора
    created_at = Column(DateTime, default=datetime.utcnow)
    is_read = Column(Boolean, default=False)

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    amount = Column(Float)
    plan = Column(String(50))
    status = Column(String(50), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)

class CarouselTip(Base):
    __tablename__ = "carousel_tips"
    id = Column(Integer, primary_key=True, index=True)
    text = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    order = Column(Integer, default=0)

class SystemSetting(Base):
    __tablename__ = "system_settings"
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserLog(Base):
    __tablename__ = "user_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    action = Column(String(255))
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)