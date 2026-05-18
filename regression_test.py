#!/usr/bin/env python3
"""
NextAccount v2 — 回帰テストスクリプト
新機能追加前後に実行して、既存機能が壊れていないか確認する
"""
import sys
import os
import json
import psycopg2
import requests

sys.path.insert(0, '/app')

PASS = "✅ PASS"
FAIL = "❌ FAIL"
results = []

def test(name, fn):
    try:
        fn()
        results.append((PASS, name))
        print(f"{PASS} | {name}")
    except Exception as e:
        results.append((FAIL, name))
        print(f"{FAIL} | {name} → {e}")

print("\n" + "="*60)
print("NextAccount v2 回帰テスト")
print("="*60)

# ========== DB テスト ==========
print("\n【1. データベース】")

def test_db_connection():
    conn = psycopg2.connect(os.getenv('DATABASE_URL'))
    conn.close()
test("DB接続", test_db_connection)

def test_transportation_columns():
    conn = psycopg2.connect(os.getenv('DATABASE_URL'))
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='accounting_events'")
    cols = [r[0] for r in cur.fetchall()]
    conn.close()
    required = ['transportation_from', 'transportation_to', 'reference_price', 'overlap_amount', 'final_reimbursement', 'compliance_status']
    missing = [c for c in required if c not in cols]
    if missing:
        raise Exception(f"カラム不足: {missing}")
test("transportation_fromカラム存在", test_transportation_columns)

def test_duplicate_check_logic():
    conn = psycopg2.connect(os.getenv('DATABASE_URL'))
    cur = conn.cursor()
    cur.execute("SELECT pg_get_functiondef(oid) FROM pg_proc WHERE proname='check_duplicate'" )
    # SQLクエリで承認済チェックを確認
    cur.execute("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name='accounting_events' AND column_name='status'
    """)
    assert cur.fetchone() is not None
    conn.close()
    # database.pyのcheck_duplicate関数に承認済フィルターがあるか確認
    with open('/app/core/database.py', 'r') as f:
        content = f.read()
    assert "承認済" in content, "重複チェックに承認済フィルターがない"
test("重複チェック（承認済のみ）", test_duplicate_check_logic)

def test_insert_event_defaults():
    with open('/app/core/database.py', 'r') as f:
        content = f.read()
    assert 'setdefault("transportation_from", None)' in content
    assert 'setdefault("transportation_to", None)' in content
test("insert_eventデフォルト値", test_insert_event_defaults)

# ========== コードテスト ==========
print("\n【2. コード構造】")

def test_journal_entry_fields():
    with open('/app/core/accounting.py', 'r') as f:
        content = f.read()
    assert 'transportation_from' in content
    assert 'transportation_to' in content
    assert 'compliance_status' in content
test("JournalEntryフィールド", test_journal_entry_fields)

def test_to_db_dict():
    with open('/app/core/accounting.py', 'r') as f:
        content = f.read()
    assert '"transportation_from": self.transportation_from' in content
    assert '"compliance_status": self.compliance_status' in content
test("to_db_dict transportation含む", test_to_db_dict)

def test_ocr_claude_vision():
    with open('/app/core/ocr.py', 'r') as f:
        content = f.read()
    assert '_call_claude_vision' in content, 'Claude Vision関数が存在しない'
    assert 'クレジット支払' in content, 'クレジット支払優先ロジックがない'
    assert 'ANTHROPIC_API_URL' in content, 'Anthropic API設定がない'
test("OCR Claude Vision使用", test_ocr_claude_vision)

def test_tax_rules_in_prompt():
    with open('/app/core/ai_classifier_v2.py', 'r') as f:
        content = f.read()
    assert '国税庁' in content
    assert '福利厚生費' in content
    assert 'スーパー' in content
test("国税庁税法ルール存在", test_tax_rules_in_prompt)

def test_csv_handler_imported():
    with open('/app/bot/slack_handler.py', 'r') as f:
        content = f.read()
    assert 'setup_csv_commands' in content
test("/csvコマンドimport", test_csv_handler_imported)

def test_export_command_exists():
    with open('/app/bot/slack_handler.py', 'r') as f:
        content = f.read()
    assert '@app.command("/export")' in content
    assert '業務承認済' in content
test("/exportコマンド存在", test_export_command_exists)

def test_invoice_number_validation():
    with open('/app/core/ocr.py', 'r') as f:
        content = f.read()
    assert "startswith('T')" in content or 'startswith("T")' in content
test("T番号バリデーション", test_invoice_number_validation)

# ========== Flask テスト ==========
print("\n【3. サービス稼働】")

def test_health_endpoint():
    resp = requests.get('http://127.0.0.1:8080/health', timeout=5)
    assert resp.status_code == 200
test("ヘルスチェックエンドポイント", test_health_endpoint)

# ========== 結果サマリー ==========
print("\n" + "="*60)
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
total = len(results)
print(f"結果: {passed}/{total} PASS  |  {failed} FAIL")
print("="*60)

if failed > 0:
    print("\n❌ 失敗したテストがあります！デプロイを中止してください。")
    sys.exit(1)
else:
    print("\n✅ 全テスト通過！デプロイ可能です。")
    sys.exit(0)
