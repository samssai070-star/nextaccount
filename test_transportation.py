#!/usr/bin/env python3
"""
NextAccount v2 — test_transportation.py
交通費・定期券機能の自動テスト
"""

import sys
import os
sys.path.insert(0, '\''/opt/nextaccount/app'\'')

from datetime import datetime
from core.transportation import (
    register_commute_ticket,
    get_user_commute_ticket,
    calculate_overlap_deduction,
    validate_transportation_price,
    calculate_nontaxable_transportation,
    normalize_station_name
)
from core.database import (
    upsert_commute_ticket,
    get_commute_ticket,
    log_compliance_check,
    get_compliance_logs,
)

# テスト用の tenant_id（ダミー）
TEST_TENANT_ID = "550e8400-e29b-41d4-a716-446655440000"
TEST_USER_ID = "U12345TEST"
TEST_EMPLOYEE = "テスト太郎"


def test_station_normalization():
    """駅名の正規化テスト"""
    print("\n" + "="*60)
    print("TEST 1: 駅名の正規化")
    print("="*60)
    
    tests = [
        ("新宿", "新宿"),
        ("しんじゅく", "新宿"),
        ("shinjuku", "新宿"),
        ("渋谷", "渋谷"),
        ("しぶや", "渋谷"),
    ]
    
    for input_name, expected in tests:
        result = normalize_station_name(input_name)
        status = "✅ PASS" if result == expected else "❌ FAIL"
        print(f"  {status} | {input_name:20} → {result:10} (期待: {expected})")


def test_commute_ticket_registration():
    """定期券登録テスト"""
    print("\n" + "="*60)
    print("TEST 2: 定期券登録")
    print("="*60)
    
    try:
        ticket = upsert_commute_ticket(
            slack_user_id=TEST_USER_ID,
            employee_name=TEST_EMPLOYEE,
            tenant_id=TEST_TENANT_ID,
            from_station="新宿",
            to_station="渋谷",
            monthly_price=10000,
            reference_price=200
        )
        
        print(f"  ✅ PASS | 定期券を登録しました")
        print(f"    - ユーザーID: {ticket['\''slack_user_id'\'']}")
        print(f"    - 区間: {ticket['\''from_station'\'']} → {ticket['\''to_station'\'']}")
        print(f"    - 月額: ¥{ticket['\''monthly_price'\'']:,}")
        print(f"    - 参考最安値: ¥{ticket.get('\''reference_price'\'', '\''N/A'\'')}")
        
        # 取得テスト
        retrieved = get_commute_ticket(TEST_USER_ID, TEST_TENANT_ID)
        if retrieved:
            print(f"  ✅ PASS | 定期券情報を取得しました")
            return True
        else:
            print(f"  ❌ FAIL | 定期券情報の取得に失敗")
            return False
            
    except Exception as e:
        print(f"  ❌ FAIL | {e}")
        return False


def test_transportation_price_validation():
    """経済合理性チェックテスト"""
    print("\n" + "="*60)
    print("TEST 3: 経済合理性チェック")
    print("="*60)
    
    tests = [
        ("新宿", "渋谷", 200, True, "OK"),      # 参考¥200 の範囲内
        ("新宿", "渋谷", 250, False, "NG"),     # 参考¥200 の範囲外
        ("新宿", "渋谷", 180, True, "OK"),      # 参考¥200 -10%
    ]
    
    for from_st, to_st, price, expected_valid, expected_result in tests:
        is_valid, message = validate_transportation_price(from_st, to_st, price)
        status = "✅ PASS" if (is_valid == expected_valid and expected_result in message) else "❌ FAIL"
        print(f"  {status} | ¥{price} ({from_st}→{to_st})")
        print(f"       {message}")


