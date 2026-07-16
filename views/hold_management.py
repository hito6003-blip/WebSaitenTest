import streamlit as st
import pandas as pd
import time
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
    
    # 💡 内部で st.session_state から引くのではなく、引数で渡された確実な current_user_id を使用する
    # (既存の user_role_id != 4 のチェックなどへ続く...)
   
    # 💡 確定仕様：全体特権管理者（role_id == 4）のみに限定ロック
    if user_role_id != 4:
        st.error("❌ この画面のアクセス権限がありません。特権管理者のみがアクセス可能です。")
        return

    # 🔑 画面遷移用の状態変数を初期化
    if "hold_current_step" not in st.session_state:
        st.session_state["hold_current_step"] = "select"

    # 👥 どのステップからでも名前を逆引きできるよう、関数直下（共通エリア）でマスタを取得
    try:
        group_members_query = supabase.table("graders").select("grader_id, grader_name, group_id")
        group_members = group_members_query.execute()
        
        group_member_map = {
            m["grader_id"]: m.get("grader_name", "")
            for m in (group_members.data or [])
            if m.get("grader_id") is not None
        }
    except Exception as e:
        st.error(f"採点者マスタの取得に失敗しました: {e}")
        return

    # ==============================================================================
    # 📄 【ステップ1】保留問題一覧画面 (hold_current_step が "select" のとき)
    # ==============================================================================
    if st.session_state["hold_current_step"] == "select":
        st.header("🟣 保留（H）レコード集中確認・再採点")
        st.markdown("現在解決していない **「保留（H）」** 判定、または過去の対応履歴データを集計しています。")
        
        # 💡【重複エラー対策】重複を根絶するため、キー名をユニークに変更
        if st.button("🔄 保留データを更新", key="hold_list_refresh_btn_v2", use_container_width=True):
            st.rerun()

        try:
            with st.spinner("データベースから保留データを抽出中..."):
                # 最新の全データを一気に対称ロード
                response = supabase.table("tbl_scoring_question_management") \
                    .select("saiten_question_id, checker_webid, response_id, judge_mark_result, final_approver_id, grading_comp_date") \
                    .execute()
                
                # 問題マスタから日本語タイトルをキャッシュ
                master_res = supabase.table("mst_questions").select("response_id, question_title").execute()
                master_data = master_res.data or []
                question_title_map = {row["response_id"]: row.get("question_title", "") for row in master_data if row.get("response_id")}
            
            if response.data:
                df_raw = pd.DataFrame(response.data)
                
                # 日付の表記揺れ・型ズレを「YYYY-MM-DD」に完全統一
                if "grading_comp_date" in df_raw.columns:
                    df_raw["grading_comp_date"] = pd.to_datetime(df_raw["grading_comp_date"], errors='coerce').dt.strftime('%Y-%m-%d')
                
                # 型エラーを防ぐため採点者IDと確定者IDを文字列としてクレンジング
                df_raw["checker_clean"] = df_raw["checker_webid"].fillna("").astype(str).str.strip()
                df_raw["approver_clean"] = df_raw["final_approver_id"].fillna("").astype(str).str.strip()
                admin_id_str = str(current_user_id).strip()
                
                # 各種進捗・監査用フラグの高速算出
                df_raw["is_graded"] = df_raw["judge_mark_result"].str.strip().isin(["O", "X", "*"])
                df_raw["is_unprocessed_hold"] = df_raw["judge_mark_result"].str.strip() == "H"
                
                # 保留対応数：最初の採点者と最終確定者が異なるレコード
                df_raw["is_hold_intervented"] = (df_raw["checker_clean"] != df_raw["approver_clean"]) & (df_raw["approver_clean"] != "")
                
                # フィルター用の自コミット・他コミット数
                df_raw["is_my_done"] = df_raw["is_graded"] & (df_raw["approver_clean"] == admin_id_str)
                df_raw["is_others_done"] = df_raw["is_graded"] & (df_raw["approver_clean"] != admin_id_str) & (df_raw["approver_clean"] != "")
                
                # グループ（キー3軸）ごとに一挙集計
                df_summary = (
                    df_raw
                    .groupby(["checker_webid", "grading_comp_date", "response_id"], dropna=False)
                    .agg(
                        採点総数=("response_id", "size"),
                        採点済数=("is_graded", "sum"),
                        保留残数=("is_unprocessed_hold", "sum"),
                        保留対応数=("is_hold_intervented", "sum"),
                        自対応数=("is_my_done", "sum"),
                        他対応数=("is_others_done", "sum")
                    )
                    .reset_index()
                )
                
                # 🚨【新要件：超厳格水際ロック】
                # 「保留残数が0より大きい」または「保留対応数が0より大きい」問題グループのみに完全限定！
                # これにより、そもそも保留が一度も絡んでいない通常問題や、他者の介入なく綺麗に通常完了したグループは100%排除されます。
                df_summary = df_summary[(df_summary['保留残数'] > 0) | (df_summary['保留対応数'] > 0)]
                
                df_summary.columns = ['対象採点者', '採点完了日', '問題', '採点総数', '採点済数', '保留残数', '保留対応数', '自対応数', '他対応数']
                df_summary = df_summary[['対象採点者', '採点完了日', '問題', '採点総数', '採点済数', '保留残数', '保留対応数', '自対応数', '他対応数']]

                # 💡 表示フィルターUI
                st.markdown("##### 🔍 保留データ表示フィルター")
                f_col1, f_col2, f_col3 = st.columns(3)
                with f_col1:
                    show_hold_active = st.checkbox("🔴 未対応の保留あり", value=True, key="filter_hold_active")
                with f_col2:
                    show_hold_my_done = st.checkbox("🔵 自分が対応済のみ", value=True, key="filter_hold_my_done")
                with f_col3:
                    show_hold_others_done = st.checkbox("🟢 自分以外が対応済のみ", value=True, key="filter_hold_others_done")

                keep_mask = pd.Series(False, index=df_summary.index)
                if show_hold_active:
                    keep_mask |= (df_summary['保留残数'] > 0)
                if show_hold_my_done:
                    keep_mask |= (df_summary['自対応数'] > 0)
                if show_hold_others_done:
                    keep_mask |= (df_summary['他対応数'] > 0)
                    
                df_summary = df_summary[keep_mask]

                if df_summary.empty:
                    st.success("✨ 条件に一致する保留データ、または対応履歴はありません。")
                    return

                # 採点完了日の昇順にソート
                df_summary = df_summary.sort_values(by=['採点完了日', '対象採点者', '問題'], ascending=[True, True, True])

                st.metric("表示中の保留総パターン数", len(df_summary))
                st.write("")

                # ─── 📊 ご指定の全8列レイアウトヘッダー ───
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
                    
                    my_done_count = int(row['自対応数'])
                    others_done_count = int(row['他対応数'])
                    
                    comp_date_val = row['採点完了日']
                    display_date = "（未設定）" if pd.isna(comp_date_val) else str(comp_date_val).strip()
                    
                    current_response_id = row['問題']
                    display_title = question_title_map.get(current_response_id, current_response_id)
                    if not display_title or str(display_title).strip() == "":
                        display_title = current_response_id
                        
                    # 🎨 状況の出し分けロジック（保留残数が0なら最優先で青の「確認・修正」にする）
                    if unprocessed_count > 0:
                        font_color = "#DC3545"      # 🔴 赤：未処理の保留が残っている
                        status_label = "🔍 再採点開始"
                    elif unprocessed_count == 0:
                        font_color = "#1266F1"      # 🔵 青：完了しているが、保留対応の歴史があるグループ
                        status_label = "🔄 確認・修正"
                    else:
                        font_color = "#1266F1"
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
                        st.rerun()
                    
                    st.markdown("<hr style='margin: 0.3em 0; border: 0; border-top: 1px solid #eee;'>", unsafe_allow_html=True)
            else:
                st.success("✨ 現在、データベース内に保留データはありません！すべての判定が完了しています。")

        except Exception as e:
            st.error(f"データ取得エラー: {e}")

    # ==============================================================================
    # ✍️ 【ステップ2】保留レコードのみの個別再採点画面 (hold_current_step が "grading" のとき)
    # ==============================================================================
    elif st.session_state["hold_current_step"] == "grading":
        selected_grader = st.session_state.get("hold_selected_grader")
        selected_response = st.session_state.get("hold_selected_response")
        
        # 💡 引数またはセッションから安全に管理者のIDを確定
        if "current_user_id" not in locals() or current_user_id is None:
            current_user_id = st.session_state.get("user_id", "UNKNOWN_USER")
        
        if selected_grader and selected_response:
            # 日本語問題タイトルの単件フォールバック取得
            display_title = selected_response
            try:
                title_res = supabase.table("mst_questions").select("question_title").eq("response_id", selected_response).limit(1).execute()
                if title_res.data and len(title_res.data) > 0:
                    title_val = title_res.data[0].get("question_title")
                    if title_val and str(title_val).strip() != "":
                        display_title = str(title_val).strip()
            except Exception:
                pass

            st.subheader(f"🟣 保留解除・再採点中: {display_title}")
            st.caption(f"対象採点者: {selected_grader} | 問題ID: {selected_response}")

            # ⬅️ 一覧に戻るボタン
            if st.button("⬅️ 保留問題一覧に戻る", key="hold_back_to_list_btn"):
                st.session_state["hold_selected_grader"] = None
                st.session_state["hold_selected_response"] = None
                st.session_state["hold_selected_row_index"] = 0
                st.session_state["hold_initial_total_records"] = None
                st.session_state["hold_current_step"] = "select"
                st.rerun()

            # 💡 日付条件を効かせて最新データを安全にロード
            with st.spinner("保留レコードを読み込み中..."):
                raw_session_date = st.session_state.get("hold_selected_comp_date")
                formatted_comp_date = None
                if raw_session_date:
                    try:
                        formatted_comp_date = pd.to_datetime(raw_session_date).strftime('%Y-%m-%d')
                    except Exception:
                        formatted_comp_date = str(raw_session_date).strip()

                query = supabase.table("tbl_scoring_question_management") \
                    .select("*") \
                    .eq("checker_webid", selected_grader) \
                    .eq("response_id", selected_response)
                
                if formatted_comp_date:
                    query = query.eq("grading_comp_date", formatted_comp_date)
                
                detail_response = query.order("saiten_question_id", desc=False).execute()

            # 生の配列順を 100% そのまま維持して格納（インデックスズレ完全粉砕）
            all_rows = []
            if detail_response.data:
                for r in detail_response.data:
                    all_rows.append(r)

            # Oldカウント記憶による0問フリーズを強制解除
            if not all_rows or (len(all_rows) > 0 and st.session_state.get("hold_total_at_start") == 0):
                st.session_state["hold_initial_total_records"] = None

            if not all_rows:
                st.info("💡 対象データが見つかりませんでした。一度一覧へ戻ります。")
                st.session_state["hold_current_step"] = "select"
                st.rerun()
                return
        
            total_records = len(all_rows)
            # 💡【総数カウントロジックの確定】未対応が0件なら全件数を分母にする
            if st.session_state.get("hold_initial_total_records") is None:
                initial_holds = [r for r in all_rows if str(r.get("judge_mark_result", "")).strip().upper() == "H"]
                st.session_state["hold_total_at_start"] = len(initial_holds) if len(initial_holds) > 0 else total_records
                st.session_state["hold_initial_total_records"] = True

            # 現在まだ「H（未対応）」のまま残っている本当の件数をリアルタイム計算
            remaining_hold_count = len([r for r in all_rows if str(r.get("judge_mark_result", "")).strip().upper() == "H"])
            display_total_records = st.session_state.get("hold_total_at_start", total_records)
                
            current_index = st.session_state.get("hold_selected_row_index", 0)
            current_index = max(0, min(current_index, total_records - 1))
            st.session_state["hold_selected_row_index"] = current_index

            current_row = all_rows[current_index]
            row_pkey = current_row.get("saiten_question_id")
            user_id_now = st.session_state.get("user_id")

            st.divider()
            
            # 📄 画面を左右指定の比率に美しく分割（左：データ表示、右：画像・操作入力）
            left_view, right_input = st.columns([5.0, 6.0])
            
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
                human_judge_val = current_row.get("judge_mark_result")
                
                # 🎨 【現在の採点状況バッジ】人間（DBの最新状態）の判定だけを見て描画
                current_status = str(human_judge_val).strip().upper() if pd.notna(human_judge_val) else "H"
                if current_status == "H" or current_status == "":
                    status_html = "<span style='background-color: #6f42c1; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>🟣 保留(H)</span>"
                elif current_status == "O":
                    status_html = "<span style='background-color: #1266F1; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>🟢 正答(O)</span>"
                elif current_status == "X":
                    status_html = "<span style='background-color: #DC3545; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>🔴 誤答(X)</span>"
                elif current_status == "*":
                    status_html = "<span style='background-color: #9e9e9e; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>⚪ 無答(*)</span>"
                else:
                    status_html = f"<span style='background-color: #757575; color: white; padding: 4px 12px; border-radius: 4px; font-weight: bold;'>{current_status}</span>"

                # 💡【新機能】確定者が存在する場合のみ、バッジの横にわかりやすく確定者IDを添えて表示！
                db_approver_raw = current_row.get("final_approver_id")
                approver_text = f" <span style='color: #666; font-size: 13px; font-weight: bold;'>👤 (確定者: {db_approver_raw})</span>" if pd.notna(db_approver_raw) and str(db_approver_raw).strip() not in ["", "None", "null"] else ""
                
                st.markdown(f"**現在の採点状況:** {status_html}{approver_text}", unsafe_allow_html=True)
                st.write("")

                # ① 解答 (answer) 💡【改行スペース2つ置換】
                with st.container(border=True):
                    st.markdown("**【解答 (answer)】**")
                    clean_answer = str(text_answer).replace("\n", "  \n")
                    st.markdown(clean_answer)
                
                # ② AI採点チェックポイント1〜3
                with st.container(border=True):
                    st.markdown("**🤖 AI採点チェックポイント1**")
                    st.write(text_cp1)
                    st.markdown("**🤖 AI採点チェックポイント2**")
                    st.write(text_cp2)
                    st.markdown("**🤖 AI採点チェックポイント3**")
                    st.write(text_cp3)

                # ③ AI採点判断理由 💡【改行スペース2つ置換】
                with st.container(border=True):
                    st.markdown("**💡 AI採点判断理由**")
                    clean_reason = str(text_reason).replace("\n", "  \n")
                    st.markdown(clean_reason)

                # ④ AI採点結果のバッジ表示
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
                st.markdown("### 🗂️ 再採点入力（保留解除）")
                current_response_id = current_row.get("response_id")
                
                try:
                    master_response = supabase.table("mst_questions").select("correct_image_file_name").eq("response_id", current_response_id).limit(1).execute()
                    if master_response.data and len(master_response.data) > 0:
                        file_name = master_response.data[0].get("correct_image_file_name")
                        if file_name and str(file_name).strip() != "":
                            full_img_url = f"{settings.STORAGE_BASE_URL}{file_name}"
                            st.markdown("**🎯 正答画像 (お手本)**")
                            st.markdown("<style>div[data-testid='stImage'] img { max-height: 280px; object-fit: contain; }</style>", unsafe_allow_html=True)
                            st.image(full_img_url, use_container_width=True)
                except Exception:
                    pass

                # 🚨【特権管理者用・悲観的ロックリアルタイムチェック】
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

                if is_currently_locked:
                    st.warning(f"🔒 この問題は現在、採点者（ID: {db_locked_by}）が画面を開いて採点中のため、ロックされています（閲覧専用）。")
                else:
                    st.write("判定を選択して上書き修正・確定してください：")

                # ⑤ 人間用の判定ボタン（等幅3列で配置）
                btn_cols = st.columns(3)
                selected_score = None
                
                if btn_cols[0].button("🟢 正答(O)", key=f"h_score_O_{row_pkey}", use_container_width=True, disabled=is_currently_locked):
                    selected_score = "O"
                if btn_cols[1].button("🔴 誤答(X)", key=f"h_score_X_{row_pkey}", use_container_width=True, disabled=is_currently_locked):
                    selected_score = "X"
                if btn_cols[2].button("⚪ 無答(*)", key=f"h_score_N_{row_pkey}", use_container_width=True, disabled=is_currently_locked):
                    selected_score = "*"

                st.write("")
                
                # ⑥ 採点メモ / コメント
                memo_input = st.text_area(
                    "採点メモ / コメントの修正", 
                    value=current_row.get("memo", "") if current_row.get("memo") else "", 
                    key=f"h_memo_{row_pkey}",
                    disabled=is_currently_locked
                )
                st.markdown("---")
                # 💡【新配置】右側エリアの最下部に集約されたレコード移動ボタン（7連ナビゲーション ＆ あと〇問バッジ中央埋め込み）
                st.write("📂 **レコード移動・ナビゲーション**")
                nav_col1, nav_col2, nav_col3, nav_col4, nav_col5, nav_col6, nav_col7 = st.columns([1.2, 1.8, 1.2, 1.2, 1.2, 1.8, 1.8])
                
                # 1. 先頭へ戻る
                if nav_col1.button("⏪ 先頭へ戻る", key="h_nav_first", use_container_width=True):
                    st.session_state["hold_selected_row_index"] = 0
                    st.rerun()

                # 2. 前の未処理（H）へ戻る（ラップ検索）
                if nav_col2.button("⏮️ 前の未処理へ", key="h_nav_prev_unprocessed", use_container_width=True):
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

                # 3. 前のレコード
                if nav_col3.button("◀ 前へ", key="h_nav_prev", use_container_width=True):
                    if current_index > 0:
                        st.session_state["hold_selected_row_index"] = current_index - 1
                        st.rerun()

                # 4. 🎯 中央配置：「あと 〇 問」カウントダウン ＆ 位置表示の美麗バッジ
                nav_col4.markdown(
                    f"<div style='text-align: center; margin:0; line-height:1.2; color: #6f42c1; font-weight: bold; font-size:11px; padding-top:4px Triton;'>"
                    f"あと <span style='font-size: 16px; font-weight: 900;'>{remaining_hold_count}</span> 問<br>"
                    f"<span style='color: #888888;'>({current_index + 1}/{total_records})</span>"
                    f"</div>", 
                    unsafe_allow_html=True
                )
                
                # 5. 次のレコード
                if nav_col5.button("次へ ▶", key="h_nav_next", use_container_width=True):
                    if current_index < total_records - 1:
                        st.session_state["hold_selected_row_index"] = current_index + 1
                        st.rerun()

                # 6. 次の未処理（H）へ進む（ラップ検索）
                if nav_col6.button("⏭️ 次の未処理へ", key="h_nav_next_unprocessed", use_container_width=True):
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

                # 7. ✨【新設】次の保留対応問題へ移動（role_id == 4 の管理者確定レコードのみを対象とした巡回レビュー監査ワープ）
                if nav_col7.button("👑 管理者対応へ", key="h_nav_next_admin_task", use_container_width=True):
                    import time as time_module_admin
                    target_admin_index = None

                    # 💡【爆速防衛策】毎回DBを叩くのを防ぐため、gradersテーブルからrole_id=4(管理者)のIDリストを瞬時に逆引き
                    try:
                        admin_users_res = supabase.table("graders").select("grader_id").eq("role_id", 4).execute()
                        admin_id_set = {str(u.get("grader_id")).strip() for u in (admin_users_res.data or []) if u.get("grader_id") is not None}
                    except Exception:
                        admin_id_set = set()

                    # 💡 判定関数：確定者IDがrole_id=4の管理者リストに含まれているかを判定
                    def is_role4_approver(val):
                        if pd.isna(val):
                            return False
                        v_str = str(val).strip()
                        return v_str in admin_id_set and v_str not in ["", "None", "null"]

                    # ① 現在地より後ろをループ横断検索
                    for i in range(current_index + 1, total_records):
                        if is_role4_approver(all_rows[i].get("final_approver_id")):
                            target_admin_index = i
                            break
                    
                    # ② 後ろになければ先頭から現在地の手前までを安全にラップ検索
                    if target_admin_index is None:
                        for i in range(0, current_index):
                            if is_role4_approver(all_rows[i].get("final_approver_id")):
                                target_admin_index = i
                                break
                    
                    if target_admin_index is not None:
                        st.session_state["hold_selected_row_index"] = target_admin_index
                        if target_admin_index < current_index:
                            st.warning("🔄 後方に該当データがないため、先頭に戻って管理者確定レコードへワープしました。")
                            time_module_admin.sleep(0.8)
                        st.rerun()
                    else:
                        st.info("✨ このグループ内に、管理者が対応した保留レコードはありません。")

            # ─── 🔄 新判定がクリックされたら自動でSupabaseへ上書きコミット ───
            if selected_score is not None:
                try:
                    with st.spinner("判定を上書き保存中..."):
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
                    
                    # 📝 【操作ログ】保留解除・上書き再修正アクションを刻印
                    try:
                        insert_operation_log(
                            supabase=supabase,
                            operator_id=approver_id,
                            action_type="HOLD_RELEASE",
                            target_id=str(row_pkey),
                            description=f"管理者による保留判定の確定・修正（問題ID: {selected_response}, 元の状態: {current_status} -> 新判定: {selected_score}）。"
                        )
                    except Exception:
                        pass

                    # 🚀 【採点画面と完全同期】判定後の挙動制御
                    if current_index < total_records - 1:
                        st.toast(f"🎯 判定「{selected_score}」で正常に保存しました！", icon="✅")
                        st.session_state["hold_selected_row_index"] = current_index + 1
                    else:
                        st.balloons()
                        st.toast("🎉 この問題に含まれるすべての保留データの処理が完了しました！", icon="✨")
                        
                        st.session_state["hold_selected_grader"] = None
                        st.session_state["hold_selected_response"] = None
                        st.session_state["hold_selected_comp_date"] = None
                        st.session_state["hold_selected_row_index"] = 0
                        st.session_state["hold_initial_total_records"] = None
                        st.session_state["hold_total_at_start"] = None
                        st.session_state["hold_current_step"] = "select"
                    
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"データベースの更新に失敗しました: {e}")
