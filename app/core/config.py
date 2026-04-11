"""
NextAccount v2 — config.py
全モジュール共通の設定・定数を管理する。
環境変数の読み込みはここだけで行い、他モジュールはこのファイルを import する。
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# ログ設定
# ============================================================
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# ============================================================
# Slack
# ============================================================
SLACK_BOT_TOKEN: str = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN: str = os.environ.get("SLACK_APP_TOKEN", "")

# ============================================================
# Google
# ============================================================
GOOGLE_APPLICATION_CREDENTIALS: str = os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS", ""
)
GOOGLE_SHEET_ID: str = os.environ.get("GOOGLE_SHEET_ID", "")

# ============================================================
# Database
# ============================================================
DATABASE_URL: str = os.environ.get("DATABASE_URL", "")

# ============================================================
# App
# ============================================================
ENVIRONMENT: str = os.environ.get("ENVIRONMENT", "development")
TZ: str = os.environ.get("TZ", "Asia/Tokyo")

# ============================================================
# 勘定科目マスター（借方）
# ============================================================
DEBIT_ACCOUNTS = [
    "旅費交通費",
    "通信費",
    "水道光熱費",
    "接待交際費",
    "消耗品費",
    "会議費",
    "広告宣伝費",
    "地代家賃",
    "修繕費",
    "諸雑費",
]

# 貸方は常に「未払費用（社員名）」
CREDIT_ACCOUNT_BASE = "未払費用"

# ============================================================
# 税区分
# ============================================================
TAX_RATE_10 = 0.10
TAX_RATE_8  = 0.08   # 軽減税率（食料品など）

# ============================================================
# 商家マスター: keyword → (正規化名, 勘定科目)
# 150+ パターン対応
# ============================================================
BRAND_MASTER: dict[str, tuple[str, str]] = {}  # Claude AIが判定するため空

# ============================================================
# Google Sheets 列定義
# ============================================================
SHEET_COLUMNS = [
    "管理ID",          # A
    "発生日",          # B
    "取引先",          # C
    "税込金額",        # D
    "税率10%対象額",   # E
    "消費税(10%)",     # F
    "税率8%対象額",    # G
    "消費税(8%)",      # H
    "T番号",           # I
    "借方科目",        # J
    "貸方科目",        # K  ← 未払費用（社員名）
    "申請者",          # L
    "ステータス",      # M
    "備考",            # N
]

# 社員別シート名フォーマット: "{employee_name}_{YYYYMM}"
EMPLOYEE_SHEET_NAME_FORMAT = "{employee}_{ym}"

# 会社財務集計シート名
FINANCE_SUMMARY_SHEET_NAME = "財務部門_集計"

# ============================================================
# 2026-04-05 追加ルール
# ============================================================