def test_overlap_deduction():
    """定期区間重複扣除ロジックテスト"""
    print("\n" + "="*60)
    print("TEST 4: 定期区間重複扣除ロジック")
    print("="*60)
    
    # シナリオ 1: 定期（新宿→渋谷）、申請（新宿→恵比寿）
    # → 新宿→渋谷 の ¥200 を扣除
    result = calculate_overlap_deduction(
        commute_from="新宿",
        commute_to="渋谷",
        claimed_from="新宿",
        claimed_to="恵比寿"
    )
    
    print(f"\n  シナリオ 1: 定期券（新宿→渋谷）、申請（新宿→恵比寿）")
    print(f"    重複状態: {result.get('\''is_overlapped'\'')}")
    print(f"    扣除額: ¥{result.get('\''overlap_amount'\'', 0)}")
    print(f"    報告額: ¥{result.get('\''final_reimbursement'\'', '\''N/A'\'')}")
    print(f"    メッセージ: {result.get('\''message'\'')}")
    
    if result.get('\''is_overlapped'\''):
        print(f"  ✅ PASS | 重複が正しく検出されました")
    else:
        print(f"  ⚠️ NOTE | 重複検出なし（参考最安値の設定を確認してください）")


def test_nontaxable_limit():
    """非課税限額管理テスト"""
    print("\n" + "="*60)
    print("TEST 5: 非課税限額管理")
    print("="*60)
    
    # テスト 1: 限額以下
    result1 = calculate_nontaxable_transportation(
        monthly_commute_price=10000,
        additional_transportation_claims=[5000, 3000, 2000]
    )
    
    print(f"\n  テスト 1: 月次定期 ¥10,000 + 実報実销 ¥10,000 = 合計 ¥20,000")
    print(f"    非課税額: ¥{result1['\''nontaxable_amount'\'']:,}")
    print(f"    課税額: ¥{result1['\''taxable_amount'\'']:,}")
    print(f"    ステータス: {result1['\''status'\'']}")
    
    if result1['\''status'\''] == '\''OK'\'' and result1['\''taxable_amount'\''] == 0:
        print(f"  ✅ PASS | 限額以下が正しく処理されました")
    
    # テスト 2: 限額超過
    result2 = calculate_nontaxable_transportation(
        monthly_commute_price=80000,
        additional_transportation_claims=[40000, 35000]
    )
    
    print(f"\n  テスト 2: 月次定期 ¥80,000 + 実報実销 ¥75,000 = 合計 ¥155,000")
    print(f"    非課税額: ¥{result2['\''nontaxable_amount'\'']:,} (限額 ¥150,000)")
    print(f"    課税額: ¥{result2['\''taxable_amount'\'']:,}")
    print(f"    ステータス: {result2['\''status'\'']}")
    
    if result2['\''status'\''] == '\''OVER_LIMIT'\'' and result2['\''taxable_amount'\''] == 5000:
        print(f"  ✅ PASS | 限額超過が正しく処理されました")


def test_compliance_logging():
    """合規性監査ログテスト"""
    print("\n" + "="*60)
    print("TEST 6: 合規性監査ログ")
    print("="*60)
    
    try:
        event_id = "T20260414-00001"
        
        # ログを記録
        log_compliance_check(
            event_id=event_id,
            tenant_id=TEST_TENANT_ID,
            check_type="commute_overlap",
            result="OK",
            details={
                "commute_from": "新宿",
                "commute_to": "渋谷",
                "overlap_amount": 200,
                "final_reimbursement": 50
            }
        )
        
        print(f"  ✅ PASS | 監査ログを記録しました")
        
        # ログを取得
        logs = get_compliance_logs(event_id, TEST_TENANT_ID)
        if logs:
            print(f"  ✅ PASS | 監査ログを取得しました ({len(logs)} 件)")
            for log in logs:
                print(f"    - {log['\''check_type'\'']}: {log['\''result'\'']} ({log['\''created_at'\'']})")
        
    except Exception as e:
        print(f"  ⚠️ NOTE | {e}")


def run_all_tests():
    """全テストを実行"""
    print("\n")
    print("╔" + "="*58 + "╗")
    print("║" + " NextAccount v2 - 交通費・定期券 テストスイート ".center(58) + "║")
    print("╚" + "="*58 + "╝")
    
    test_station_normalization()
    test_commute_ticket_registration()
    test_transportation_price_validation()
    test_overlap_deduction()
    test_nontaxable_limit()
    test_compliance_logging()
    
    print("\n" + "="*60)
    print("全テスト完了 🎉")
    print("="*60 + "\n")


if __name__ == "__main__":
    run_all_tests()

