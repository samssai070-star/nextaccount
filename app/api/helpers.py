"""API Helper Functions"""
from __future__ import annotations
import os, jwt, secrets, hashlib
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL")
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-key-change-in-production")
JWT_EXPIRY_HOURS = 24

def get_db_connection():
    """数据库连接"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        raise Exception(f"Database connection failed: {e}")

def get_db_cursor(conn):
    """获取游标"""
    return conn.cursor(cursor_factory=RealDictCursor)

def hash_password(password: str) -> str:
    """密码哈希"""
    return hashlib.sha256(password.encode()).hexdigest()

def generate_token(user_id: int, organization_id: int) -> str:
    """生成JWT token"""
    payload = {
        "user_id": user_id,
        "organization_id": organization_id,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def verify_token(token: str) -> dict | None:
    """验证JWT token"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload
    except:
        return None

def require_auth(f):
    """认证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid token"}), 401

        token = auth_header[7:]
        payload = verify_token(token)
        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401

        request.user_id = payload.get("user_id")
        request.organization_id = payload.get("organization_id")
        return f(*args, **kwargs)

    return decorated_function

def success_response(data: dict | list, status_code: int = 200):
    """成功响应"""
    return jsonify({"success": True, "data": data}), status_code

def error_response(message: str, status_code: int = 400):
    """错误响应"""
    return jsonify({"success": False, "error": message}), status_code
