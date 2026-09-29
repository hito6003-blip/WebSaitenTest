import streamlit as st
import pandas as pd
import time
from datetime import datetime, timezone

def show_login_page(supabase, settings):
    """
    🔐 画面制御1: ログイン画面（二重ログイン防止＆強制解除対応版）
    """
    st.markdown(settings.LABELS["login_title"])
    
    with st.form("login_form"):
        input_id = st.text_input(settings.LABELS["login_user_id"], placeholder=settings.LABELS["input_id_placeholder"])
        input_pass = st.text_input(settings.LABELS["login_password"], type="password", placeholder=settings.LABELS["input_pass_placeholder"])
        
        # フォーム内のボタン等幅対称配置
        col_login, col_unlock = st.columns(2)
        with col_login:
            submit_button = st.form_submit_button(settings.LABELS["login_button"], use_container_width=True)
        with col_unlock:
            unlock_button = st.form_submit_button(settings.LABELS["force_unlock"], use_container_width=True)
        
        # --- ① 通常ログイン処理 ---
        if submit_button:
            if not input_id or not input_pass:
                st.error(settings.LABELS["login_required_fields"])
            else:
                try:
                    response = supabase.table("graders") \
                        .select("grader_id, grader_name, role_id, group_id, login_status, last_activity_at") \
                        .eq("grader_id", input_id) \
                        .eq("password", input_pass) \
                        .limit(1) \
                        .execute()
                  
                    if response.data and len(response.data) > 0:
                        user_data = response.data[0]
                        current_status = user_data.get("login_status", False)
                        raw_last_activity = user_data.get("last_activity_at")
                        
                        # ⏱️ 放置によるタイムアウト（風化）の秒数判定
                        is_timeout = False
                        if raw_last_activity:
                            try:
                                raw_str = str(raw_last_activity).strip()
                                if " " in raw_str:
                                    date_part, time_part = raw_str.split(" ")
                                    time_part = time_part.split("+")[0].split("Z")[0]
                                    clean_ts_str = f"{date_part} {time_part}"
                                else:
                                    clean_ts_str = raw_str.split("+")[0].split("Z")[0]
                                    
                                db_datetime = datetime.fromisoformat(clean_ts_str)
                                db_epoch = db_datetime.replace(tzinfo=timezone.utc).timestamp()
                                now_epoch = datetime.now(timezone.utc).timestamp()
                                
                                timeout_limit = getattr(settings, "TIMEOUT_SECONDS", settings.LOGIN_TIMEOUT_SECONDS)
                                elapsed_seconds = now_epoch - db_epoch
                                
                                # 🧪 デバッグ情報の出力
                                st.info(settings.LABELS["login_debug_time"].format(elapsed=int(elapsed_seconds), limit=timeout_limit, db_value=raw_last_activity))
                                
                                if elapsed_seconds > timeout_limit or elapsed_seconds < 0:
                                    is_timeout = True
                                    
                            except Exception as e:
                                st.error(settings.LABELS["login_time_error"].format(error=e, db_value=raw_last_activity))

                        # ⚠️ 二重ログインチェック
                        if current_status is True and not is_timeout:
                            st.error(settings.LABELS["login_already_active"])
                        else:
                            # 救済または通常ログイン成功に伴うDB更新
                            now_str = datetime.now(timezone.utc).isoformat()
                            supabase.table("graders") \
                                .update({
                                    "login_status": True,
                                    "last_activity_at": now_str
                                }) \
                                .eq("grader_id", input_id) \
                                .execute()
                            
                            # 💡 role_id を安全に整数へパース
                            raw_role = user_data.get("role_id", None)
                            parsed_role = None
                            if raw_role is not None:
                                try:
                                    parsed_role = int(raw_role)
                                except Exception:
                                    try:
                                        parsed_role = int(str(raw_role).strip())
                                    except Exception:
                                        parsed_role = None
                            # セッション状態への同期
                            st.session_state["logged_in"] = True
                            st.session_state["user_id"] = input_id
                            st.session_state["user_name"] = user_data.get("grader_name", "未設定")
                            st.session_state["group_id"] = user_data.get("group_id", None)
                            st.session_state["role_id"] = parsed_role
                            st.session_state["last_activity_time"] = time.time()  # タイムアウト監視を開始
                            
                            st.success(settings.LABELS["login_success"])
                            st.rerun()
                    else:
                        st.error(settings.LABELS["login_invalid_credentials"])
                except Exception as e:
                    st.error(settings.LABELS["login_error"].format(error=e))

        # --- ② ログイン詰まり強制解除処理 ---
        if unlock_button:
            if not input_id or not input_pass:
                st.error(settings.LABELS["unlock_required_fields"])
            else:
                try:
                    # IDとパスワードの整合性を確認
                    response = supabase.table("graders") \
                        .select("grader_id, login_status") \
                        .eq("grader_id", input_id) \
                        .eq("password", input_pass) \
                        .limit(1) \
                        .execute()
                    
                    if response.data and len(response.data) > 0:
                        # 整合性が取れたら、DBのログイン状態を強制的に False へリセット
                        supabase.table("graders") \
                            .update({"login_status": False}) \
                            .eq("grader_id", input_id) \
                            .execute()
                        
                        # 💡【新規追加】その採点者が掴みっぱなしにしていた問題のレコードロックも100%連動して全自動解放！
                        supabase.table("tbl_scoring_question_management") \
                            .update({
                                "is_locked": False, 
                                "locked_by_webid": None, 
                                "locked_at": None
                            }) \
                            .eq("locked_by_webid", input_id) \
                            .execute()
                            
                        st.success(settings.LABELS["unlock_success"])
                    else:
                        st.error(settings.LABELS["unlock_invalid_credentials"])
                except Exception as e:
                    st.error(settings.LABELS["unlock_error"].format(error=e))
