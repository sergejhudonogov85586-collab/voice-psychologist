from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean
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
    created_at = Column(DateTime, default=datetime.utcnow)
    is_read = Column(Boolean, default=False)