import streamlit as st
import pandas as pd
import time

try:
    from master_cache import clear_master_cache, get_grader_master, get_question_master
except ImportError:
    from views.master_cache import clear_master_cache, get_grader_master, get_question_master
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

# 📝 【バグ修正】自分と同じviewsフォルダ内から安全にインポートする記述に修正
try:
    from log_common import insert_operation_log
except ImportError:
    try:
        # 万が一親階層から探すケースも想定したダブルガード
        from views.log_common import insert_operation_log
    except ImportError:
        # 最終セーフティ：どちらもダメならダミー関数で落とさない
        def insert_operation_log(*args, **kwargs):
            pass

# 💡 引数の最後に「current_user_id」を追加！
def show_hold_management_page(supabase, settings, display_confirm_panel, current_user_id):
    """
    🟣 タブ7: 保留（H）レコード専用管理・再採点画面（管理者専用・2ステップ完全再現版）
    """
    user_role_id = st.session_state.get("role_id", None)
    current_group_id = st.session_state.get("group_id", None)
    
    # 💡 確定仕様：全体特権管理者（role_id == 4）のみに限定ロック
    if user_role_id != 4:
        st.error(settings.LABELS["hold_access_denied"])
        return

    # 🔑 画面遷移用の状態変数を初期化
    if "hold_current_step" not in st.session_state:
        st.session_state["hold_current_step"] = "select"

    # 👥 どのステップからでも名前を逆引きできるよう、関数直下（共通エリア）でマスタを取得
    try:
        group_member_map = {
            m["grader_id"]: m.get("grader_name", "")
            for m in get_grader_master(supabase)
            if m.get("grader_id") is not None
        }
    except Exception as e:
        st.error(settings.LABELS["hold_master_error"].format(error=e))
        return

    # ==============================================================================
    # 📄 【ステップ1】保留問題一覧画面 (hold_current_step が "select" のとき)
    # ==============================================================================
    if st.session_state["hold_current_step"] == "select":
        st.header(settings.LABELS["hold_title"])
        st.markdown(settings.LABELS["hold_description"])
        
        # 💡【重複エラー対策】重複を根絶するため、キー名をユニークに変更
        if st.button(settings.LABELS["hold_refresh"], key="hold_list_refresh_btn_v2", use_container_width=True):
            clear_master_cache()
            st.rerun()

        try:
            with st.spinner(settings.LABELS["hold_loading"]):
                response = supabase.table("tbl_scoring_question_management") \
                    .select("saiten_question_id, checker_webid, response_id, judge_mark_result, final_approver_id, grading_comp_date") \
                    .execute()
                
                master_data = get_question_master(supabase)
                question_title_map = {row["response_id"]: row.get("question_title", "") for row in master_data if row.get("response_id")}
            if response.data:
                df_raw = pd.DataFrame(response.data)
                
                # 日付の表記揺れ・型ズレを「YYYY-MM-DD」に完全統一
                if "grading_comp_date" in df_raw.columns:
                    df_raw["grading_comp_date"] = pd.to_datetime(df_raw["grading_comp_date"], errors='coerce').dt.strftime('%Y-%m-%d')
                
                # 型エラーを防ぐため採点者IDと確定者IDを文字列としてクレンジング
                df_raw["checker_clean"] = df_raw["checker_webid"].fillna("").astype(str).str.strip()
                df_raw["approver_clean"] = df_raw["final_approver_id"].fillna("").astype(str).str.strip()
                
                # ─── 🔑 【最重要：role_id=4 の特権管理者のIDリストを厳格抽出】 ───
                try:
                    admin_id_set = {
                        str(user.get("grader_id")).strip()
                        for user in get_grader_master(supabase)
                        if user.get("role_id") == 4 and user.get("grader_id") is not None
                    }
                except Exception:
                    admin_id_set = set()
                # ──────────────────────────────────────────────────────────────────

                # 💡 各種進捗・監査用フラグの計算ルールを「保留」と「role_id=4の管理者確定」に完全固定
                df_raw["is_graded"] = df_raw["judge_mark_result"].fillna("").str.strip().isin(["O", "X", "*"])
                df_raw["is_unprocessed_hold"] = df_raw["judge_mark_result"].fillna("").str.strip() == "H"
                
                # 💡 最終確定した人のIDが、role_id=4の管理者マスタに存在するかどうかを厳密に判定
                df_raw["is_admin_approved"] = df_raw["approver_clean"].isin(admin_id_set)
                
                admin_id_str = str(current_user_id).strip()
                
                # フィルター用のカウント：管理者が対応したもののうち、自分か自分以外か
                df_raw["is_my_done"] = df_raw["is_graded"] & df_raw["is_admin_approved"] & (df_raw["approver_clean"] == admin_id_str)
                df_raw["is_others_done"] = df_raw["is_graded"] & df_raw["is_admin_approved"] & (df_raw["approver_clean"] != admin_id_str)
                
                # グループ（キー3軸：採点者・日付・問題）ごとに一挙集計
                df_summary = (
                    df_raw
                    .groupby(["checker_webid", "grading_comp_date", "response_id"], dropna=False)
                    .agg(
                        採点総数=("response_id", "size"),
                        採点済数=("is_graded", "sum"),
                        保留残数=("is_unprocessed_hold", "sum"),
                        保留対応数=("is_admin_approved", "sum"),
                        自対応数=("is_my_done", "sum"),
                        他対応数=("is_others_done", "sum")
                    )
                    .reset_index()
                )
                
                # 🚨【超厳格水際ロック】
                # 「現在進行形で保留（H）が残っている」または「過去に管理者が上書き対応した履歴が1件でもある」グループのみに完全限定！
                df_summary = df_summary[(df_summary['保留残数'] > 0) | (df_summary['保留対応数'] > 0)]
                
                df_summary.columns = ['対象採点者', '採点完了日', '問題', '採点総数', '採点済数', '保留残数', '保留対応数', '自対応数', '他対応数']
                df_summary = df_summary[['対象採点者', '採点完了日', '問題', '採点総数', '採点済数', '保留残数', '保留対応数', '自対応数', '他対応数']]

                # 💡 表示フィルターUI（「過去の採点完了日分も表示」仕様の4大フィルター）
                st.markdown(settings.LABELS["hold_filter_title"])
                f_col1, f_col2, f_col3, f_col4 = st.columns(4)
                with f_col1:
                    show_hold_active = st.checkbox(settings.LABELS["hold_filter_active"], value=True, key="filter_hold_active")
                with f_col2:
                    show_hold_my_done = st.checkbox(settings.LABELS["hold_filter_mine"], value=False, key="filter_hold_my_done")
                with f_col3:
                    show_hold_others_done = st.checkbox(settings.LABELS["hold_filter_others"], value=False, key="filter_hold_others_done")
                with f_col4:
                    show_hold_past_only = st.checkbox(settings.LABELS["hold_filter_past"], value=True, key="filter_hold_past_only")
                # ─── 📊 チェックボックスの状態を掛け合わせてマスクを作成 ───
                keep_mask = pd.Series(False, index=df_summary.index)
                
                if show_hold_active:
                    keep_mask |= (df_summary['保留残数'] > 0)
                if show_hold_my_done:
                    keep_mask |= (df_summary['自対応数'] > 0)
                if show_hold_others_done:
                    keep_mask |= (df_summary['他対応数'] > 0)
                    
                if not show_hold_past_only:
                    try:
                        today_now = datetime.now()
                        today_str = today_now.strftime('%Y-%m-%d')
                        
                        def is_today_or_future(date_val):
                            if pd.isna(date_val) or str(date_val).strip() == "" or str(date_val).strip() == "（未設定）":
                                return True
                            try:
                                return str(date_val).strip() >= today_str
                            except Exception:
                                return True
                        
                        future_date_mask = df_summary['採点完了日'].apply(is_today_or_future)
                        keep_mask = keep_mask & future_date_mask
                    except Exception:
                        pass
                    
                df_summary = df_summary[keep_mask]

                if df_summary.empty:
                    st.success(settings.LABELS["hold_no_match"])
                    return

                # 採点完了日の昇順にソート
                df_summary = df_summary.sort_values(by=['採点完了日', '対象採点者', '問題'], ascending=[True, True, True])

                st.metric(settings.LABELS["hold_count"], len(df_summary))
                st.write("")

                # ─── 📊 ご指定の全 8 列レイアウトヘッダー ───
                h_col1, h_col2, h_col3, h_col4, h_col5, h_col6, h_col7, h_col8 = st.columns([1.3, 1.3, 2.7, 0.7, 0.7, 0.8, 0.8, 1.7])
                h_col1.markdown("**対象採点者**")
                h_col2.markdown("**採点完了日**")
                h_col3.markdown("**問題**")
                h_col4.markdown("**採点総数**")
                h_col5.markdown("**採点済数**")
                h_col6.markdown("**保留残数**")
                h_col7.markdown("**保留対応数**")
                h_col8.markdown("**操作**")
                st.divider()

                # ─── 🔄 行ループ描画 ───
                for index, row in df_summary.iterrows():
                    col1, col2, col3, col4, col5, col6, col7, col8 = st.columns([1.3, 1.3, 2.7, 0.7, 0.7, 0.8, 0.8, 1.7])
                    
                    total_count = int(row['採点総数'])
                    graded_count = int(row['採点済数'])
                    unprocessed_count = int(row['保留残数'])
                    intervented_count = int(row['保留対応数'])
                    
                    comp_date_val = row['採点完了日']
                    display_date = "（未設定）" if pd.isna(comp_date_val) else str(comp_date_val).strip()
                    
                    current_response_id = row['問題']
                    display_title = question_title_map.get(current_response_id, current_response_id)
                    if not display_title or str(display_title).strip() == "":
                        display_title = current_response_id
                        
                    if unprocessed_count > 0:
                        font_color = "#DC3545"      # 🔴 赤：未処理の保留が残っている
                        status_label = "🔍 再採点開始"
                    else:
                        font_color = "#1266F1"      # 🔵 青：完了しているが、保留対応の歴史があるグループ
                        status_label = "🔄 確認・修正"
                        
                    style_attr = f"color: {font_color}; font-family: 'Meiryo', sans-serif; font-weight: bold; font-size: 13px; margin: 0; padding: 4px 0;"
                    
                    col1.markdown(f"<p style='{style_attr}'>{row['対象採点者']}</p>", unsafe_allow_html=True)
                    col2.markdown(f"<p style='{style_attr}'>{display_date}</p>", unsafe_allow_html=True)
                    col3.markdown(f"<div style='color: {font_color}; font-family: \"Meiryo\", sans-serif; font-weight: bold; font-size: 13px; white-space: normal; word-break: break-all; padding: 4px 0; line-height: 1.3;'>{display_title}</div>", unsafe_allow_html=True)
                    col4.markdown(f"<p style='{style_attr} text-align: center;'>{total_count}</p>", unsafe_allow_html=True)
                    col5.markdown(f"<p style='{style_attr} text-align: center;'>{graded_count}</p>", unsafe_allow_html=True)
                    col6.markdown(f"<p style='{style_attr} text-align: center;'>{unprocessed_count}</p>", unsafe_allow_html=True)
                    col7.markdown(f"<p style='{style_attr} text-align: center;'>{intervented_count}</p>", unsafe_allow_html=True)
                    
                    if col8.button(status_label, key=f"hold_start_btn_{index}", use_container_width=True):
                        st.session_state["hold_selected_grader"] = row['対象採点者']
                        st.session_state["hold_selected_response"] = row['問題']
                        st.session_state["hold_selected_comp_date"] = row['採点完了日']
                        st.session_state["hold_selected_row_index"] = 0
                        st.session_state["hold_current_step"] = "grading"
                        st.session_state["hold_initial_total_records"] = None
                        
                        st.session_state["saved_filter_active"] = st.session_state.get("filter_hold_active", True)
                        st.session_state["saved_filter_my_done"] = st.session_state.get("filter_hold_my_done", True)
                        st.session_state["saved_filter_others_done"] = st.session_state.get("filter_hold_others_done", True)
                        
                        st.rerun()
                   
                    st.markdown("<hr style='margin: 0.3em 0; border: 0; border-top: 1px solid #eee;'>", unsafe_allow_html=True)
            else:
                st.success(settings.LABELS["hold_no_data"])

        except Exception as e:
            st.error(settings.LABELS["hold_error"].format(error=e))
    # ==============================================================================
    # ✍️ 【ステップ2】保留レコードのみの個別再採点画面 (hold_current_step が "grading" のとき)
    # ==============================================================================
    elif st.session_state["hold_current_step"] == "grading":
        selected_grader = st.session_state.get("hold_selected_grader")
        selected_response = st.session_state.get("hold_selected_response")
        
        if "current_user_id" not in locals() or current_user_id is None:
            current_user_id = st.session_state.get("user_id", "UNKNOWN_USER")
        
        if selected_grader and selected_response:
            display_title = selected_response
            try:
                question = next((row for row in get_question_master(supabase) if row.get("response_id") == selected_response), None)
                title_val = question.get("question_title") if question else None
                if title_val and str(title_val).strip() != "":
                    display_title = str(title_val).strip()
            except Exception:
                pass

            st.subheader(settings.LABELS["hold_grading_title"].format(title=display_title))
            st.caption(settings.LABELS["hold_context"].format(grader=selected_grader, response=selected_response))

            def back_to_hold_list():
                st.components.v1.html("<script>window.parent.document.dispatchEvent(new CustomEvent('close_otehon_window'));</script>", height=0, width=0)
                st.session_state["hold_selected_grader"] = None
                st.session_state["hold_selected_response"] = None
                st.session_state["hold_selected_row_index"] = 0
                st.session_state["hold_initial_total_records"] = None
                st.session_state["hold_current_step"] = "select"
                st.rerun()

            if st.button(settings.LABELS["back_to_hold_list"], key="hold_back_to_list_btn"):
                back_to_hold_list()

            with st.spinner(settings.LABELS["hold_records_loading"]):
                raw_session_date = st.session_state.get("hold_selected_comp_date")
                formatted_comp_date = None
                if raw_session_date:
                    try: formatted_comp_date = pd.to_datetime(raw_session_date).strftime('%Y-%m-%d')
                    except Exception: formatted_comp_date = str(raw_session_date).strip()

                query = supabase.table("tbl_scoring_question_management").select("*").eq("checker_webid", selected_grader).eq("response_id", selected_response)
                if formatted_comp_date: query = query.eq("grading_comp_date", formatted_comp_date)
                detail_response = query.order("saiten_question_id", desc=False).execute()

            try:
                admin_ids = {str(u.get("grader_id")).strip() for u in get_grader_master(supabase) if u.get("role_id") == 4 and u.get("grader_id") is not None}
            except Exception:
                admin_ids = set()

            f_active = st.session_state.get("saved_filter_active", True)
            f_my_done = st.session_state.get("saved_filter_my_done", False)
            f_others_done = st.session_state.get("saved_filter_others_done", False)
            
            my_user_id_str = str(current_user_id).strip()
            all_rows = []
            first_hold_index = None
            
            if detail_response.data:
                for r in detail_response.data:
                    j_val = str(r.get("judge_mark_result", "")).strip().upper()
                    a_val = str(r.get("final_approver_id", "")).strip()
                    is_graded = j_val in ["O", "X", "*"]
                    
                    is_active_hold_row = (j_val == "H")
                    is_my_done_row = is_graded and (a_val == my_user_id_str)
                    is_others_done_row = is_graded and (a_val != my_user_id_str) and (a_val in admin_ids and a_val not in ["", "None", "null"])
                    
                    keep_this_row = False
                    if f_active and is_active_hold_row:
                        keep_this_row = True
                    if f_my_done and is_my_done_row:
                        keep_this_row = True
                    if f_others_done and is_others_done_row:
                        keep_this_row = True
                        
                    if keep_this_row:
                        all_rows.append(r)
                        if j_val == "H" and first_hold_index is None:
                            first_hold_index = len(all_rows) - 1
            if not all_rows or (len(all_rows) > 0 and st.session_state.get("hold_total_at_start") == 0):
                st.session_state["hold_initial_total_records"] = None

            if not all_rows:
                st.info(settings.LABELS["hold_target_missing"])
                st.session_state["hold_current_step"] = "select"
                st.rerun()
                return
        
            total_records = len(all_rows)
            
            if st.session_state.get("hold_initial_total_records") is None:
                st.session_state["hold_total_at_start"] = total_records
                st.session_state["hold_initial_total_records"] = True
                if first_hold_index is not None:
                    st.session_state["hold_selected_row_index"] = first_hold_index

            remaining_hold_count = len([r for r in all_rows if str(r.get("judge_mark_result", "")).strip().upper() == "H"])
                
            current_index = st.session_state.get("hold_selected_row_index", 0)
            current_index = max(0, min(current_index, total_records - 1))
            st.session_state["hold_selected_row_index"] = current_index

            current_row = all_rows[current_index]
            row_pkey = current_row.get("saiten_question_id")
            user_id_now = st.session_state.get("user_id")

            lock_check = supabase.table("tbl_scoring_question_management") \
                .select("is_locked, locked_by_webid, locked_at") \
                .eq("saiten_question_id", row_pkey) \
                .execute()
            
            db_is_locked = False
            db_locked_by = None
            db_locked_at = None
            if lock_check.data and len(lock_check.data) > 0:
                db_is_locked = lock_check.data[0].get("is_locked", False)
                db_locked_by = lock_check.data[0].get("locked_by_webid")
                db_locked_at = lock_check.data[0].get("locked_at")

            is_lock_expired = False
            if db_is_locked and db_locked_at:
                try:
                    db_ts = datetime.fromisoformat(str(db_locked_at).replace("Z", "+00:00")).timestamp()
                    if (time.time() - db_ts) > settings.LOGIN_TIMEOUT_SECONDS:
                        is_lock_expired = True
                except Exception:
                    pass

            is_currently_locked = db_is_locked and (str(db_locked_by).strip() != str(user_id_now).strip()) and (not is_lock_expired)

            st.divider()
          
            left_view, right_input = st.columns([5.0, 6.0])
            
            with left_view:
                st.markdown("### 📝 回答内容・情報")
                
                text_answer = current_row.get("answer", settings.LABELS["no_data_value"])
                text_cp1 = current_row.get("ai_cp1", settings.LABELS["no_data_value"])
                text_cp2 = current_row.get("ai_cp2", settings.LABELS["no_data_value"])
                text_cp3 = current_row.get("ai_cp3", settings.LABELS["no_data_value"])
                text_reason = current_row.get("ai_reason", settings.LABELS["no_data_value"])
                ai_judge_val = current_row.get("ai_judge_mark")
                current_judge = current_row.get("judge_mark_result")

                with st.container(border=True):
                    st.markdown("**【解答 (answer)】**")
                    clean_answer = str(text_answer).replace("\n", "  \n")
                    st.markdown(clean_answer)
                
                with st.container(border=True):
                    st.markdown(settings.LABELS["ai_checkpoint_1"])
                    st.write(text_cp1)
                    st.markdown(settings.LABELS["ai_checkpoint_2"])
                    st.write(text_cp2)
                    st.markdown(settings.LABELS["ai_checkpoint_3"])
                    st.write(text_cp3)

                with st.container(border=True):
                    st.markdown(settings.LABELS["ai_reason"])
                    clean_reason = str(text_reason).replace("\n", "  \n")
                    st.markdown(clean_reason)

                with st.container(border=True):
                    st.markdown(settings.LABELS["ai_result"])
                    if pd.isna(ai_judge_val) or str(ai_judge_val).strip() == "":
                        ai_status_html = f"<span style='background-color: #757575; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{settings.LABELS['status_no_data']}</span>"
                    elif ai_judge_val == "O":
                        ai_status_html = f"<span style='background-color: #1266F1; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{settings.LABELS['status_correct']}</span>"
                    elif ai_judge_val == "X":
                        ai_status_html = f"<span style='background-color: #DC3545; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{settings.LABELS['status_wrong']}</span>"
                    elif ai_judge_val == "*":
                        ai_status_html = f"<span style='background-color: #9e9e9e; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{settings.LABELS['status_none']}</span>"
                    else:
                        ai_status_html = f"<span style='background-color: #757575; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{ai_judge_val}</span>"
                    st.markdown(settings.LABELS["ai_judgement"].format(status=ai_status_html), unsafe_allow_html=True)
            # ==========================================================
            # 📥 右のエリア：正答画像、判定ボタン、メモ、レコード移動
            # ==========================================================
            with right_input:
                st.markdown(settings.LABELS["hold_input_title"])
                current_response_id = current_row.get("response_id")
                
                try:
                    master_response = supabase.table("mst_questions") \
                        .select("correct_image_file_name") \
                        .eq("response_id", current_response_id) \
                        .limit(1) \
                        .execute()
                        
                    if master_response.data and len(master_response.data) > 0:
                        first_row = next(iter(master_response.data), {})
                        file_name = first_row.get("correct_image_file_name")
                        
                        if file_name and str(file_name).strip() != "":
                            base_url = settings.STORAGE_BASE_URL
                            
                            if "object/sign/" in base_url:
                                base_url = base_url.replace("object/sign/", "object/public/")
                            if "object/authenticated/" in base_url:
                                base_url = base_url.replace("object/authenticated/", "object/public/")
                            
                            base_url = base_url.rstrip("/") + "/"
                            clean_file_name = str(file_name).strip()
                            
                            if "correct_image/" in base_url:
                                full_img_url = f"{base_url}{clean_file_name}"
                            else:
                                full_img_url = f"{base_url}correct_image/{clean_file_name}"
                            
                            st.markdown(settings.LABELS["correct_standard"])
                            st.markdown("<style>div.img-clickable-box img { max-height: 280px; object-fit: contain; width: 100%; border-radius: 4px; border: 1px solid #ddd; transition: opacity 0.2s; } div.img-clickable-box img:hover { opacity: 0.8; cursor: pointer; }</style>", unsafe_allow_html=True)
                            
                            html_preview = f"""
                            <div class="img-clickable-box">
                                <a href="{full_img_url}" target="_blank" title="別ウィンドウで拡大表示">
                                    <img src="{full_img_url}" />
                                </a>
                            </div>
                            """
                            st.markdown(html_preview, unsafe_allow_html=True)
                        else:
                            st.caption(settings.LABELS["image_missing"])
                    else:
                        st.caption(settings.LABELS["image_missing"])
                except Exception as img_err:
                    st.caption(settings.LABELS["image_load_skipped"].format(error=img_err))

                if is_currently_locked:
                    st.warning(settings.LABELS["hold_locked"].format(grader=db_locked_by))
                else:
                    st.write(settings.LABELS["hold_select_judgement"])

                # ─── 🎨 選ばれていないボタンの背景色をグレーにするCSS ───
                if pd.notna(current_judge) and str(current_judge).strip() != "":
                    tgt = str(current_judge).strip()
                    c_styles = "<style>"
                    if tgt != "O": c_styles += f"div[class*='st-key-h_score_O_'] button {{ background-color: #E0E0E0 !important; color: #888888 !important; opacity: 0.6 !important; }}"
                    if tgt != "X": c_styles += f"div[class*='st-key-h_score_X_'] button {{ background-color: #E0E0E0 !important; color: #888888 !important; opacity: 0.6 !important; }}"
                    if tgt != "*": c_styles += f"div[class*='st-key-h_score_N_'] button {{ background-color: #E0E0E0 !important; color: #888888 !important; opacity: 0.6 !important; }}"
                    
                    act_key = {"O": "h_score_O_", "X": "h_score_X_", "*": "h_score_N_"}.get(tgt)
                    if act_key: c_styles += f"div[class*='st-key-{act_key}'] button {{ border: 3px solid #111111 !important; font-weight: bold !important; box-shadow: 0px 4px 10px rgba(0,0,0,0.15) !important; }}"
                    c_styles += "</style>"
                    st.markdown(c_styles, unsafe_allow_html=True)
                
                # ─── ⌨️ 【移植】入力監視＆画像ウィンドウ一元管理JavaScript ───
                if not is_currently_locked:
                    js_shortcut = f"""
                    <script>
                    const doc = window.parent.document;
                    
                    if (window.scoringKeydownHandler) doc.removeEventListener('keydown', window.scoringKeydownHandler);
                    if (window.openOtehonHandler) doc.removeEventListener('open_otehon_image', window.openOtehonHandler);
                    if (window.closeOtehonHandler) doc.removeEventListener('close_otehon_window', window.closeOtehonHandler);
                    
                    if (!window.hasOwnProperty('otehonWindowRef')) {{
                        window.otehonWindowRef = null;
                    }}
                    
                    window.openOtehonHandler = function(e) {{
                        const url = e.detail;
                        if (window.otehonWindowRef && !window.otehonWindowRef.closed) {{
                            window.otehonWindowRef.location.href = url;
                            window.otehonWindowRef.focus();
                        }} else {{
                            window.otehonWindowRef = window.parent.open(url, 'otehon_secure_tab');
                        }}
                    }};
                    doc.addEventListener('open_otehon_image', window.openOtehonHandler);
                    
                    window.closeOtehonHandler = function() {{
                        if (window.otehonWindowRef && !window.otehonWindowRef.closed) {{
                            window.otehonWindowRef.close();
                            window.otehonWindowRef = null;
                        }}
                    }};
                    doc.addEventListener('close_otehon_window', window.closeOtehonHandler);
                    
                    window.scoringKeydownHandler = function(e) {{
                        if (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT') return;
                        if (e.repeat) return;
                        
                        let targetButton = null;
                        let judgeLabel = "";
                        const key = e.key;
                        const keyLower = key.toLowerCase();
                        
                        if (keyLower === 'o') {{
                            targetButton = doc.querySelector("div[class*='st-key-h_score_O_'] button");
                            judgeLabel = "🟢 正答(O)";
                        }} else if (keyLower === 'x') {{
                            targetButton = doc.querySelector("div[class*='st-key-h_score_X_'] button");
                            judgeLabel = "🔴 誤答(X)";
                        }} else if (key === ' ') {{
                            targetButton = doc.querySelector("div[class*='st-key-h_score_N_'] button");
                            judgeLabel = "⚪ 無答(*)";
                        }} else if (keyLower === 'n') {{
                            targetButton = doc.querySelector("div[class*='st-key-h_nav_next'] button") || doc.getElementById("h_nav_next");
                        }} else if (keyLower === 'b') {{
                            targetButton = doc.querySelector("div[class*='st-key-h_nav_prev'] button") || doc.getElementById("h_nav_prev");
                        }}
                        
                        if (targetButton) {{
                            e.preventDefault();
                            if (judgeLabel !== "") {{
                                const confirmed = window.confirm(`判定を「${{judgeLabel}}」で上書き登録して、次の問題に進みますか？\\n（Enterキーで確定）`);
                                if (confirmed) {{
                                    targetButton.click();
                                }}
                            }} else {{
                                targetButton.click();
                            }}
                        }}
                    }};
                    doc.addEventListener('keydown', window.scoringKeydownHandler);
                    </script>
                    """
                    st.components.v1.html(js_shortcut, height=0, width=0)
                # ⑤ 人間用の判定ボタン（等幅3列で配置）
                btn_cols = st.columns(3)
                selected_score = None
                
                if btn_cols[0].button(settings.LABELS["answer_score_correct"], key=f"h_score_O_{row_pkey}", use_container_width=True, disabled=is_currently_locked):
                    selected_score = "O"
                if btn_cols[1].button(settings.LABELS["answer_score_wrong"], key=f"h_score_X_{row_pkey}", use_container_width=True, disabled=is_currently_locked):
                    selected_score = "X"
                if btn_cols[2].button(settings.LABELS["answer_score_none"], key=f"h_score_N_{row_pkey}", use_container_width=True, disabled=is_currently_locked):
                    selected_score = "*"

                # ─── 📊 採点状況の右側に問題数を美しく配置（登録者名の追記版） ───
                if pd.isna(current_judge) or str(current_judge).strip() == "H":
                    status_html = f"<span style='background-color: #6f42c1; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{settings.LABELS['status_hold']}</span>"
                else:
                    status_html = f"<span style='background-color: #1266F1; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{settings.LABELS['confirmed_status'].format(status=current_judge)}</span>"
                
                # 💡 誰が登録した値なのかをマスタから逆引きしてテキストを生成
                raw_approver_id = current_row.get("final_approver_id")
                if pd.notna(raw_approver_id) and str(raw_approver_id).strip() not in ["", "None", "null"]:
                    approver_id_str = str(raw_approver_id).strip()
                    # マスタから名前を引く。なければIDをそのまま表示
                    approver_name = group_member_map.get(approver_id_str, approver_id_str)
                    if approver_name == approver_id_str:
                        approver_name = group_member_map.get(int(approver_id_str), approver_id_str) if approver_id_str.isdigit() else approver_id_str
                    
                    approver_html = f"<span style='font-size: 13px; color: #555555; margin-left: 8px; font-weight: bold;'>👤 確定者: {approver_name}</span>"
                else:
                    approver_html = "<span style='font-size: 13px; color: #888888; margin-left: 8px; font-style: italic;'>👤 確定者: なし（初期状態）</span>"

                st.write("")
                # 左右に分割して、左側に「状態＋確定者名」、右側に「〇問目」を表示します
                status_col1, status_col2 = st.columns([1.5, 1.0])
                with status_col1:
                    st.markdown(settings.LABELS["current_status"].format(status=f"{status_html}{approver_html}"), unsafe_allow_html=True)
                with status_col2:
                    st.markdown(f"<p style='margin:0; font-size:15px; font-weight:bold; color:#1266F1; line-height:1.8; text-align:right;'>📄 {current_index + 1}問目（{len(all_rows)}問中）</p>", unsafe_allow_html=True)
                st.write("")
                
                # ⑥ 採点メモ / コメント
                memo_input = st.text_area(
                    settings.LABELS["memo"],
                    value=current_row.get("memo", "") if current_row.get("memo") else "", 
                    key=f"h_memo_{row_pkey}",
                    disabled=is_currently_locked
                )

                st.markdown("---")
                
                st.write(settings.LABELS["navigation"])
                nav_col1, nav_col2, nav_col3, nav_col4, nav_col5, nav_col6, nav_col7 = st.columns([1.2, 1.8, 1.2, 1.2, 1.2, 1.8, 1.8])
                
                if nav_col1.button(settings.LABELS["first_record"], key="h_nav_first", use_container_width=True):
                    st.session_state["hold_selected_row_index"] = 0
                    st.rerun()

                if nav_col2.button(settings.LABELS["previous_unprocessed"], key="h_nav_prev_unprocessed", use_container_width=True):
                    prev_unprocessed = current_index
                    for i in range(current_index - 1, -1, -1):
                        if str(all_rows[i].get("judge_mark_result", "")).strip().upper() == "H":
                            prev_unprocessed = i
                            break
                    if prev_unprocessed == current_index:
                        for i in range(total_records - 1, current_index, -1):
                            if str(all_rows[i].get("judge_mark_result", "")).strip().upper() == "H":
                                prev_unprocessed = i
                                break
                    st.session_state["hold_selected_row_index"] = prev_unprocessed
                    st.rerun()

                if nav_col3.button(settings.LABELS["previous"], key="h_nav_prev", use_container_width=True):
                    if current_index > 0:
                        st.session_state["hold_selected_row_index"] = current_index - 1
                        st.rerun()

                nav_col4.markdown(
                    f"<div style='text-align: center; margin:0; line-height:1.2; color: #6f42c1; font-weight: bold; font-size:11px; padding-top:4px;'>"
                    f"保留問題残り： <span style='font-size: 16px; font-weight: 900;'>{remaining_hold_count}</span> 問<br>"
                    f"<span style='color: #888888;'>({current_index + 1}/{len(all_rows)})</span>"
                    f"</div>", 
                    unsafe_allow_html=True
                )
              
                if nav_col5.button(settings.LABELS["next"], key="h_nav_next", use_container_width=True):
                    if current_index < total_records - 1:
                        st.session_state["hold_selected_row_index"] = current_index + 1
                        st.rerun()

                if nav_col6.button(settings.LABELS["next_unprocessed"], key="h_nav_next_unprocessed", use_container_width=True):
                    next_unprocessed = current_index
                    for i in range(current_index + 1, total_records):
                        if str(all_rows[i].get("judge_mark_result", "")).strip().upper() == "H":
                            next_unprocessed = i
                            break
                    if next_unprocessed == current_index:
                        for i in range(0, current_index):
                            if str(all_rows[i].get("judge_mark_result", "")).strip().upper() == "H":
                                next_unprocessed = i
                                break
                    st.session_state["hold_selected_row_index"] = next_unprocessed
                    st.rerun()
                if nav_col7.button(settings.LABELS["admin_next"], key="h_nav_next_admin_task", use_container_width=True):
                    import time as time_module_admin
                    target_admin_index = None

                    admin_id_set = {
                        str(user.get("grader_id")).strip()
                        for user in get_grader_master(supabase)
                        if user.get("role_id") == 4 and user.get("grader_id") is not None
                    }

                    def is_role4_approver(val):
                        if pd.isna(val):
                            return False
                        v_str = str(val).strip()
                        return v_str in admin_id_set and v_str not in ["", "None", "null"]

                    for i in range(current_index + 1, total_records):
                        if is_role4_approver(all_rows[i].get("final_approver_id")):
                            target_admin_index = i
                            break
                    
                    if target_admin_index is None:
                        for i in range(0, current_index):
                            if is_role4_approver(all_rows[i].get("final_approver_id")):
                                target_admin_index = i
                                break
                    
                    if target_admin_index is not None:
                        st.session_state["hold_selected_row_index"] = target_admin_index
                        if target_admin_index < current_index:
                            st.warning(settings.LABELS["admin_jump"])
                            time_module_admin.sleep(0.8)
                        st.rerun()
                    else:
                        st.info(settings.LABELS["no_admin_record"])

            # ─── 🔄 新判定がクリックされたら自動でSupabaseへ上書きコミット ───
            if selected_score is not None:
                try:
                    is_last_record = (current_index >= total_records - 1)

                    with st.spinner(settings.LABELS["hold_saving"]):
                        approver_id = current_user_id
                        hold_comp_date_val = st.session_state.get("hold_selected_comp_date")
                        
                        query = supabase.table("tbl_scoring_question_management") \
                            .update({
                                "judge_mark_result": selected_score,
                                "final_approver_id": approver_id,
                                "memo": memo_input
                            }) \
                            .eq("saiten_question_id", row_pkey)
                        
                        if hold_comp_date_val:
                            query = query.eq("grading_comp_date", hold_comp_date_val)
                            
                        query.execute()
                    
                    try:
                        # 🛠️ 【NameError修正】元の状態を current_judge から安全に文字列変換してログを挿入
                        old_status = str(current_judge).strip() if pd.notna(current_judge) else "H"
                        insert_operation_log(
                            supabase=supabase,
                            operator_id=approver_id,
                            action_type="HOLD_RELEASE",
                            target_id=str(row_pkey),
                            description=f"管理者による保留判定の確定・修正（問題ID: {selected_response}, 元の状態: {old_status} -> 新判定: {selected_score}）。"
                        )
                    except Exception:
                        pass

                    if not is_last_record:
                        st.toast(settings.LABELS["hold_save_success"].format(score=selected_score), icon="✅")
                        st.session_state["hold_selected_row_index"] = current_index + 1
                        st.rerun()
                    else:
                        # 💡 【移植】最後の問題を解き終えた際の2ボタン確認ダイアログ
                        @st.dialog(settings.LABELS["hold_finish_title"])
                        def show_hold_finish_dialog():
                            st.markdown(settings.LABELS["hold_finish_heading"])
                            st.write(settings.LABELS["hold_finish_message"])
                            st.write(settings.LABELS["hold_finish_hint"])
                            st.write("")
                            
                            with st.form(key="hold_finish_confirm_2btn_form", border=False):
                                submit_btn = st.form_submit_button(settings.LABELS["hold_finish_submit"], use_container_width=True)
                                if submit_btn:
                                    st.balloons()
                                    back_to_hold_list()
                            
                            if st.button(settings.LABELS["hold_review_again"], use_container_width=True):
                                st.rerun()
                        
                        show_hold_finish_dialog()
                    
                except Exception as e:
                    st.error(settings.LABELS["hold_update_error"].format(error=e))
