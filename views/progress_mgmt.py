import streamlit as st
import pandas as pd
from datetime import datetime, date

# 📝 安全に操作ログ関数をインポート
try:
    from log_common import insert_operation_log
except ImportError:
    try:
        from views.log_common import insert_operation_log
    except ImportError:
        def insert_operation_log(*args, **kwargs):
            pass

def show_progress_management(supabase, settings, display_confirm_panel):
    """
    📊 タブ2: 採点管理（グループリーダー用 / 特権管理者対応完全版）
    """
    st.header(settings.LABELS["tab2_group_header"])
    
    # セッション状態の初期化
    if "show_bulk_area" not in st.session_state:
        st.session_state["show_bulk_area"] = False

    user_role_id = st.session_state.get("role_id", None)
    current_group_id = st.session_state.get("group_id", None)
    current_user_id = st.session_state.get("user_id", "UNKNOWN_USER")
    
    if user_role_id != 4 and current_group_id is None:
        st.warning("あなたの所属グループ情報がありません。管理者にお問い合わせください。")
        return

    if st.button(settings.LABELS["refresh_button"], key="refresh_group_btn"):
        st.rerun()

    try:
        with st.spinner("データを受信中..."):
            # 💡 1. ユーザー権限によるデータ絞り込み（特権4なら全体、それ以外は自グループ）
            if user_role_id == 4:
                group_members_query = supabase.table("graders").select("grader_id, grader_name, group_id")
            else:
                group_members_query = supabase.table("graders").select("grader_id, grader_name, group_id").eq("group_id", current_group_id)
            
            group_members = group_members_query.execute()

            # グループマスタキャッシュ作成
            groups_res = supabase.table("scoring_groups").select("group_id, group_name").execute()
            groups_data = groups_res.data or []
            group_map = {row["group_id"]: row["group_name"] for row in groups_data if row.get("group_id") is not None}

            # マッピングデータの構築
            group_member_map = {m["grader_id"]: m.get("grader_name", "") for m in (group_members.data or []) if m.get("grader_id") is not None}
            member_group_id_map = {m["grader_id"]: m.get("group_id") for m in (group_members.data or []) if m.get("grader_id") is not None}
            member_ids = list(group_member_map.keys())

            if not member_ids:
                st.info("対象の採点者が見つかりません。")
                return

            # プルダウン用ラベル作成
            dropdown_labels = []
            for m_id in member_ids:
                m_name = group_member_map.get(m_id, "未設定")
                m_g_id = member_group_id_map.get(m_id)
                m_group_name = group_map.get(m_g_id, "所属なし")
                dropdown_labels.append(f"{m_group_name}：{m_name}（{m_id}）")

            # 管理データの一括取得
            response = supabase.table("tbl_scoring_question_management") \
                .select("checker_webid, response_id, judge_mark_result, grading_comp_date") \
                .in_("checker_webid", member_ids) \
                .execute()

            if response.data:
                df_data = pd.DataFrame(response.data)
                df_data["graded_filled"] = df_data["judge_mark_result"].apply(
                    lambda x: pd.notna(x) and str(x).strip() != ""
                )

                # 💡 3軸グループ化による進捗集計（日付が異なれば別行として分離）
                df_summary = (
                    df_data
                    .groupby(["checker_webid", "grading_comp_date", "response_id"], dropna=False)
                    .agg(
                        採点数=("response_id", "size"),
                        未採点問題数=("graded_filled", lambda x: (~x).sum()),
                        採点済問題数=("graded_filled", "sum"),
                    )
                    .reset_index()
                )
                df_summary = df_summary[['checker_webid', 'response_id', '採点数', '未採点問題数', '採点済問題数', 'grading_comp_date']]
                df_summary.columns = ['採点者ID', '問題ID', '採点数', '未採点問題数', '採点済問題数', '採点完了日']
                df_summary = df_summary.sort_values(by=['採点者ID', '採点完了日', '問題ID'], ascending=[True, True, True])

                # メトリック表示
                metric_label = "全体の担当総集計パターン数" if user_role_id == 4 else "グループ内の担当総集計パターン数"
                st.metric(metric_label, len(df_summary))
                
                # 🔍 表示フィルターUI
                st.markdown("##### 🔍 表示フィルター")
                filter_col1, filter_col2, filter_col3 = st.columns([2.0, 2.0, 5.0])
                
                with filter_col1:
                    show_active = st.checkbox("📝 要採点（未採点あり）", value=True, key="filter_show_active")
                with filter_col2:
                    show_completed = st.checkbox("✅ 採点完了のみ", value=True, key="filter_show_completed")

                with filter_col3:
                    # 💡 gradersマスタからIDと名前のマッピングを高速生成
                    grader_name_map = {}
                    try:
                        graders_res = supabase.table("graders").select("grader_id, grader_name").execute()
                        if graders_res.data:
                            for g_row in graders_res.data:
                                g_id = g_row.get("grader_id")
                                g_name = g_row.get("grader_name")
                                if g_id:
                                    grader_name_map[str(g_id).strip()] = str(g_name).strip() if g_name else ""
                    except Exception as e:
                        st.warning(f"⚠️ 採点者マスタの取得に失敗しました: {e}")

                    # 集計データに存在するユニークな採点者IDを自動取得
                    grader_column_name = '採点者ID' if '採点者ID' in df_summary.columns else 'checker_webid'
                    if grader_column_name in df_summary.columns:
                        available_graders = sorted(df_summary[grader_column_name].dropna().unique().tolist())
                    else:
                        available_graders = []

                    # format_funcを使ってプルダウンの表示を「ID：名前」に変換
                    def format_grader_option(option_id):
                        name = grader_name_map.get(str(option_id).strip())
                        if name:
                            return f"{option_id} ： {name}"
                        return str(option_id)

                    selected_graders = st.multiselect(
                        "👤 担当採点者で絞り込み (未選択時は全表示)",
                        options=available_graders,
                        default=[],
                        format_func=format_grader_option,
                        key="admin_filter_selected_graders"
                    )

                # 🧮 1. 進捗状況による第1次絞り込み
                if show_active and not show_completed:
                    df_summary = df_summary[df_summary['未採点問題数'] > 0]
                elif show_completed and not show_active:
                    df_summary = df_summary[df_summary['未採点問題数'] == 0]
                elif not show_active and not show_completed:
                    df_summary = df_summary.iloc[0:0]

                # 🧮 2. 採点者IDによる第2次絞り込み
                if selected_graders and grader_column_name in df_summary.columns:
                    df_summary = df_summary[df_summary[grader_column_name].isin(selected_graders)]

                st.caption(f"💡 フィルター適用後の表示件数: {len(df_summary)} 件")

                # ─── 🛠️ 一括更新エリア（UI表示） ───
                area_bulk_cols = st.columns([1.0, 1.0, 1.0, 1.0, 1.0, 4.0, 4.0, 5.5])
                with area_bulk_cols[-1]:
                    btn_label = "🔼 一括変更を閉じる" if st.session_state["show_bulk_area"] else "🔽 一括変更を開く"
                    if st.button(btn_label, key="toggle_bulk_btn", use_container_width=True):
                        st.session_state["show_bulk_area"] = not st.session_state["show_bulk_area"]
                        st.rerun()
                
                bulk_update_clicked = False
                bulk_checker_webid = None
                if st.session_state["show_bulk_area"]:
                    with st.container(border=True):
                        st.markdown("<span style='font-weight:bold; font-size:14px;'>↓チェックした行を一括変更↓</span>", unsafe_allow_html=True)
                        inner_col1, inner_col2 = st.columns(2)
                        with inner_col1:
                            bulk_select_idx = st.selectbox(
                                "", options=range(len(member_ids)),
                                format_func=lambda x: dropdown_labels[x],
                                key="bulk_checker_select", label_visibility="collapsed"
                            )
                            bulk_checker_webid = member_ids[bulk_select_idx]
                        with inner_col2:
                            bulk_update_clicked = st.button("一括更新", key="bulk_update_btn", use_container_width=True)

                # 🌟 一括更新用のプレースホルダーをループ手前に配置して固定
                bulk_msg_container = st.empty()

                # ─── 📊 データテーブルのヘッダー描画 ───
                grader_name_label = settings.LABELS.get('col_grader_name', '採点者名')
                h_check, h_col0, h_col1, h_col_date, h_col2, h_col3, h_col4, h_col5, h_col6, h_col7 = st.columns([0.5, 1.0, 1.0, 1.2, 1.0, 1.0, 1.0, 1.0, 2.0, 4.0])
                h_check.markdown("**選択**")
                h_col0.markdown("**行番号**")
                h_col1.markdown(f"**{settings.LABELS['col_grader']}**")
                h_col_date.markdown("**採点完了日**")
                h_col2.markdown(f"**{settings.LABELS['col_response']}**")
                h_col3.markdown(f"**{settings.LABELS['col_count']}**")
                h_col4.markdown(f"**{settings.LABELS['col_ungraded']}**")
                h_col5.markdown(f"**{settings.LABELS['col_graded']}**")
                h_col6.markdown(f"**{grader_name_label}**")
                h_col7.markdown("**採点者変更**")

                # ─── 🔄 データ行ループと個別変更 ───
                row_records = df_summary.to_dict('records')
                for row_index, row in enumerate(row_records):
                    col_check, col_row_num, col1, col_date, col2, col3, col4, col5, col6, col7 = st.columns([0.5, 1.0, 1.0, 1.2, 1.0, 1.0, 1.0, 1.0, 2.0, 4.0])
                    selected = col_check.checkbox("", key=f"row_check_{row_index}", label_visibility="collapsed")
                    
                    ungraded_count = int(row.get('未採点問題数', 0))
                    comp_date_val = row.get('採点完了日', None)
                    
                    font_color = "#000000"
                    if ungraded_count == 0:
                        font_color = "#1266F1"
                    else:
                        if pd.notna(comp_date_val) and str(comp_date_val).strip() != "":
                            try:
                                if isinstance(comp_date_val, str):
                                    comp_date = datetime.strptime(comp_date_val.strip(), "%Y-%m-%d").date()
                                else:
                                    comp_date = pd.to_datetime(comp_date_val).date()
                                if abs((date.today() - comp_date).days) <= 2:
                                    font_color = "#DC3545"
                            except Exception:
                                font_color = "#000000"

                    def write_colored(col_obj, text):
                        if font_color == "#DC3545":
                            style_attr = "font-family: 'Meiryo', sans-serif; font-weight: 900; font-size: 16px; letter-spacing: 0.5px; text-shadow: 0.5px 0.5px 1px rgba(0,0,0,0.15);"
                        else:
                            style_attr = "font-weight: 500;"
                        col_obj.markdown(f"<p style='color: {font_color}; {style_attr} margin: 0; padding: 4px 0;'>{text}</p>", unsafe_allow_html=True)

                    write_colored(col_row_num, f"{row_index + 1}")
                    write_colored(col1, f"{row['採点者ID']}")
                    display_date = "" if pd.isna(comp_date_val) else str(comp_date_val).strip()
                    write_colored(col_date, display_date)
                    write_colored(col2, f"{row['問題ID']}")
                    write_colored(col3, f"{row['採点数']}")
                    write_colored(col4, f"{ungraded_count}")
                    write_colored(col5, f"{row['採点済問題数']}")
                    write_colored(col6, group_member_map.get(row['採点者ID'], ""))

                    current_member_idx = member_ids.index(row['採点者ID']) if row['採点者ID'] in member_ids else 0
                    # 🔓 --- 誤操作防止の安全ロックチェックボックス ---
                    is_editable = col7.checkbox("🔓 変更する", key=f"lock_guard_{row_index}")
                    
                    select_idx = col7.selectbox(
                        "", options=range(len(member_ids)), index=current_member_idx,
                        format_func=lambda x: dropdown_labels[x], key=f"checker_select_{row_index}",
                        label_visibility="collapsed", disabled=not is_editable
                    )
                    new_checker_webid = member_ids[select_idx]

                    if col7.button("変更", key=f"update_btn_{row_index}", use_container_width=True, disabled=not is_editable):
                        new_checker_webid = str(new_checker_webid).strip()
                        if new_checker_webid == "":
                            st.warning("変更する採点者を選択してください。")
                        else:
                            st.session_state[f"pending_update_{row_index}"] = {
                                "response_id": row['問題ID'], "old_webid": row['採点者ID'], "new_webid": new_checker_webid, "comp_date": row['採点完了日']
                            }
                    
                    # ─── 🔄 個別更新のコミット実行 ───
                    confirm_placeholder = col7.empty()
                    pending_key = f"pending_update_{row_index}"
                    pending_update = st.session_state.get(pending_key)
                    
                    if pending_update:
                        confirm_message = f"以下の内容で更新を実行します。\n行番号: {row_index + 1}\n問題ID: {pending_update['response_id']}\n変更前WEBID: {pending_update['old_webid']}\n変更後WEBID: {pending_update['new_webid']}\n⚠️ ※未採点レコード（白紙・空文字含む）のみが変更対象となります。"
                        confirmed = display_confirm_panel(confirm_message, pending_key, container=confirm_placeholder)
                        
                        if confirmed is True:
                            try:
                                q = supabase.table("tbl_scoring_question_management") \
                                    .update({"checker_webid": pending_update['new_webid']}) \
                                    .eq("response_id", pending_update['response_id']) \
                                    .eq("checker_webid", pending_update['old_webid']) \
                                    .or_("judge_mark_result.is.null, judge_mark_result.eq.")
                                
                                if pd.isna(pending_update['comp_date']) or str(pending_update['comp_date']).strip() == "" or str(pending_update['comp_date']).lower() in ["none", "null"]:
                                    q = q.is_("grading_comp_date", "null")
                                else:
                                    q = q.eq("grading_comp_date", str(pending_update['comp_date']).strip())
                                update_response = q.execute()
                                
                                updated_rows = getattr(update_response, 'data', []) or []
                                if len(updated_rows) == 0:
                                    confirm_placeholder.warning("変更対象 of 未採点レコードが見つかりませんでした。")
                                else:
                                    # 📝 【個別変更ログ刻印】
                                    try:
                                        insert_operation_log(
                                            supabase=supabase, operator_id=current_user_id, action_type="CHANGE_GRADER_INDIVIDUAL",
                                            target_id=str(pending_update['response_id']),
                                            description=f"個別変更実行。問題ID: {pending_update['response_id']}、旧担当: {pending_update['old_webid']} -> 新担当: {pending_update['new_webid']}、更新件数: {len(updated_rows)}件。"
                                        )
                                    except Exception:
                                        pass
                                    st.toast(f"🔓 未採点レコード {len(updated_rows)} 件の担当者を変更しました。", icon="✅")
                                    del st.session_state[pending_key]
                                    st.rerun()
                            except Exception as update_error:
                                confirm_placeholder.error(f"更新エラー: {update_error}")
                        elif confirmed is False:
                            del st.session_state[pending_key]
                            st.rerun()
                    st.markdown("<hr style='margin: 0.3em 0; border: 0; border-top: 1px solid #eee;'>", unsafe_allow_html=True)
                
                # ─── 🚀 ループの「外側」で一括更新ボタンの判定を実行 ───
                selected_indices = []
                if bulk_update_clicked:
                    for i in range(len(row_records)):
                        is_row_checked = st.session_state.get(f"row_check_{i}", False)
                        is_unlocked = st.session_state.get(f"lock_guard_{i}", False)
                        if is_row_checked and is_unlocked:
                            selected_indices.append(i)
                            
                    if not selected_indices:
                        st.warning("⚠️ 一括変更する行を選択し、かつ対象行の「🔓 変更する」チェックボックスをONにしてロックを解除してください。")
                    else:
                        st.session_state["pending_bulk_update"] = {
                            "selected_indices": selected_indices,
                            "new_webid": str(bulk_checker_webid).strip(),
                        }

                # ─── 🔄 一括更新の実行ロジック ───
                pending_bulk = st.session_state.get("pending_bulk_update")
                if pending_bulk:
                    if pending_bulk["new_webid"] == "":
                        st.warning("一括変更先の採点者WEBIDを選択してください。")
                        del st.session_state["pending_bulk_update"]
                    else:
                        selected_idx_list = pending_bulk["selected_indices"]
                        new_webid = pending_bulk["new_webid"]
                        
                        row_numbers = [str(i + 1) for i in selected_idx_list]
                        confirm_message = (
                            f"🗂️ **一括更新の確認**\n\n"
                            f"選択された **{len(selected_idx_list)} 件** の行から、**未採点問題のみ（白紙・空文字含む）** を抽出し担当者を一括変更します。\n"
                            f"対象行番号: {', '.join(row_numbers)}\n"
                            f"➡️ **変更後WEBID: {new_webid}**"
                        )
                        
                        bulk_confirmed = display_confirm_panel(confirm_message, "pending_bulk_update", container=bulk_msg_container)

                        if bulk_confirmed is True:
                            try:
                                total_success_groups = 0
                                total_updated_records = 0
                                with st.spinner("未採点レコードを抽出して一括更新中..."):
                                    for idx in selected_idx_list:
                                        target_row = row_records[idx]
                                        
                                        q = supabase.table("tbl_scoring_question_management") \
                                            .update({"checker_webid": new_webid}) \
                                            .eq("response_id", target_row['問題ID']) \
                                            .eq("checker_webid", target_row['採点者ID']) \
                                            .or_("judge_mark_result.is.null, judge_mark_result.eq.")
                                        
                                        comp_date_raw = target_row.get('採点完了日')
                                        if pd.isna(comp_date_raw) or str(comp_date_raw).strip() == "" or str(comp_date_raw).lower() in ["none", "null"]:
                                            q = q.is_("grading_comp_date", "null")
                                        else:
                                            q = q.eq("grading_comp_date", str(comp_date_raw).strip())
                                        
                                        update_response = q.execute()
                                        updated_rows = getattr(update_response, 'data', []) or []
                                        if len(updated_rows) > 0:
                                            total_success_groups += 1
                                            total_updated_records += len(updated_rows)
                                
                                # 📝 【一括変更ログ刻印】
                                if total_updated_records > 0:
                                    try:
                                        insert_operation_log(
                                            supabase=supabase, operator_id=current_user_id, action_type="CHANGE_GRADER_BULK",
                                            target_id="BULK_UPDATE",
                                            description=f"管理者による担当者の一括変更を実行。対象行数: {len(selected_idx_list)}行、新担当: {new_webid}、合計更新レコード数: {total_updated_records}件。"
                                        )
                                    except Exception:
                                        pass

                                del st.session_state["pending_bulk_update"]
                                if total_updated_records == 0:
                                    st.toast("⚠️ 対象行に未採点の問題が残っていなかったため、更新はスキップされました。", icon="ℹ️")
                                else:
                                    st.toast(f"🎉 正常に {total_success_groups} グループ（計 {total_updated_records} 件の未採点レコード）を一括更新しました！", icon="✅")
                                st.rerun()
                                
                            except Exception as bulk_err:
                                st.error(f"❌ 一括更新エラー: {bulk_err}")
                        elif bulk_confirmed is False:
                            del st.session_state["pending_bulk_update"]
                            st.rerun()
            else:
                st.info("対象の採点データが見つかりません。")

    except Exception as group_err:
        st.error(f"データ取得中にエラーが発生しました: {group_err}")


