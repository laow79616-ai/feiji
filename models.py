from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.sql import func
from database import Base

class ApiCredential(Base):
    """API 池"""
    __tablename__ = "api_credentials"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100))                      # 备注名，例如：API-1
    api_id = Column(Integer, nullable=False)
    api_hash = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Proxy(Base):
    """代理池"""
    __tablename__ = "proxies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100))                      # 备注名
    proxy_str = Column(String(200), unique=True)    # ip:port:user:pass
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), unique=True, index=True)
    name = Column(String(100), nullable=True)
    session_name = Column(String(100), unique=True)
    
    api_id = Column(Integer, ForeignKey("api_credentials.id"), nullable=False)
    proxy_id = Column(Integer, ForeignKey("proxies.id"), nullable=False)
    
    is_active = Column(Boolean, default=True)
    is_online = Column(Boolean, default=False)
    health_status = Column(String(20), default='unknown')
    is_monitor = Column(Boolean, default=False)
    last_login = Column(DateTime(timezone=True), server_default=func.now())
    note = Column(Text, nullable=True)


class Target(Base):
    """目标群组/频道"""
    __tablename__ = "targets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100))
    link = Column(String(300), unique=True)
    member_count = Column(Integer, nullable=True)
    last_member_count = Column(Integer, nullable=True)
    member_updated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())