import time
from datetime import date, datetime, timedelta, timezone
import pandas as pd
import streamlit as st
from zoneinfo import ZoneInfo

try:
    from master_cache import clear_master_cache, get_question_master
except ImportError:
    from views.master_cache import clear_master_cache, get_question_master

# ==========================================================
# 💡 負荷対策：問題マスタの取得を2分間キャッシュ化する
# ==========================================================
@st.cache_data(ttl=120)
def fetch_cached_question_master(_supabase):
    """問題マスタをキャッシュ取得（20人同時アクセス時のDBパンク防止）"""
    try:
        return get_question_master(_supabase)
    except Exception:
        return []

def show_question_list(supabase, settings, current_user_id, current_role_id):
    """
    📄 タブ1: レスポンス識別子別集計(採点者画面・完全最適化同期版)
    ステップ1(問題一覧)と、ステップ2(個別採点)を制御します。
    """
    # --- 🔑 画面遷移用の状態変数を初期化 ---
    if "current_step" not in st.session_state:
        st.session_state["current_step"] = "select"

    # ==========================================================
    # 📄 ステップ1: 問題一覧画面 (current_step が "select" のとき)
    # ==========================================================
    if st.session_state["current_step"] == "select":
        st.header(settings.LABELS["tab1_header"])
        if st.button(settings.LABELS["refresh_button"], key="refresh_btn"):
            st.cache_data.clear() # キャッシュも一括クリア
            clear_master_cache()
            st.rerun()

        try:
            with st.spinner(settings.LABELS["loading_data"]):
                # 💡 自分宛てデータをロード
                response = supabase.table("tbl_scoring_question_management") \
                    .select("checker_webid, response_id, judge_mark_result, grading_comp_date, ai_judge_mark, is_locked, locked_by_webid, locked_at") \
                    .eq("checker_webid", current_user_id) \
                    .not_.is_("ai_judge_mark", "null") \
                    .execute()
                
                # キャッシュ化された問題マスタから一括取得
                master_data = fetch_cached_question_master(supabase)
                question_title_map = {row["response_id"]: row.get("question_title", "") for row in master_data if row.get("response_id")}
            
            if response.data:
                df_data = pd.DataFrame(response.data)
                
                # 日付の表記揺れ・型ズレを「YYYY-MM-DD」に一撃統一
                if "grading_comp_date" in df_data.columns:
                    df_data["grading_comp_date"] = pd.to_datetime(df_data["grading_comp_date"], errors='coerce').dt.strftime('%Y-%m-%d')

                # 進捗集計フラグの高速算出
                df_data["graded_filled"] = df_data["judge_mark_result"].str.strip().isin(["O", "X", "*"])
                df_data["hold_filled"] = df_data["judge_mark_result"].str.strip() == "H"
                df_data["ungraded_filled"] = df_data["judge_mark_result"].isna() | (df_data["judge_mark_result"].str.strip() == "")
                # 3軸グループ化ロジック
                df_summary = (
                    df_data
                    .groupby(["checker_webid", "grading_comp_date", "response_id"], dropna=False)
                    .agg(
                        採点数=("response_id", "size"),
                        未採点数=("ungraded_filled", "sum"),
                        採点済数=("graded_filled", "sum"),
                        保留数=("hold_filled", "sum"),
                    )
                    .reset_index()
                )
                df_summary.columns = ['採点者ID', '採点完了日', '問題ID', '総数', '未採点', '採点済', '保留']

                # 🔍 表示フィルターUI（横並びに綺麗に配置）
                st.markdown(settings.LABELS["display_filter"])
                filter_col1, filter_col2 = st.columns([1.0, 1.0])
                
                with filter_col1:
                    filter_active = st.checkbox(settings.LABELS["question_filter_active"], value=False, key="main_list_filter_active")
                with filter_col2:
                    # 💡 採点完了日超過のトグル（デフォルトOFF = 超過は非表示）
                    show_expired = st.checkbox(settings.LABELS["question_filter_expired"], value=False, key="main_list_filter_expired")

                # 🧮 フィルター1: 未採点・保留ありによる絞り込み
                if filter_active:
                    df_summary = df_summary[(df_summary['未採点'] > 0) | (df_summary['保留'] > 0)]

                # 🧮 フィルター2: 採点完了日超過データの絞り込み（当日より過去のものを制御）
                if not show_expired and not df_summary.empty:
                    import datetime as dt_module
                    today_str = dt_module.date.today().strftime("%Y-%m-%d")
                    # 当日以降（未来）または日付が空（未定）のもののみを残す
                    is_future_or_empty = (
                        (df_summary['採点完了日'].isna()) | 
                        (df_summary['採点完了日'].astype(str).str.strip() == "") |
                        (df_summary['採点完了日'] >= today_str)
                    )
                    df_summary = df_summary[is_future_or_empty]

                if df_summary.empty:
                    st.success(settings.LABELS["question_no_filtered_data"])
                    return

                # 完了日の昇順で完全整列（優先順位：採点完了日 ➔ 問題ID）
                df_summary = df_summary.sort_values(by=['採点完了日', '問題ID'], ascending=[True, True])

                st.metric(settings.LABELS["question_count"], len(df_summary))
                st.write("")

                # ─── 📊 グリッドヘッダー描画 ───
                h_col1, h_col2, h_col3, h_col4, h_col5, h_col6, h_col7, h_col8 = st.columns([1.5, 1.5, 3.2, 0.8, 0.8, 0.8, 0.8, 1.5])
                h_col1.markdown("**担当採点者**")
                h_col2.markdown("**採点完了日**")
                h_col3.markdown("**問題**")
                h_col4.markdown("**総数**")
                h_col5.markdown(f"**{settings.LABELS['col_ungraded']}**")
                h_col6.markdown("**採点済**")
                h_col7.markdown(f"**{settings.LABELS['hold_count']}**")
                h_col8.markdown("**操作**")
                st.divider()

                # ─── 🔄 行ループ描画 ───
                for index, row in df_summary.iterrows():
                    col1, col2, col3, col4, col5, col6, col7, col8 = st.columns([1.5, 1.5, 3.2, 0.8, 0.8, 0.8, 0.8, 1.5])
                    
                    unprocessed_count = int(row['未採点'])
                    graded_count = int(row['採点済'])
                    hold_count = int(row['保留'])
                    comp_date_val = row['採点完了日']
                    display_date = "（未設定）" if pd.isna(comp_date_val) else str(comp_date_val).strip()
                    
                    current_response_id = row['問題ID']
                    display_title = question_title_map.get(current_response_id, current_response_id)
                    if not display_title or str(display_title).strip() == "":
                        display_title = current_response_id
                        
                    if unprocessed_count > 0 or hold_count > 0:
                        font_color = "#DC3545"
                        status_label = "✍️ 採点開始"
                    else:
                        font_color = "#1266F1"
                        status_label = "🔍 閲覧確認"
                        
                    style_attr = f"color: {font_color}; font-family: 'Meiryo', sans-serif; font-weight: bold; font-size: 14px; margin: 0; padding: 4px 0;"
                    
                    col1.markdown(f"<p style='{style_attr}'>{row['採点者ID']}</p>", unsafe_allow_html=True)
                    col2.markdown(f"<p style='{style_attr}'>{display_date}</p>", unsafe_allow_html=True)
                    col3.markdown(f"<div style='color: {font_color}; font-family: \"Meiryo\", sans-serif; font-weight: bold; font-size: 13px; white-space: normal; word-break: break-all; padding: 4px 0; line-height: 1.3;'>{display_title}</div>", unsafe_allow_html=True)
                    col4.markdown(f"<p style='{style_attr} text-align: center;'>{row['総数']}</p>", unsafe_allow_html=True)
                    col5.markdown(f"<p style='{style_attr} text-align: center;'>{unprocessed_count}</p>", unsafe_allow_html=True)
                    col6.markdown(f"<p style='{style_attr} text-align: center;'>{graded_count}</p>", unsafe_allow_html=True)
                    col7.markdown(f"<p style='{style_attr} text-align: center;'>{hold_count}</p>", unsafe_allow_html=True)
                    
                    if col8.button(status_label, key=f"main_start_btn_{index}", use_container_width=True):
                        st.session_state["selected_grader"] = row['採点者ID']
                        st.session_state["selected_response"] = row['問題ID']
                        st.session_state["selected_comp_date"] = row['採点完了日']
                        st.session_state["selected_row_index"] = 0
                        st.session_state["current_step"] = "grading"
                        st.rerun()
                    
                    st.markdown("<hr style='margin: 0.3em 0; border: 0; border-top: 1px solid #eee;'>", unsafe_allow_html=True)
            else:
                st.info(settings.LABELS["question_no_data"])

        except Exception as e:
            st.error(settings.LABELS["question_list_error"].format(error=e))
    # ==========================================================
    # ✍️ ステップ2: 1レコードずつの個別採点画面 (current_step が "grading" のとき)
    # ==========================================================
    elif st.session_state["current_step"] == "grading":
        selected_grader = st.session_state.get("selected_grader")
        selected_response = st.session_state.get("selected_response")
        selected_comp_date = st.session_state.get("selected_comp_date")
        
        if selected_grader and selected_response:
            display_title = selected_response
            try:
                # 💡 修正：キャッシュ化されたマスタから安全に1件抽出（連打時のDBパンク防止）
                cached_questions = fetch_cached_question_master(supabase)
                question = next(
                    (row for row in cached_questions if row.get("response_id") == selected_response),
                    None,
                )
                title_val = question.get("question_title") if question else None
                if title_val and str(title_val).strip() != "":
                    display_title = str(title_val).strip()
            except Exception:
                pass

            st.subheader(settings.LABELS["question_grading_title"].format(title=display_title))
            st.caption(settings.LABELS["question_context"].format(grader=selected_grader, response=selected_response, date=selected_comp_date))

            if st.button(settings.LABELS["back_to_question_list"], key="back_to_list_btn"):
                st.session_state["selected_grader"] = None
                st.session_state["selected_response"] = None
                st.session_state["selected_comp_date"] = None
                st.session_state["selected_row_index"] = 0
                st.session_state["current_step"] = "select"
                st.rerun()

            # 大元の最新データをSupabaseから安全にロード
            with st.spinner(settings.LABELS["loading_question_records"]):
                detail_response = supabase.table("tbl_scoring_question_management") \
                    .select("*") \
                    .eq("checker_webid", selected_grader) \
                    .eq("response_id", selected_response) \
                    .eq("grading_comp_date", selected_comp_date) \
                    .order("saiten_question_id", desc=False) \
                    .execute()

            detail_rows = detail_response.data or []
            
            if not detail_rows:
                st.warning(settings.LABELS["question_records_not_found"])
                st.session_state["current_step"] = "select"
                st.rerun()
            else:
                total_records = len(detail_rows)
                current_index = st.session_state.get("selected_row_index", 0)
                current_index = max(0, min(current_index, total_records - 1))
                st.session_state["selected_row_index"] = current_index

                current_row = detail_rows[current_index]
                row_pkey = current_row.get("saiten_question_id")
                login_user_id = st.session_state.get("user_id")

                # 🚨【自爆防止型・悲観的ロックリアルタイムチェック】
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

                is_currently_conflict = db_is_locked and (str(db_locked_by).strip() != str(login_user_id).strip()) and (not is_lock_expired)

                if is_currently_conflict:
                    st.error(settings.LABELS["question_locked"].format(grader=db_locked_by))
                    st.info(settings.LABELS["question_locked_readonly"])
                    is_admin_locked = True
                else:
                    is_admin_locked = False

                st.divider()
                
                # 📄 画面を左右等幅に分割
                left_view, right_input = st.columns([5.0, 6.0])

                # ==========================================================
                # 📝 左のエリア：現在の採点状況、解答、AIチェック
                # ==========================================================
                with left_view:
                    st.markdown(settings.LABELS["answer_information"])
                    
                    text_answer = current_row.get("answer", settings.LABELS["no_data_value"])
                    text_cp1 = current_row.get("ai_cp1", settings.LABELS["no_data_value"])
                    text_cp2 = current_row.get("ai_cp2", settings.LABELS["no_data_value"])
                    text_cp3 = current_row.get("ai_cp3", settings.LABELS["no_data_value"])
                    text_reason = current_row.get("ai_reason", settings.LABELS["no_data_value"])
                    ai_judge_val = current_row.get("ai_judge_mark")
                    current_judge = current_row.get("judge_mark_result")
                    
                    with st.container(border=True):
                        st.markdown(settings.LABELS["answer_label"])
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
                    st.markdown(settings.LABELS["grading_input"])
                    current_response_id = current_row.get("response_id")
                    
                    try:
                        # 💡 修正：キャッシュ化されたマスタから安全に抽出
                        cached_questions = fetch_cached_question_master(supabase)
                        master_row = next(
                            (row for row in cached_questions if row.get("response_id") == current_response_id),
                            {},
                        )
                        file_name = master_row.get("correct_image_file_name")

                        if file_name and str(file_name).strip() != "":
                            # 💡 修正：Privateバケット高負荷対応用の「署名付きURL」発行処理
                            bucket_name = "correct_image"
                            try:
                                res_url = supabase.storage.from_(bucket_name).create_signed_url(str(file_name).strip(), 60)
                                full_img_url = res_url.get("signedURL") or res_url.get("signedUrl")
                            except Exception:
                                full_img_url = f"{settings.STORAGE_BASE_URL}{file_name}"

                            st.markdown(settings.LABELS["correct_image"])
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
                    except Exception as img_err:
                        st.caption(settings.LABELS["image_load_skipped"].format(error=img_err))
                 
                    st.write("")

                    db_approver = current_row.get("final_approver_id")
                    has_approver = pd.notna(db_approver) and str(db_approver).strip() != "" and str(db_approver).lower() not in ["none", "null"]
                    is_admin_locked = has_approver and str(db_approver).strip() != str(st.session_state.get("user_id")).strip()
                    
                    if pd.isna(current_judge) or str(current_judge).strip() == "":
                        status_html = f"<span style='background-color: #757575; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{settings.LABELS['ungraded_status']}</span>"
                    elif current_judge == "O":
                        status_html = f"<span style='background-color: #1266F1; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{settings.LABELS['status_correct']}</span>"
                    elif current_judge == "X":
                        status_html = f"<span style='background-color: #DC3545; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{settings.LABELS['status_wrong']}</span>"
                    elif current_judge == "*":
                        status_html = f"<span style='background-color: #9e9e9e; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{settings.LABELS['status_none']}</span>"
                    elif current_judge == "H":
                        status_html = f"<span style='background-color: #6f42c1; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{settings.LABELS['status_hold']}</span>"
                    else:
                        status_html = f"<span style='background-color: #757575; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{current_judge}</span>"

                    current_num = current_index + 1
                    total_num = total_records
                    
                    status_col1, status_col2 = st.columns([1.0, 1.0])
                    with status_col1:
                        st.markdown(settings.LABELS["current_status"].format(status=status_html), unsafe_allow_html=True)
                    with status_col2:
                        st.markdown(f"<p style='margin:0; font-size:15px; font-weight:bold; color:#1266F1; line-height:1.8; text-align:right;'>📄 {current_num}問目（{total_num}問中）</p>", unsafe_allow_html=True)
                    
                    st.write("")
                    if is_admin_locked:
                        st.warning(settings.LABELS["admin_locked"])
                    else:
                        st.write(settings.LABELS["select_judgement"])
                    
                    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns(4)
                    selected_score = None

                    if pd.notna(current_judge) and str(current_judge).strip() != "":
                        tgt = str(current_judge).strip()
                        c_styles = "<style>"
                        if tgt != "O": c_styles += f"div[class*='st-key-ans_true_'] button {{ background-color: #E0E0E0 !important; color: #888888 !important; opacity: 0.6 !important; }}"
                        if tgt != "X": c_styles += f"div[class*='st-key-ans_false_'] button {{ background-color: #E0E0E0 !important; color: #888888 !important; opacity: 0.6 !important; }}"
                        if tgt != "*": c_styles += f"div[class*='st-key-ans_none_'] button {{ background-color: #E0E0E0 !important; color: #888888 !important; opacity: 0.6 !important; }}"
                        if tgt != "H": c_styles += f"div[class*='st-key-ans_hold_'] button {{ background-color: #E0E0E0 !important; color: #888888 !important; opacity: 0.6 !important; }}"
                        act_key = {"O": "ans_true_", "X": "ans_false_", "*": "ans_none_", "H": "ans_hold_"}.get(tgt)
                        if act_key: c_styles += f"div[class*='st-key-{act_key}'] button {{ border: 3px solid #111111 !important; font-weight: bold !important; box-shadow: 0px 4px 10px rgba(0,0,0,0.15) !important; }}"
                        c_styles += "</style>"
                        st.markdown(c_styles, unsafe_allow_html=True)

                    if not is_admin_locked:
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
                                targetButton = doc.querySelector("div[class*='st-key-ans_true_'] button");
                                judgeLabel = "🟢 正答(O)";
                            }} else if (keyLower === 'x') {{
                                targetButton = doc.querySelector("div[class*='st-key-ans_false_'] button");
                                judgeLabel = "🔴 誤答(X)";
                            }} else if (key === ' ') {{
                                targetButton = doc.querySelector("div[class*='st-key-ans_none_'] button");
                                judgeLabel = "⚪ 無答(*)";
                            }} else if (keyLower === 'h') {{
                                targetButton = doc.querySelector("div[class*='st-key-ans_hold_'] button");
                                judgeLabel = "🟡 保留(H)";
                            }} else if (keyLower === 'n') {{
                                targetButton = doc.querySelector("div[class*='st-key-next_detail_btn'] button") || doc.getElementById("next_detail_btn");
                            }} else if (keyLower === 'b') {{
                                targetButton = doc.querySelector("div[class*='st-key-prev_detail_btn'] button") || doc.getElementById("prev_detail_btn");
                            }}
                            
                            if (targetButton) {{
                                e.preventDefault();
                                if (judgeLabel !== "") {{
                                    const confirmed = window.confirm(`判定を「${{judgeLabel}}」で登録して、次の問題に進みますか？\\n（Enterキーで確定）`);
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
                    if btn_col1.button(settings.LABELS["answer_score_correct"], key=f"ans_true_{row_pkey}", use_container_width=True, disabled=is_admin_locked):
                        selected_score = "O"
                    if btn_col2.button(settings.LABELS["answer_score_wrong"], key=f"ans_false_{row_pkey}", use_container_width=True, disabled=is_admin_locked):
                        selected_score = "X"
                    if btn_col3.button(settings.LABELS["answer_score_none"], key=f"ans_none_{row_pkey}", use_container_width=True, disabled=is_admin_locked):
                        selected_score = "*"
                    if btn_col4.button(settings.LABELS["answer_score_hold"], key=f"ans_hold_{row_pkey}", use_container_width=True, disabled=is_admin_locked):
                        selected_score = "H"

                    st.write("")

                    memo_input = st.text_area(
                        settings.LABELS["memo"],
                        value=current_row.get("memo", "") if current_row.get("memo") else "",
                        key=f"memo_text_{row_pkey}",
                        disabled=is_admin_locked
                    )
                    st.markdown("---")
                    
                    st.write("📂 **レコード移動・ナビゲーション**")
                    nav_col1, nav_col2, nav_col3, nav_col4, nav_col5, nav_col6 = st.columns([2.5, 2.2, 1.5, 1.5, 1.5, 2.2])
                    
                    if nav_col1.button(settings.LABELS["first_record_short"], key="first_detail_btn", use_container_width=True):
                        if current_index > 0:
                            st.session_state["selected_row_index"] = 0
                            st.rerun()

                    if nav_col2.button(settings.LABELS["previous_unprocessed_short"], key="prev_unprocessed_btn", use_container_width=True):
                        import time as time_module_prev
                        target_index_prev = None
                        for i in range(current_index - 1, -1, -1):
                            r_judge = detail_rows[i].get("judge_mark_result")
                            if pd.isna(r_judge) or str(r_judge).strip() in ["", "H"]:
                                target_index_prev = i
                                break
                        if target_index_prev is not None:
                            st.session_state["selected_row_index"] = target_index_prev
                            st.rerun()
                        else:
                            for i in range(total_records - 1, current_index, -1):
                                r_judge = detail_rows[i].get("judge_mark_result")
                                if pd.isna(r_judge) or str(r_judge).strip() in ["", "H"]:
                                    target_index_prev = i
                                    break
                            if target_index_prev is not None:
                                st.session_state["selected_row_index"] = target_index_prev
                                st.warning(settings.LABELS["question_previous_wrap"])
                                time_module_prev.sleep(1.0)
                                st.rerun()
                            else:
                                st.info(settings.LABELS["question_previous_none"])

                    if nav_col3.button("◀ 前へ", key="prev_detail_btn", use_container_width=True):
                        if current_index > 0:
                            st.session_state["selected_row_index"] = current_index - 1
                            st.rerun()

                    nav_col4.markdown(f"<p style='text-align: center; margin:0; font-weight:bold; font-size:14px; line-height:2.4;'>{current_index + 1}/{total_records}</p>", unsafe_allow_html=True)
                    
                    if nav_col5.button("次へ ▶", key="next_detail_btn", use_container_width=True):
                        if current_index < total_records - 1:
                            st.session_state["selected_row_index"] = current_index + 1
                            st.rerun()

                    if nav_col6.button(settings.LABELS["next_unprocessed_short"], key="next_unprocessed_btn", use_container_width=True):
                        import time as time_module_next
                        target_index_next = None
                        for i in range(current_index + 1, total_records):
                            r_judge = detail_rows[i].get("judge_mark_result")
                            if pd.isna(r_judge) or str(r_judge).strip() in ["", "H"]:
                                target_index_next = i
                                break
                        if target_index_next is not None:
                            st.session_state["selected_row_index"] = target_index_next
                            st.rerun()
                        else:
                            for i in range(0, current_index):
                                r_judge = detail_rows[i].get("judge_mark_result")
                                if pd.isna(r_judge) or str(r_judge).strip() in ["", "H"]:
                                    target_index_next = i
                                    break
                            if target_index_next is not None:
                                st.session_state["selected_row_index"] = target_index_next
                                st.warning(settings.LABELS["question_next_wrap"])
                                time_module_next.sleep(1.0)
                                r_judge = detail_rows[i].get("judge_mark_result")
                                st.rerun()
                            else:
                                st.info(settings.LABELS["question_next_none"])

                # ─── 🔄 いずれかのボタンが押されたら自動でSupabaseへ保存 ───
                if selected_score is not None:
                    try:
                        is_last_record = (current_index >= total_records - 1)

                        with st.spinner("Supabaseに保存中..."):
                            approver_id = st.session_state.get("user_id")
                            
                            supabase.table("tbl_scoring_question_management") \
                                .update({
                                    "judge_mark_result": selected_score,
                                    "final_approver_id": approver_id,
                                    "memo": memo_input
                                }) \
                                .eq("saiten_question_id", row_pkey) \
                                .eq("grading_comp_date", selected_comp_date) \
                                .execute()
                        
                        if not is_last_record:
                            st.toast(settings.LABELS["question_save_success"].format(score=selected_score), icon="✅")
                            st.session_state["selected_row_index"] = current_index + 1
                            st.rerun()
                        else:
                            @st.dialog(settings.LABELS["question_finish_title"])
                            def show_finish_dialog():
                                st.markdown(settings.LABELS["question_finish_heading"])
                                st.write(settings.LABELS["question_finish_message"])
                                st.write("")
                                
                                with st.form(key="finish_confirm_2btn_form", border=False):
                                    submit_btn = st.form_submit_button(settings.LABELS["question_finish_submit"], use_container_width=True)
                                    if submit_btn:
                                        st.balloons()
                                        st.session_state["selected_grader"] = None
                                        st.session_state["selected_response"] = None
                                        st.session_state["selected_comp_date"] = None
                                        st.session_state["selected_row_index"] = 0
                                        st.session_state["current_step"] = "select"
                                        st.rerun()
                                
                                if st.button(settings.LABELS["question_review_again"], use_container_width=True):
                                    st.rerun()
                            
                            show_finish_dialog()
                        
                    except Exception as e:
                        st.error(settings.LABELS["question_update_error"].format(error=e))
