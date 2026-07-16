import streamlit as st
import pandas as pd
from datetime import datetime, date, timezone, timedelta
from zoneinfo import ZoneInfo
import time

def show_question_list(supabase, settings, current_user_id, current_role_id):
    """
    📄 タブ1: レスポンス識別子別集計(採点者画面)
    ステップ1(問題一覧)と、ステップ2(個別採点：ナビゲーション・画像・4連ボタン・メモ同時保存仕様)を制御します。
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
            st.rerun()

        try:
            with st.spinner("データを受信中..."):
                # メイン管理データの取得 (条件判定のために ai_judge_mark, is_locked, locked_by_webid, locked_at も合わせて取得)
                response = supabase.table("tbl_scoring_question_management") \
                    .select("checker_webid, response_id, judge_mark_result, grading_comp_date, ai_judge_mark, is_locked, locked_by_webid, locked_at") \
                    .eq("checker_webid", current_user_id) \
                    .execute()
                
                # 💡 問題マスタから response_id と question_title を一括取得
                master_res = supabase.table("mst_questions") \
                    .select("response_id, question_title") \
                    .execute()
                
                # マスタの辞書マップを作成（高速検索用）
                master_data = master_res.data or []
                question_title_map = {row["response_id"]: row.get("question_title", "") for row in master_data if row.get("response_id")}
            
            if response.data:
                df_raw = pd.DataFrame(response.data)
                
                # 🚨 【確定仕様】ai_judge_mark が空（Null または 空文字）のレコードが「1件でも含まれるグループ」を特定して完全排除する
                # まず、ai_judge_mark が無効（Null または 空文字）なレコードを判定するフラグを立てる
                df_raw["ai_is_invalid"] = df_raw["ai_judge_mark"].isna() | (df_raw["ai_judge_mark"].astype(str).str.strip() == "")
                
                # グループ（checker_webid, grading_comp_date, response_id）ごとに、無効なAI結果が1件でもあるか集計
                df_ai_check = df_raw.groupby(["checker_webid", "grading_comp_date", "response_id"], dropna=False)["ai_is_invalid"].sum().reset_index()
                
                # 無効なAIデータが0件（＝すべて正常に登録完了している）グループのキーだけを抽出
                df_valid_groups = df_ai_check[df_ai_check["ai_is_invalid"] == 0][["checker_webid", "grading_comp_date", "response_id"]]
                
                # 元の生データから、有効なグループに属するものだけを残す（＝未準備グループを存在ごと完全非表示）
                df_data = pd.merge(df_raw, df_valid_groups, on=["checker_webid", "grading_comp_date", "response_id"], how="inner")
                
                # 💡 採点可能データがゼロ件になってしまった場合のケア
                if df_data.empty:
                    st.info("💡 現在、採点可能な問題はありません。AIデータの準備が整うまでしばらくお待ちください。")
                    return

                # 進捗集計フラグ
                df_data["graded_filled"] = df_data["judge_mark_result"].apply(
                    lambda x: pd.notna(x) and str(x).strip() in ["O", "X", "*"]
                )
                df_data["hold_filled"] = df_data["judge_mark_result"].apply(
                    lambda x: pd.notna(x) and str(x).strip() == "H"
                )
                df_data["ungraded_filled"] = df_data["judge_mark_result"].apply(
                    lambda x: pd.isna(x) or str(x).strip() == ""
                )
                
                # 3軸グループ化ロジック (AIデータがクリーンなものだけが集計される)
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
                
                # カラムの構造化とソート
                df_summary.columns = ['採点者ID', '採点完了日', '問題ID', '採点数', '未採点数', '採点済数', '保留数']
                df_summary = df_summary[['採点者ID', '問題ID', '採点数', '未採点数', '採点済数', '保留数', '採点完了日']]
                df_summary = df_summary.sort_values(by=['採点者ID', '採点完了日', '問題ID'], ascending=[True, True, True])
                
                st.metric("あなたの担当総集計パターン数", len(df_summary))
                
                # --- 進捗状況フィルターUIエリア ---
                st.markdown("##### 🔍 表示フィルター")
                filter_col1, filter_col2, filter_col3 = st.columns([2.0, 2.0, 5.0])
                
                with filter_col1:
                    show_active = st.checkbox(" 要採点（未採点・保留あり）", value=True, key="my_filter_show_active")
                with filter_col2:
                    show_completed = st.checkbox(" 採点完了", value=True, key="my_filter_show_completed")
                
                if show_active and not show_completed:
                    df_summary = df_summary[(df_summary['未採点数'] > 0) | (df_summary['保留数'] > 0)]
                elif show_completed and not show_active:
                    df_summary = df_summary[(df_summary['未採点数'] == 0) & (df_summary['保留数'] == 0)]
                elif not show_active and not show_completed:
                    df_summary = df_summary.iloc[0:0]

                st.caption(f"💡 フィルター適用後の表示件数: {len(df_summary)} 件")
                st.write("")
                # ─── 📊 データ表示ヘッダー（8列構成） ───
                h_col1, h_col2, h_col3, h_col4, h_col5, h_col6, h_col7, h_col8 = st.columns([1.2, 1.2, 2.5, 1.0, 1.0, 1.0, 1.0, 1.2])
                h_col1.markdown(f"<div style='white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>**{settings.LABELS['col_grader']}**</div>", unsafe_allow_html=True)
                h_col2.markdown("<div style='white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>**提出期限日**</div>", unsafe_allow_html=True)
                h_col3.markdown("<div style='white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>**問題**</div>", unsafe_allow_html=True)
                h_col4.markdown(f"<div style='white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>**{settings.LABELS['col_count']}**</div>", unsafe_allow_html=True)
                h_col5.markdown("<div style='white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>**未採点数**</div>", unsafe_allow_html=True)
                h_col6.markdown("<div style='white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>**採点済数**</div>", unsafe_allow_html=True)
                h_col7.markdown("<div style='white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>**保留数**</div>", unsafe_allow_html=True)
                h_col8.markdown(f"<div style='white-space: nowrap; overflow: hidden; text-overflow: ellipsis;'>**{settings.LABELS['col_action']}**</div>", unsafe_allow_html=True)
                st.divider()

                # ─── 🔄 データ行ループ ───
                for index, row in df_summary.iterrows():
                    col1, col2, col3, col4, col5, col6, col7, col8 = st.columns([1.2, 1.2, 2.5, 1.0, 1.0, 1.0, 1.0, 1.2])
                    
                    ungraded_count = int(row.get('未採点数', 0))
                    hold_count = int(row.get('保留数', 0))
                    comp_date_val = row.get('採点完了日', None)
                    
                    font_color = "#000000"
                    is_alert_font = False
                    
                    jst_tz = ZoneInfo("Asia/Tokyo")
                    now_jst = datetime.now(jst_tz)
                    is_lock_deadline = False
                    
                    if pd.notna(comp_date_val) and str(comp_date_val).strip() != "":
                        try:
                            comp_date_str = str(comp_date_val).strip()
                            deadline_jst = datetime.strptime(f"{comp_date_str} 12:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=jst_tz)
                            if now_jst >= deadline_jst:
                                is_lock_deadline = True
                        except Exception:
                            pass

                    # 🎨 4色条件分岐判定
                    if hold_count > 0:
                        font_color = "#6f42c1"  # 紫文字
                        is_alert_font = True
                    elif ungraded_count == 0 and hold_count == 0:
                        font_color = "#1266F1"  # 青文字
                    else:
                        if pd.notna(comp_date_val) and str(comp_date_val).strip() != "":
                            try:
                                comp_date = datetime.strptime(str(comp_date_val).strip(), "%Y-%m-%d").date()
                                if 0 <= (comp_date - now_jst.date()).days <= 2:
                                    font_color = "#DC3545"  # 赤文字
                                    is_alert_font = True
                            except Exception:
                                font_color = "#000000"

                    def write_colored(col_obj, text):
                        if is_alert_font:
                            style_attr = "font-family: 'Meiryo', sans-serif; font-weight: 900; font-size: 16px; letter-spacing: 0.5px; text-shadow: 0.5px 0.5px 1px rgba(0,0,0,0.15);"
                        else:
                            style_attr = "font-weight: 500;"
                        col_obj.markdown(f"<p style='color: {font_color}; {style_attr} margin: 0; padding: 4px 0;'>{text}</p>", unsafe_allow_html=True)

                    current_response_id = row['問題ID']
                    display_title = question_title_map.get(current_response_id, current_response_id)
                    if not display_title or str(display_title).strip() == "":
                        display_title = current_response_id

                    write_colored(col1, f"{row['採点者ID']}")
                    display_date = "" if pd.isna(comp_date_val) else str(comp_date_val).strip()
                    write_colored(col2, display_date)

                    id_style = "font-family: 'Meiryo', sans-serif; font-weight: 900; font-size: 14px;" if is_alert_font else "font-weight: 500; font-size: 14px;"
                    col3.markdown(f"<div style='color: {font_color}; {id_style} white-space: normal; word-break: break-all; padding: 4px 0; line-height: 1.3;'>{display_title}</div>", unsafe_allow_html=True)
                    
                    write_colored(col4, f"{row['採点数']}")
                    write_colored(col5, f"{ungraded_count}")
                    write_colored(col6, f"{row['採点済数']}")
                    write_colored(col7, f"{hold_count}")
                    
                    # 🛡️ 【ルートA：12時以降の採点ロック 兼 管理者救済ガードレール】
                    is_button_disabled = False
                    if is_lock_deadline and ungraded_count == 0 and hold_count == 0:
                        if st.session_state.get("role_id") != 4:
                            is_button_disabled = True
                            
                    button_label = "🔒 締切" if is_button_disabled else settings.LABELS['col_action']
                    if col8.button(button_label, key="start_btn_" + str(index), use_container_width=True, disabled=is_button_disabled):
                        login_user_id = st.session_state.get("user_id")
                        
                        # 🚨【バツ閉じ・放置対策：タイムゾーン完全適合のゴーストロック風化救済】
                        with st.spinner("🔒 問題の編集ロックを確保中..."):
                            try:
                                # ① グループ内の実レコードからロック状態を精査
                                lock_check_res = supabase.table("tbl_scoring_question_management") \
                                    .select("saiten_question_id, is_locked, locked_by_webid, locked_at") \
                                    .eq("checker_webid", row['採点者ID']) \
                                    .eq("response_id", row['問題ID']) \
                                    .execute()
                                
                                is_group_locked_by_other = False
                                
                                # ⏱️ テスト用に30秒（0.5分）、本番用に設定値などを秒換算で厳格に定義
                                # もしsettingsファイルに秒数があるなら settings.LOGIN_TIMEOUT_SECONDS を直接秒数で使ってもOK！
                                LOCK_TIMEOUT_SECONDS = 30  # 💡ここで「〇〇秒」とダイレクトに指定します
                                
                                # 現在の絶対時間（エポック秒：タイムゾーンに依存しない絶対数値）を取得
                                now_epoch = time.time() 
                                
                                if lock_check_res.data:
                                    for rec in lock_check_res.data:
                                        rec_locked = rec.get("is_locked", False)
                                        rec_by = rec.get("locked_by_webid")
                                        rec_at_str = rec.get("locked_at")
                                        
                                        if rec_locked and str(rec_by).strip() != str(login_user_id).strip():
                                            # ロックの風化検証
                                            is_expired = False
                                            if rec_at_str:
                                                try:
                                                    # 💡【鉄壁の対策】文字列の型に関わらず、ISO形式から純粋なエポックタイム（秒数）へ変換
                                                    # 末尾のZやタイムゾーンのズレを完全に吸収して比較します
                                                    clean_ts_str = str(rec_at_str).replace("Z", "+00:00")
                                                    db_epoch = datetime.fromisoformat(clean_ts_str).timestamp()
                                                    
                                                    # 純粋な数値として「現在秒 - 保存された秒 > 設定秒」を計算（時差ボケゼロ）
                                                    if (now_epoch - db_epoch) > LOCK_TIMEOUT_SECONDS:
                                                        is_expired = True  # 設定秒以上経過したゴーストは確実に風化扱い
                                                except Exception:
                                                    pass
                                            
                                            if not is_expired:
                                                is_group_locked_by_other = True
                                                break
                                
                                if is_group_locked_by_other:
                                    st.error("⚠️ この問題グループは現在、他の採点者が作業中のためロックされています。")
                                    st.stop()
                                
                                # ② ロック取得（または風化ゴーストの上書き奪還）：グループ全件を自分専用に染め上げる
                                # 💡 後工程で型エラーを起こさないよう、ISO形式かつ現在タイムスタンプを綺麗に保存
                                now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                                supabase.table("tbl_scoring_question_management") \
                                    .update({
                                        "is_locked": True, 
                                        "locked_by_webid": login_user_id, 
                                        "locked_at": now_iso
                                    }) \
                                    .eq("checker_webid", row['採点者ID']) \
                                    .eq("response_id", row['問題ID']) \
                                    .execute()
                                    
                            except Exception as lock_err:
                                st.error(f"ロックの取得に失敗しました: {lock_err}")
                                st.stop()

                        # セッション状態を同期してステップ2へ突入
                        st.session_state["selected_grader"] = row['採点者ID']
                        st.session_state["selected_response"] = row['問題ID']
                        st.session_state["selected_row_index"] = 0
                        st.session_state["current_step"] = "grading"
                        st.rerun()

            else:
                st.info("あなたが担当するデータはまだ登録されていません。")
	                
        except Exception as e:
            st.error(f"データ取得エラー: {e}")

    # ==========================================================
    # ✍️ ステップ2: 1レコードずつの個別採点画面 (current_step が "grading" のとき)
    # ==========================================================
    elif st.session_state["current_step"] == "grading":
        selected_grader = st.session_state.get("selected_grader")
        selected_response = st.session_state.get("selected_response")
        
        if selected_grader and selected_response:
            display_title = selected_response
            try:
                title_res = supabase.table("mst_questions").select("question_title").eq("response_id", selected_response).limit(1).execute()
                if title_res.data and len(title_res.data) > 0:
                    title_val = title_res.data[0].get("question_title")
                    if title_val and str(title_val).strip() != "":
                        display_title = str(title_val).strip()
            except Exception:
                pass

            st.subheader(f"🔍 採点中: {display_title}")
            st.caption(f"担当採点者: {selected_grader} | 問題ID: {selected_response}")

            if st.button("⬅️ 問題一覧に戻る", key="back_to_list_btn"):
                st.session_state["selected_grader"] = None
                st.session_state["selected_response"] = None
                st.session_state["selected_row_index"] = 0
                st.session_state["current_step"] = "select"
                st.rerun()

            # 💡 大元の最新データをSupabaseから安全にロードする処理を完全に復元！
            with st.spinner("採点対象データを読み込み中..."):
                detail_response = supabase.table("tbl_scoring_question_management") \
                    .select("*") \
                    .eq("checker_webid", selected_grader) \
                    .eq("response_id", selected_response) \
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

                # ⏱️ 放置されたロックの自動風化救済判定
                is_lock_expired = False
                if db_is_locked and db_locked_at:
                    try:
                        db_ts = datetime.fromisoformat(str(db_locked_at).replace("Z", "+00:00")).timestamp()
                        if (time.time() - db_ts) > settings.LOGIN_TIMEOUT_SECONDS:
                            is_lock_expired = True
                    except Exception:
                        pass

                # 🚨 判定：他人がロックしており、かつ時間が風化していない場合のみ「競合（閲覧専用）」とする
                is_currently_conflict = db_is_locked and (str(db_locked_by).strip() != str(login_user_id).strip()) and (not is_lock_expired)

                if is_currently_conflict:
                    st.error(f"⚠️ この問題は現在、別の採点者（ID: {db_locked_by}）が画面を開いて採点中のため、ロックされています。")
                    st.info("💡 内容の閲覧は可能ですが、判定ボタンの操作やメモの書き込みはバッティング防止のため制限されます。")
                    is_admin_locked = True
                else:
                    is_admin_locked = False



                # 📊 ナビゲーションバーの完全対称配置
                nav_col1, nav_col2, nav_col3, nav_col4, nav_col5, nav_col6 = st.columns([1.5, 2.2, 1.5, 1.5, 1.5, 2.2])
                
                # 1. ⏪ 先頭へ戻る
                if nav_col1.button("⏪ 先頭へ戻る", key="first_detail_btn", use_container_width=True):
                    if current_index > 0:
                        st.session_state["selected_row_index"] = 0
                        st.rerun()

                # 2. ⏮️ 前の未採点/保留へ戻る（ラップ検索）
                if nav_col2.button("⏮️ 前の未採点/保留へ", key="prev_unprocessed_btn", use_container_width=True):
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

                # 3. ◀ 前のレコード
                if nav_col3.button("◀ 前のレコード", key="prev_detail_btn", use_container_width=True):
                    if current_index > 0:
                        st.session_state["selected_row_index"] = current_index - 1
                        st.rerun()

                # 4. （現在の件数表示）
                nav_col4.markdown(f"<h4 style='text-align: center; margin:0; line-height:2.2;'>{current_index + 1} / {total_records} 件目</h4>", unsafe_allow_html=True)
                
                # 5. 次のレコードへ ▶ 
                if nav_col5.button("次のレコード ▶", key="next_detail_btn", use_container_width=True):
                    if current_index < total_records - 1:
                        st.session_state["selected_row_index"] = current_index + 1
                        st.rerun()

                # 6. ⏭️ 次の未採点/保留へ進む（ラップ検索）
                if nav_col6.button("⏭️ 次の未採点/保留へ", key="next_unprocessed_btn", use_container_width=True):
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
                            st.info("✨ この問題（パターン）に含まれるすべての未採点・保留データは処理完了しています！")

                st.divider()

                # 📄 画面を左右に分割（等幅2列に引数「2」を明示して安全対策済み）
                left_view, right_input = st.columns(2)

                with left_view:
                    st.markdown("### 📝 回答内容・情報")
                    current_response_id = current_row.get("response_id")
                    
                    try:
                        # 問題マスタから「画像ファイル名」を取得してダイレクト表示
                        master_response = supabase.table("mst_questions") \
                            .select("correct_image_file_name") \
                            .eq("response_id", current_response_id) \
                            .limit(1) \
                            .execute()
                        
                        if master_response.data and len(master_response.data) > 0:
                            first_row = master_response.data[0]
                            file_name = first_row.get("correct_image_file_name")
                            
                            if file_name and str(file_name).strip() != "":
                                full_img_url = f"{settings.STORAGE_BASE_URL}{file_name}"
                                st.markdown("**🎯 正答画像 (お手本)**")
                                st.image(full_img_url, use_container_width=True)
                            else:
                                st.caption("⚠️ この問題の正答画像ファイル名が登録されていません（空欄）。")
                        else:
                            st.caption("⚠️ この問題の正答画像はマスタに登録されていません。")
                            
                    except Exception as img_err:
                        st.caption(f"（画像読み込みスキップ: {img_err}）")
                with right_input:
                    st.markdown("### 🗂️ 採点入力")
                    
                    row_pkey = current_row.get("saiten_question_id")
                    text_answer = current_row.get("answer", "（データなし）")
                    text_cp1 = current_row.get("ai_cp1", "（データなし）")
                    text_cp2 = current_row.get("ai_cp2", "（データなし）")
                    text_cp3 = current_row.get("ai_cp3", "（データなし）")
                    text_reason = current_row.get("ai_reason", "（データなし）")
                    ai_judge_val = current_row.get("ai_judge_mark")
                    current_judge = current_row.get("judge_mark_result")
                    
                    # 人間用採点状況のバッジ表示ロジック
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

                    st.markdown(f"**現在の採点状況:** {status_html}", unsafe_allow_html=True)
                    st.write("")

                    # ① 解答 (answer) 💡【改行対応：Markdownのスペース2つ改行に変換】
                    with st.container(border=True):
                        st.markdown("**【解答 (answer)】**")
                        # 💡 文字列内の改行コードを、Markdownが認識できる「改行＋半角スペース2つ」に変換！
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

                    # ③ AI採点判断理由
                    with st.container(border=True):
                        st.markdown("**💡 AI採点判断理由**")
                        st.write(text_reason)

                    # ④ AI採点結果 (ai_judge_mark) のバッジ表示
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
                    
                    # 💡【ロック判定】管理者がすでに判定を終えているか厳格にチェック
                    # 確定者IDを取得
                    db_approver = current_row.get("final_approver_id")
                    has_approver = pd.notna(db_approver) and str(db_approver).strip() != "" and str(db_approver).lower() not in ["none", "null"]
                    
                    # 🚨 確定者が存在し、かつそれが自分自身（ログイン中の採点者ID）ではない場合のみ「管理者ロック」と判定！
                    is_admin_locked = has_approver and str(db_approver).strip() != str(st.session_state.get("user_id")).strip()
                    
                    if is_admin_locked:
                        st.warning(f"🔒 この問題は管理者によって判定が確定しているため、上書き変更はロックされています（閲覧専用）。")
                    else:
                        st.write("採点判定を選択してください（ボタンを押すと即時保存して次の問題へ進みます）：")
                    
                    # ⑤ 人間用の4連採点ボタン（💡is_admin_lockedがTrueなら完全グレーアウト無効化！）
                    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns(4)
                    selected_score = None
                    
                    if btn_col1.button("🟢 正答(O)", key=f"ans_true_{row_pkey}", use_container_width=True, disabled=is_admin_locked):
                        selected_score = "O"
                    if btn_col2.button("🔴 誤答(X)", key=f"ans_false_{row_pkey}", use_container_width=True, disabled=is_admin_locked):
                        selected_score = "X"
                    if btn_col3.button("⚪ 無答(*)", key=f"ans_none_{row_pkey}", use_container_width=True, disabled=is_admin_locked):
                        selected_score = "*"
                    if btn_col4.button("🟡 保留(H)", key=f"ans_hold_{row_pkey}", use_container_width=True, disabled=is_admin_locked):
                        selected_score = "H"

                    st.write("")

                    # ⑥ 採点メモ / コメント（💡ロック時は入力欄も自動で編集不可ロック！）
                    memo_input = st.text_area(
                        "採点メモ / コメント", 
                        value=current_row.get("memo", "") if current_row.get("memo") else "",
                        key=f"memo_text_{row_pkey}",
                        disabled=is_admin_locked
                    )
                    st.markdown("---")
                
                # ─── 🔄 いずれかのボタンが押されたら自動でSupabaseへ保存 ───
                if selected_score is not None:
                    try:
                        with st.spinner("Supabaseに保存中..."):
                            approver_id = st.session_state.get("user_id")
                            
                            # ⚠️ 【不可侵ルール】期限日（grading_comp_date）は上書き項目から100%除外してDB初期値を維持
                            supabase.table("tbl_scoring_question_management") \
                                .update({
                                    "judge_mark_result": selected_score,
                                    "final_approver_id": approver_id,
                                    "memo": memo_input
                                }) \
                                .eq("saiten_question_id", row_pkey) \
                                .execute()
                        
                        if current_index < total_records - 1:
                            st.toast(f"🟢 判定「{selected_score}」で正常に保存しました！", icon="✅")
                            st.session_state["selected_row_index"] = current_index + 1
                        else:
                            st.balloons()
                            st.toast("🎉 すべてのレコードの採点が完了しました！", icon="✨")
                            st.session_state["selected_grader"] = None
                            st.session_state["selected_response"] = None
                            st.session_state["selected_row_index"] = 0
                            st.session_state["current_step"] = "select"
                        st.rerun()
                        
                    except Exception as e:
                        st.error(f"データベースの更新に失敗しました: {e}")
