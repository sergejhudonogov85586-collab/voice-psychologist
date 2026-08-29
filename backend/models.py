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
    tutor_subscription = Column(String(50), default="trial")
    tutor_end = Column(DateTime, nullable=True)
    tutor_minutes_balance = Column(Integer, default=0)
    
    has_seen_welcome = Column(Boolean, default=False)
    partner_code = Column(String(20), nullable=True)
    partner_id = Column(Integer, nullable=True)
    voice_responses_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    is_email_verified = Column(Boolean, default=False)
    verification_code = Column(String(6), nullable=True)
    verification_code_expires = Column(DateTime, nullable=True)

class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    mode = Column(String(20), default="psychologist")
    text = Column(Text, nullable=True)
    response = Column(Text, nullable=True)
    mood_score = Column(Integer, nullable=True)
    is_pair_session = Column(Boolean, default=False)
    is_live = Column(Boolean, default=False)
    subject = Column(String(100), nullable=True)
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
    created_at = Column(DateTime, default=datetime.utcnow)
    is_read = Column(Boolean, default=False)
    reply = Column(Text, nullable=True)

class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    session_id = Column(Integer, index=True)
    text = Column(Text, nullable=False)
    selection = Column(Text, nullable=True)
    color = Column(String(20), default="yellow")
    created_at = Column(DateTime, default=datetime.utcnow)

class Mark(Base):
    __tablename__ = "marks"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    session_id = Column(Integer, index=True)
    color = Column(String(20), default="blue")
    label = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

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

class UserLimit(Base):
    __tablename__ = "user_limits"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, unique=True, index=True, nullable=False)
    voice_used = Column(Integer, default=0)
    upload_used = Column(Integer, default=0)
    last_reset = Column(DateTime, default=datetime.utcnow)