# ==============================================================================
# 🛠️ テストモード限定：採点問題テーブル全削除する処理
# ==============================================================================
def show_danger_zone_test_tools(supabase):
    """
    🚨【テスト環境専用】問題テーブル全削除ツール
    """
    # 💡 関数内部でローカルインポートすることで、リリース時の剥ぎ取りを最速化
    import time
    
    # 💡 管制塔(app.py)の動的トグル「is_test_mode_active」セッション状態と完全連動！
    if not st.session_state.get("is_test_mode_active", False):
        return

    st.markdown("---")
    st.markdown("### 🛠️ 管理者テスト用デバッグツール")
    st.caption("※このエリアは「🛠️ テスト用デバッグ」タブのトグルスイッチがオンの時だけ自動的に露出します。")

    # 二重の安全ロック（確認チェックボックス）
    danger_check = st.checkbox("⚠️ 本当に全ての問題データを完全に削除してもよろしいですか？（元に戻せません）", key="test_danger_delete_check")
    
    if st.button("🔥 問題テーブルの全データを物理削除する", key="test_all_delete_btn", use_container_width=True, disabled=not danger_check):
        try:
            with st.spinner("データベースを初期化中..."):
                # 主キー（saiten_question_id）が 0 より大きいもの（＝全件）を物理削除
                supabase.table("tbl_scoring_question_management") \
                    .delete() \
                    .gt("saiten_question_id", 0) \
                    .execute()
                
                # 操作監査ログにミリ秒刻印
                try:
                    insert_operation_log(
                        supabase=supabase,
                        operator_id=st.session_state.get("user_id", "TEST_ADMIN"),
                        action_type="TEST_DEBUG_TRUNCATE",
                        target_id="ALL_RECORDS",
                        description="【テストモード】管理者がデバッグツールを使用して問題管理テーブルの全件物理削除を実行。"
                    )
                except Exception:
                    pass
                
                st.success("💥 問題管理テーブルの全データを正常に物理削除・初期化しました！")
                time.sleep(1.5)
                st.rerun()
                
        except Exception as e:
            st.error(f"物理削除の実行に失敗しました: {e}")
