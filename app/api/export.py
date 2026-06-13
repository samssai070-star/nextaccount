"""CSV Export API — ビジネス・パートナープラン限定"""
from __future__ import annotations
import logging
from flask import Blueprint, request, Response
from .helpers import get_db_connection, get_db_cursor, require_auth, error_response

logger = logging.getLogger(__name__)
export_bp = Blueprint("export", __name__, url_prefix="/api/export")

ALLOWED_PLANS = ("business", "partner")

FORMAT_MAP = {
    "yayoi":  ("core.yayoi_export",        "build_yayoi_csv",    "yayoi",  "弥生"),
    "yenbo":  ("core.yayoi_export",        "build_yenbo_csv",    "yenbo",  "クラウド円簿"),
    "freee":  ("core.csv_export",          "build_freee_csv",    "freee",  "freee"),
    "mf":     ("core.csv_export",          "build_mf_csv",       "mf",     "マネーフォワード"),
    "csv":    ("core.csv_export",          "build_generic_csv",  "csv",    "汎用"),
    "kanjo":  ("core.multi_software_export","build_kanjo_ahra_csv","kanjo","勘定奉行"),
    "pca":    ("core.multi_software_export","build_pca_csv",      "pca",   "PCA"),
    "tkc":    ("core.multi_software_export","build_tkc_csv",      "tkc",   "TKC"),
    "jdl":    ("core.multi_software_export","build_jdl_csv",      "jdl",   "JDL"),
    "mjs":    ("core.multi_software_export","build_mjs_csv",      "mjs",   "MJS"),
}


@export_bp.route("/csv", methods=["GET"])
@require_auth
def export_csv():
    """経費仕訳CSVエクスポート（ビジネス・パートナープランのみ）"""
    try:
        org_id = request.organization_id
        fmt = request.args.get("format", "csv")
        start = request.args.get("start", "")
        end = request.args.get("end", "")

        if not start or not end:
            return error_response("start と end パラメータは必須です", 400)

        if fmt not in FORMAT_MAP:
            return error_response(f"不明なフォーマット: {fmt}", 400)

        conn = get_db_connection()
        cur = get_db_cursor(conn)

        # プランチェック
        cur.execute("SELECT plan FROM organizations WHERE id=%s", (org_id,))
        org = cur.fetchone()
        if not org or org.get("plan") not in ALLOWED_PLANS:
            conn.close()
            return error_response("この機能はビジネス・パートナープランのみ利用できます", 403)

        # org_id → workspace_id → tenant_id
        cur.execute(
            "SELECT workspace_id FROM slack_workspaces WHERE organization_id=%s AND is_connected=TRUE LIMIT 1",
            (org_id,)
        )
        ws = cur.fetchone()
        if not ws:
            conn.close()
            return error_response("Slack ワークスペースが接続されていません", 404)

        cur.execute(
            "SELECT id FROM tenants WHERE slack_team_id=%s LIMIT 1",
            (ws["workspace_id"],)
        )
        tenant_row = cur.fetchone()
        if not tenant_row:
            conn.close()
            return error_response("テナント情報が見つかりません", 404)

        tenant_id = str(tenant_row["id"])

        # 承認済み仕訳を取得
        cur.execute(
            """SELECT * FROM accounting_events
               WHERE tenant_id=%s AND event_date>=%s AND event_date<=%s AND status=%s
               ORDER BY event_date""",
            (tenant_id, start, end, "業務承認済")
        )
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return error_response(f"{start} ～ {end} の承認済み仕訳が見つかりません", 404)

        events = []
        for r in rows:
            evt = dict(r)
            if "amount" not in evt or evt["amount"] is None:
                evt["amount"] = sum(
                    int(evt.get(k, 0) or 0)
                    for k in ["taxable_10_amount", "tax_10_amount", "taxable_8_amount", "tax_8_amount"]
                )
            events.append(evt)

        mod_name, func_name, fname, _ = FORMAT_MAP[fmt]
        import importlib
        mod = importlib.import_module(mod_name)
        build_fn = getattr(mod, func_name)
        csv_bytes = build_fn(events)

        from datetime import datetime as dt
        start_str = dt.strptime(start, "%Y-%m-%d").strftime("%Y%m%d")
        end_str = dt.strptime(end, "%Y-%m-%d").strftime("%Y%m%d")
        filename = f"{fname}_{start_str}_{end_str}.csv"

        return Response(
            csv_bytes,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
        )

    except Exception as e:
        logger.error(f"export_csv error: {e}", exc_info=True)
        return error_response(str(e), 500)
