import streamlit as st
from datetime import datetime, timezone

def insert_operation_log(supabase, operator_id: str, action_type: str, description: str, target_id: str = None):
    """
    tbl_operation_logs テーブルに操作ログを1件正しく刻印する（DBフィールド完全適合版）
    """
    try:
        supabase.table("tbl_operation_logs").insert({
            "operator_id": operator_id,       # 採点者・管理者のID
            "action_type": action_type,       # 例: "CSV_EXPORT", "LOGIN"
            "target_id": target_id,           # 対象の問題IDや日付など（任意・初期値None）
            "description": description,       # 詳細な文言
            "created_at": datetime.now(timezone.utc).isoformat() # 自動付与でなければ明示
        }).execute()
    except Exception as log_err:
        # メイン処理を落とさないための安全弁
        st.warning(f"⚠️ 操作ログの記録に失敗しました: {log_err}")


def release_all_user_locks_common(supabase, user_id: str):
    """
    🔒 【バグ修正用】対象ユーザーが掴みっぱなしにしている全レコードのロックを強制一括解除する
    """
    try:
        supabase.table("tbl_scoring_question_management").update({
            "is_locked": False,
            "locked_by_webid": None,
            "locked_at": None
        }).eq("locked_by_webid", user_id).execute()
        return True
    except Exception as e:
        # 他の処理を巻き込んで落ちないようログ等に留める
        return False
