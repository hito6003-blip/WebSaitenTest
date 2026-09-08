import streamlit as st
import pandas as pd
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo
import time

try:
    from master_cache import clear_master_cache, get_question_master
except ImportError:
    from views.master_cache import clear_master_cache, get_question_master

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
            clear_master_cache()
            st.rerun()

        try:
            with st.spinner("データを受信中..."):
                # 💡 PostgreSQL側で「自分宛て」かつ「AI判定が登録済」のものだけをピンポイント水際ロード！
                response = supabase.table("tbl_scoring_question_management") \
                    .select("checker_webid, response_id, judge_mark_result, grading_comp_date, ai_judge_mark, is_locked, locked_by_webid, locked_at") \
                    .eq("checker_webid", current_user_id) \
                    .not_.is_("ai_judge_mark", "null") \
                    .execute()
                
                # 問題マスタから一括取得
                master_data = get_question_master(supabase)
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

                # 💡【UI改善】未採点や保留が残っている「要対応」のみに絞り込む簡易フィルター
                st.markdown("##### 🔍 表示フィルター")
                filter_active = st.checkbox("⏳ 未採点・保留ありのグループのみ表示", value=False, key="main_list_filter_active")
                if filter_active:
                    df_summary = df_summary[(df_summary['未採点'] > 0) | (df_summary['保留'] > 0)]

                if df_summary.empty:
                    st.success("✨ 現在、対応が必要な採点対象データはありません！すべて処理完了しています。")
                    return

                # 完了日の昇順で完全整列
                df_summary = df_summary.sort_values(by=['採点完了日', '問題ID'], ascending=[True, True])

                st.metric("表示中の問題パターン数", len(df_summary))
                st.write("")

                # ─── 📊 グリッドヘッダー描画（採点済カラムを1列追加して等幅調整） ───
                h_col1, h_col2, h_col3, h_col4, h_col5, h_col6, h_col7, h_col8 = st.columns([1.5, 1.5, 3.2, 0.8, 0.8, 0.8, 0.8, 1.5])
                h_col1.markdown("**担当採点者**")
                h_col2.markdown("**採点完了日**")
                h_col3.markdown("**問題**")
                h_col4.markdown("**総数**")
                h_col5.markdown("**未採点**")
                h_col6.markdown("**採点済**") # 💡追加
                h_col7.markdown("**保留**")
                h_col8.markdown("**操作**")
                st.divider()

                # ─── 🔄 行ループ描画 ───
                for index, row in df_summary.iterrows():
                    col1, col2, col3, col4, col5, col6, col7, col8 = st.columns([1.5, 1.5, 3.2, 0.8, 0.8, 0.8, 0.8, 1.5])
                    
                    unprocessed_count = int(row['未採点'])
                    graded_count = int(row['採点済']) # 💡取得
                    hold_count = int(row['保留'])
                    comp_date_val = row['採点完了日']
                    display_date = "（未設定）" if pd.isna(comp_date_val) else str(comp_date_val).strip()
                    
                    current_response_id = row['問題ID']
                    display_title = question_title_map.get(current_response_id, current_response_id)
                    if not display_title or str(display_title).strip() == "":
                        display_title = current_response_id
                        
                    # 未採点や保留があれば警告の赤、完了していれば安心の青
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
                    col6.markdown(f"<p style='{style_attr} text-align: center;'>{graded_count}</p>", unsafe_allow_html=True) # 💡採点済数を正しく表示
                    col7.markdown(f"<p style='{style_attr} text-align: center;'>{hold_count}</p>", unsafe_allow_html=True)
                    
                    # 🚀 各ボタンにセッションを安全に同期させてステップ2（grading）へ突入
                    if col8.button(status_label, key=f"main_start_btn_{index}", use_container_width=True):
                        st.session_state["selected_grader"] = row['採点者ID']
                        st.session_state["selected_response"] = row['問題ID']
                        st.session_state["selected_comp_date"] = row['採点完了日']
                        st.session_state["selected_row_index"] = 0
                        st.session_state["current_step"] = "grading"
                        st.rerun()
                    
                    st.markdown("<hr style='margin: 0.3em 0; border: 0; border-top: 1px solid #eee;'>", unsafe_allow_html=True)
            else:
                st.info("💡 現在、データベース内に有効な採点対象レコードが割り当てられていません。")

        except Exception as e:
            st.error(f"一覧取得エラーが発生しました: {e}")

    # ==========================================================
    # ✍️ ステップ2: 1レコードずつの個別採点画面 (current_step が "grading" のとき)
    # ==========================================================
    elif st.session_state["current_step"] == "grading":
        selected_grader = st.session_state.get("selected_grader")
        selected_response = st.session_state.get("selected_response")
        selected_comp_date = st.session_state.get("selected_comp_date")  # ✨新設キーのロード
        
        if selected_grader and selected_response:
            display_title = selected_response
            try:
                question = next(
                    (row for row in get_question_master(supabase) if row.get("response_id") == selected_response),
                    None,
                )
                title_val = question.get("question_title") if question else None
                if title_val and str(title_val).strip() != "":
                    display_title = str(title_val).strip()
            except Exception:
                pass

            st.subheader(f"🔍 採点中: {display_title}")
            st.caption(f"担当採点者: {selected_grader} | 問題ID: {selected_response} | 採点完了日: {selected_comp_date}")

            if st.button("⬅️ 問題一覧に戻る", key="back_to_list_btn"):
                st.session_state["selected_grader"] = None
                st.session_state["selected_response"] = None
                st.session_state["selected_comp_date"] = None  # クリア
                st.session_state["selected_row_index"] = 0
                st.session_state["current_step"] = "select"
                st.rerun()

            # 💡 大元の最新データをSupabaseから安全にロードする処理
            with st.spinner("採点対象データを読み込み中..."):
                detail_response = supabase.table("tbl_scoring_question_management") \
                    .select("*") \
                    .eq("checker_webid", selected_grader) \
                    .eq("response_id", selected_response) \
                    .eq("grading_comp_date", selected_comp_date) \
                    .order("saiten_question_id", desc=False) \
                    .execute()

            detail_rows = detail_response.data or []
            
            if not detail_rows:
                st.warning("採点対象のレコードが見つかりませんでした。")
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
                    st.error(f"⚠️ この問題は現在、別の採点者（ID: {db_locked_by}）が画面を開いて採点中のため、ロックされています。")
                    st.info("💡 内容の閲覧は可能ですが、判定ボタンの操作やメモの書き込みはバッティング防止のため制限されます。")
                    is_admin_locked = True
                else:
                    is_admin_locked = False

                st.divider()
                
                # 📄 画面を左右等幅に美しく分割（左：データ表示、右：画像・操作入力）
                left_view, right_input = st.columns([5.0,6.0])

                # ==========================================================
                # 📝 左のエリア：現在の採点状況、解答、AIチェック1〜3、AI判断理由、AI結果
                # ==========================================================
                with left_view:
                    st.markdown("### 📝 回答内容・情報")
                    
                    text_answer = current_row.get("answer", "（データなし）")
                    text_cp1 = current_row.get("ai_cp1", "（データなし）")
                    text_cp2 = current_row.get("ai_cp2", "（データなし）")
                    text_cp3 = current_row.get("ai_cp3", "（データなし）")
                    text_reason = current_row.get("ai_reason", "（データなし）")
                    ai_judge_val = current_row.get("ai_judge_mark")
                    current_judge = current_row.get("judge_mark_result")
                    
                    with st.container(border=True):
                        st.markdown("**【解答 (answer)】**")
                        clean_answer = str(text_answer).replace("\n", "  \n")
                        st.markdown(clean_answer)
                    
                    with st.container(border=True):
                        st.markdown("**🤖 AI採点チェックポイント1**")
                        st.write(text_cp1)
                        st.markdown("**🤖 AI採点チェックポイント2**")
                        st.write(text_cp2)
                        st.markdown("**🤖 AI採点チェックポイント3**")
                        st.write(text_cp3)

                    with st.container(border=True):
                        st.markdown("**💡 AI採点判断理由**")
                        clean_reason = str(text_reason).replace("\n", "  \n")
                        st.markdown(clean_reason)

                    with st.container(border=True):
                        st.markdown("**🤖 AI採点結果**")
                        if pd.isna(ai_judge_val) or str(ai_judge_val).strip() == "":
                            ai_status_html = "<span style='background-color: #757575; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>データなし</span>"
                        elif ai_judge_val == "O":
                            ai_status_html = "<span style='background-color: #1266F1; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>🟢 正答(O)</span>"
                        elif ai_judge_val == "X":
                            ai_status_html = "<span style='background-color: #DC3545; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>🔴 誤答(X)</span>"
                        elif ai_judge_val == "*":
                            ai_status_html = "<span style='background-color: #9e9e9e; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>⚪ 無答(*)</span>"
                        else:
                            ai_status_html = f"<span style='background-color: #757575; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{ai_judge_val}</span>"
                        st.markdown(f"AI判定: {ai_status_html}", unsafe_allow_html=True)

                # ==========================================================
                # 📥 右のエリア：正答画像、判定ボタン、メモ、レコード移動
                # ==========================================================
                with right_input:
                    st.markdown("### 🗂️ 採点入力・お手本確認")
                    current_response_id = current_row.get("response_id")
                    
                    try:
                        master_row = next(
                            (row for row in get_question_master(supabase) if row.get("response_id") == current_response_id),
                            {},
                        )
                        file_name = master_row.get("correct_image_file_name")

                        if file_name and str(file_name).strip() != "":
                            base_url = settings.STORAGE_BASE_URL
                            if "/storage/" in base_url:
                                domain = base_url.split("/storage/")[0]
                            else:
                                domain = base_url.rstrip("/")

                            clean_file_name = str(file_name).strip()
                            full_img_url = f"{domain}/storage/v1/object/public/correct_image/{clean_file_name}"

                            st.markdown("**🎯 正答画像 (お手本)** ※クリックで別タブで拡大")
                            st.markdown("<style>div.img-clickable-box img { max-height: 280px; object-fit: contain; width: 100%; border-radius: 4px; border: 1px solid #ddd; transition: opacity 0.2s; } div.img-clickable-box img:hover { opacity: 0.8; cursor: pointer; }</style>", unsafe_allow_html=True)

                            # 💡 ブラウザ標準のシンプルな a タグ（target="_blank"）のみに戻しました
                            html_preview = f"""
                            <div class="img-clickable-box">
                                <a href="{full_img_url}" target="_blank" title="別ウィンドウで拡大表示">
                                    <img src="{full_img_url}" />
                                </a>
                            </div>
                            """
                            st.markdown(html_preview, unsafe_allow_html=True)
                        else:
                            st.caption("⚠️ 正答画像ファイル名が登録されていません。")
                    except Exception as img_err:
                        st.caption(f"（画像読み込みスキップ: {img_err}）")
                 
           # ─────────────────────────────────────────────────────────

                    st.write("")

                    db_approver = current_row.get("final_approver_id")
                    has_approver = pd.notna(db_approver) and str(db_approver).strip() != "" and str(db_approver).lower() not in ["none", "null"]
                    is_admin_locked = has_approver and str(db_approver).strip() != str(st.session_state.get("user_id")).strip()
                    
                    
                    if pd.isna(current_judge) or str(current_judge).strip() == "":
                        status_html = "<span style='background-color: #757575; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>⏳ 未採点</span>"
                    elif current_judge == "O":
                        status_html = "<span style='background-color: #1266F1; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>🟢 正答(O)</span>"
                    elif current_judge == "X":
                        status_html = "<span style='background-color: #DC3545; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>🔴 誤答(X)</span>"
                    elif current_judge == "*":
                        status_html = "<span style='background-color: #9e9e9e; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>⚪ 無答(*)</span>"
                    elif current_judge == "H":
                        status_html = "<span style='background-color: #6f42c1; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>🟣 保留(H)</span>"
                    else:
                        status_html = f"<span style='background-color: #757575; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{current_judge}</span>"

                    
                    # ─── 📊 【新規追加】採点状況の横に進捗問題数を美しく配置 ───
                    # 現在のインデックス（0始まり）に+1した数値と、全体のレコード数を取得
                    current_num = current_index + 1
                    total_num = total_records
                    
                    # st.columnsを使って、現在の採点状況（左）と問題数（右）を等幅で綺麗に横並び配置
                    status_col1, status_col2 = st.columns([1.0, 1.0])
                    
                    with status_col1:
                        st.markdown(f"**現在の採点状況:** {status_html}", unsafe_allow_html=True)
                        
                    with status_col2:
                        # 💡 採点者がパッと直感的に見やすいように太字の青マイルド色でテキストを描画
                        st.markdown(f"<p style='margin:0; font-size:15px; font-weight:bold; color:#1266F1; line-height:1.8; text-align:right;'>📄 {current_num}問目（{total_num}問中）</p>", unsafe_allow_html=True)
                    
                    st.write("")
                    # ───────────────────────────────────────────────────────

                    
                    if is_admin_locked:
                        st.warning(f"🔒 この問題は管理者によって判定が確定しているため、上書き変更はロックされています（閲覧専用）。")
                    else:
                        st.write("採点判定を選択してください（ボタンを押すと即時保存して次の問題へ進みます）：")
                    
                    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns(4)
                    selected_score = None

                    # ─── 🎨 【確実背景色変更】選ばれていないボタンの背景色をグレーにするCSS ───
                    if pd.notna(current_judge) and str(current_judge).strip() != "":
                        tgt = str(current_judge).strip()
                        c_styles = "<style>"
                        
                        # 選択されていない判定ボタンの背景をグレー(#E0E0E0)、文字を薄くする
                        if tgt != "O": c_styles += f"div[class*='st-key-ans_true_'] button {{ background-color: #E0E0E0 !important; color: #888888 !important; opacity: 0.6 !important; }}"
                        if tgt != "X": c_styles += f"div[class*='st-key-ans_false_'] button {{ background-color: #E0E0E0 !important; color: #888888 !important; opacity: 0.6 !important; }}"
                        if tgt != "*": c_styles += f"div[class*='st-key-ans_none_'] button {{ background-color: #E0E0E0 !important; color: #888888 !important; opacity: 0.6 !important; }}"
                        if tgt != "H": c_styles += f"div[class*='st-key-ans_hold_'] button {{ background-color: #E0E0E0 !important; color: #888888 !important; opacity: 0.6 !important; }}"
                        
                        # 選択されているボタンの枠線を黒く太く強調する
                        act_key = {"O": "ans_true_", "X": "ans_false_", "*": "ans_none_", "H": "ans_hold_"}.get(tgt)
                        if act_key: c_styles += f"div[class*='st-key-{act_key}'] button {{ border: 3px solid #111111 !important; font-weight: bold !important; box-shadow: 0px 4px 10px rgba(0,0,0,0.15) !important; }}"
                        
                        c_styles += "</style>"
                        st.markdown(c_styles, unsafe_allow_html=True)
                        
                    # ─────────────────────────────────────────────────────────────────────────
                 

                    # ─── ⌨️ 【完全版】入力監視＆画像ウィンドウ一元管理JavaScript ───
                    if not is_admin_locked:
                        js_shortcut = f"""
                        <script>
                        // Streamlitアプリが動いている最上位のメイン画面のドキュメントをがっちり捕捉
                        const doc = window.parent.document;
                        
                        // 画面が再描画されるたびに命令が重複してフリーズするのを防ぐため、古い命令を一度完全にお掃除
                        if (window.scoringKeydownHandler) doc.removeEventListener('keydown', window.scoringKeydownHandler);
                        if (window.openOtehonHandler) doc.removeEventListener('open_otehon_image', window.openOtehonHandler);
                        if (window.closeOtehonHandler) doc.removeEventListener('close_otehon_window', window.closeOtehonHandler);
                        
                        // 💡 メイン画面側の変数として、開いた別ウィンドウのハンドル（操縦権限）をずっと記憶します
                        if (!window.hasOwnProperty('otehonWindowRef')) {{
                            window.otehonWindowRef = null;
                        }}
                        
                        // 💡 【超重要】画像がクリックされた時に、メイン画面の権限で確実にタブを開く（使い回す）処理
                        window.openOtehonHandler = function(e) {{
                            const url = e.detail;
                            // すでにタブが開いていて、かつ閉じられていない場合は、中身のURLだけを切り替えてフォーカスします（乱立防止）
                            if (window.otehonWindowRef && !window.otehonWindowRef.closed) {{
                                window.otehonWindowRef.location.href = url;
                                window.otehonWindowRef.focus();
                            }} else {{
                                // まだ開いていない場合は、新しく名前をつけて開きます
                                window.otehonWindowRef = window.parent.open(url, 'otehon_secure_tab');
                            }}
                        }};
                        doc.addEventListener('open_otehon_image', window.openOtehonHandler);
                        
                        // 💡 一覧に戻るボタン（またはEnter）が押されたときに、開いているタブを強制終了する処理
                        window.closeOtehonHandler = function() {{
                            if (window.otehonWindowRef && !window.otehonWindowRef.closed) {{
                                window.otehonWindowRef.close();
                                window.otehonWindowRef = null;
                            }}
                        }};
                        doc.addEventListener('close_otehon_window', window.closeOtehonHandler);
                        
                        // 🟢 キーボードショートカット（O, X, Space, H, N, B）の監視処理
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
                    # ─────────────────────────────────────────────────────────

                    if btn_col1.button("🟢 正答(O)", key=f"ans_true_{row_pkey}", use_container_width=True, disabled=is_admin_locked):
                        selected_score = "O"
                    if btn_col2.button("🔴 誤答(X)", key=f"ans_false_{row_pkey}", use_container_width=True, disabled=is_admin_locked):
                        selected_score = "X"
                    if btn_col3.button("⚪ 無答(*)", key=f"ans_none_{row_pkey}", use_container_width=True, disabled=is_admin_locked):
                        selected_score = "*"
                    if btn_col4.button("🟡 保留(H)", key=f"ans_hold_{row_pkey}", use_container_width=True, disabled=is_admin_locked):
                        selected_score = "H"

                    st.write("")


                    memo_input = st.text_area(
                        "採点メモ / コメント", 
                        value=current_row.get("memo", "") if current_row.get("memo") else "",
                        key=f"memo_text_{row_pkey}",
                        disabled=is_admin_locked
                    )
                    st.markdown("---")
                    
                    st.write("📂 **レコード移動・ナビゲーション**")
                    nav_col1, nav_col2, nav_col3, nav_col4, nav_col5, nav_col6 = st.columns([2.5, 2.2, 1.5, 1.5, 1.5, 2.2])
                    
                    if nav_col1.button("⏪ 先頭へ戻る", key="first_detail_btn", use_container_width=True):
                        if current_index > 0:
                            st.session_state["selected_row_index"] = 0
                            st.rerun()

                    if nav_col2.button("⏮️ 前の未採点へ", key="prev_unprocessed_btn", use_container_width=True):
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
                                st.warning("🔄 現在地より手前に未採点・保留がないため、末尾に戻って逆引き検索しました。")
                                time_module_prev.sleep(1.0)
                                st.rerun()
                            else:
                                st.info("✨ 現在地より手前に未採点・保留データはありません。")

                    if nav_col3.button("◀ 前へ", key="prev_detail_btn", use_container_width=True):
                        if current_index > 0:
                            st.session_state["selected_row_index"] = current_index - 1
                            st.rerun()

                    nav_col4.markdown(f"<p style='text-align: center; margin:0; font-weight:bold; font-size:14px; line-height:2.4;'>{current_index + 1}/{total_records}</p>", unsafe_allow_html=True)
                    
                    if nav_col5.button("次へ ▶", key="next_detail_btn", use_container_width=True):
                        if current_index < total_records - 1:
                            st.session_state["selected_row_index"] = current_index + 1
                            st.rerun()

                    if nav_col6.button("⏭️ 次の未採点へ", key="next_unprocessed_btn", use_container_width=True):
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
                                st.warning("🔄 現在地より後ろに未採点・保留がないため、先頭に戻って検索しました。")
                                time_module_next.sleep(1.0)
                                st.rerun()
                            else:
                                st.info("✨ この問題に含まれるすべての未採点・保留データは処理完了しています！")

                # ─── 🔄 いずれかのボタンが押されたら自動でSupabaseへ保存 ───
                if selected_score is not None:
                    try:
                        # 💡 最後のレコードかどうかを正確に判定
                        is_last_record = (current_index >= total_records - 1)

                        with st.spinner("Supabaseに保存中..."):
                            approver_id = st.session_state.get("user_id")
                            
                            # 最後の判定結果をデータベースへ確実に先行保存
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
                            # まだ後ろにレコードがある場合は通常通り次へ進む
                            st.toast(f"🟢 判定「{selected_score}」で正常に保存しました！", icon="✅")
                            st.session_state["selected_row_index"] = current_index + 1
                            st.rerun()
                        else:
                            # 💡 最後のレコードの場合、その場に2ボタン形式の確認ダイアログを起動
                            @st.dialog("🎉 採点完了の確認")
                            def show_finish_dialog():
                                st.markdown("##### ✨ これで最後の採点が終了しました！")
                                st.write("最終判定の保存は完了しています。このまま問題一覧画面に戻りますか？それとも内容を見直しますか？")
                                st.write("（※そのまま **Enterキー** を押すと一覧に戻ります）")
                                st.write("")
                                
                                # 横並びのフォームボタンを配置
                                with st.form(key="finish_confirm_2btn_form", border=False):
                                    submit_btn = st.form_submit_button("✅ このまま登録をして一覧に戻る", use_container_width=True)
                                    if submit_btn:
                                        # 💡 複雑なJSの干渉をすべて消去
                                        st.balloons() # 完了演出
                                        st.session_state["selected_grader"] = None
                                        st.session_state["selected_response"] = None
                                        st.session_state["selected_comp_date"] = None
                                        st.session_state["selected_row_index"] = 0
                                        st.session_state["current_step"] = "select"
                                        st.rerun()
                                
                                # 2. サブアクション（見直し：フォームの外に置くことでEnter誤爆を防ぎ、クリック専用にします）
                                if st.button("🔍 もう一度採点を見直す", use_container_width=True):
                                    # 一覧に戻らず、ポップアップを閉じて現在の最後の問題画面を再描画する
                                    st.rerun()
                            
                            # ダイアログを自動起動
                            show_finish_dialog()
                        
                    except Exception as e:
                        st.error(f"データベースの更新に失敗しました: {e}")
