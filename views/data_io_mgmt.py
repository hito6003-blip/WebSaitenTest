import streamlit as st
import pandas as pd
from datetime import datetime, timezone
import time
from views.log_common import insert_operation_log
import csv 

# この下に関数 def show_csv_import や def show_data_io_management が続く...


def show_csv_import(supabase, settings):
    """
    📄 タブ3: CSVデータ一括取り込み（採点問題管理テーブル用）
    複数CSVの安全パース、最新マッピングへのリネーム、および1,000件単位の分割バルクアップサートを制御します。
    """
    st.header(settings.LABELS.get('tab3_header', 'CSVデータ一括取り込み'))
    st.markdown("**取り込みたいCSVフォルダ内のファイルをすべて選択するか、フォルダをそのまま以下の枠内にドラッグ＆ドロップしてください**")
    
    uploaded_files = st.file_uploader(
        "取り込むCSVフォルダ内のファイルをまとめて選択・ドロップしてください", 
        type=["csv"],
        accept_multiple_files=True,
        key="csv_folder_uploader"
    )

    if uploaded_files and len(uploaded_files) > 0:
        try:
            all_dfs = []  # 読み込んだ各CSVのデータフレームを格納するリスト
            
            for uploaded_file in uploaded_files:
                df_raw = None
                encodings = ['utf-8-sig', 'utf-8', 'shift_jis', 'cp932']
                
                for encoding in encodings:
                    try:
                        uploaded_file.seek(0)
                        # 💡 【改行バグ完全撃退】lineterminatorを削除し、engine='python' にすることで
                        # Windows(\r\n) と Mac(\n) の改行コードの違いを全自動で完璧に判別してパースします
                        df_raw = pd.read_csv(
                            uploaded_file, 
                            encoding=encoding,
                            engine='python'
                        )
                        break
                    except (UnicodeDecodeError, LookupError):
                        continue
                
                if df_raw is None or df_raw.empty:
                    st.warning(f"⚠️ {uploaded_file.name} は読み込めないか、空のためスキップしました。")
                    continue
                
                # 🚨 【今回の最重要対策】見えない制御文字（\r や半角スペース）を列名から100%完全に削ぎ落とす！
                df_raw.columns = df_raw.columns.str.strip()
                
                # 💡 期待されるカラムを「TAOデータ出力日」に変更した最新仕様
                required_columns = [
                    '採点問題ID', 'コンテンツID', 'レスポンス識別子', 
                    '解答内容', '解答内容（文字数）', 
                    '採点者WEBID', '採点者リーダーWEBID',
                    '振り分けデータ作成日', '採点完了日', 'TAOデータ出力日'
                ]
                
                # CSVのカラムをチェック
                missing_columns = [col for col in required_columns if col not in df_raw.columns]
                if missing_columns:
                    st.error(f"❌ {uploaded_file.name} に必須カラムが不足しています: {', '.join(missing_columns)}")
                    return
                
                # 💡 【型指定再読み込み時も改行＆列名トリムを徹底適用】
                uploaded_file.seek(0)
                df_file = pd.read_csv(
                    uploaded_file, 
                    encoding=encoding, 
                    engine='python',
                    dtype={
                        '採点問題ID': str,
                        'コンテンツID': str,
                        'レスポンス識別子': str,
                        '解答内容': str,
                        '解答内容（文字数）': 'Int32',
                        '採点者WEBID': str,
                        '採点者リーダーWEBID': str,
                        'TAOデータ出力日': str
                    }
                )
                # 列名を再度綺麗にトリム
                df_file.columns = df_file.columns.str.strip()
                
                # 日付列形式の統一化（エラーを安全に無視する errors='coerce' を指定）
                df_file['振り分けデータ作成日'] = pd.to_datetime(df_file['振り分けデータ作成日'], errors='coerce').dt.strftime('%Y-%m-%d')
                df_file['採点完了日'] = pd.to_datetime(df_file['採点完了日'], errors='coerce').dt.strftime('%Y-%m-%d')
                df_file['TAOデータ出力日'] = pd.to_datetime(df_file['TAOデータ出力日'], errors='coerce').dt.strftime('%Y-%m-%d')
                
                all_dfs.append(df_file)
            
            if not all_dfs:
                st.warning("有効なCSVデータがありませんでした。")
                return
                
            df = pd.concat(all_dfs, ignore_index=True)

            # 前回の登録メッセージを表示
            if st.session_state.get('csv_import_message'):
                msg = st.session_state['csv_import_message']
                msg_type = st.session_state.get('csv_import_message_type', 'success')
                if msg_type == 'success': st.success(msg)
                elif msg_type == 'warning': st.warning(msg)
                elif msg_type == 'error': st.error(msg)
                else: st.info(msg)
            st.write(f"📂 読み込んだファイル数: **{len(uploaded_files)} 個**")
            st.write(f"📊 **全ファイル合計プレビュー: {len(df)}件のデータが統合されました**")
            preview_df = df.copy()
            preview_df.index = range(1, len(preview_df) + 1)
            st.dataframe(preview_df, use_container_width=True)
            
            if not df.empty:
                if st.button(settings.LABELS.get('db_insert_btn', '全ファイルをまとめて登録'), key="tab3_insert_btn", use_container_width=True):
                    # 💡 最新のDBカラム名へマッピング変換（TAOデータ出力日 ➡️ tao_outdate 対応）
                    rename_dict = {
                        '採点問題ID': 'saiten_question_id',
                        'コンテンツID': 'contents_id',
                        'レスポンス識別子': 'response_id',
                        '解答内容': 'answer',
                        '解答内容（文字数）': 'answer_strnum',
                        '採点者WEBID': 'checker_webid',
                        '採点者リーダーWEBID': 'leader_webid',
                        '振り分けデータ作成日': 'saiten_file_out_date',
                        '採点完了日': 'grading_comp_date',
                        'TAOデータ出力日': 'tao_outdate'
                    }
                    
                    df_db = df.rename(columns=rename_dict)
                    
                    # 文字列の両端の空白を安全にトリム（💡 解答内容の内側の改行は維持しつつトリムします）
                    df_db = df_db.apply(
                        lambda col: col.map(lambda x: x.strip() if isinstance(x, str) else x)
                        if col.dtype == object else col
                    )
                    
                    if 'checker_webid' in df_db.columns: df_db['checker_webid'] = df_db['checker_webid'].astype(str)
                    if 'leader_webid' in df_db.columns: df_db['leader_webid'] = df_db['leader_webid'].astype(str)
                    
                    # 主キーの重複削除（ファイル間重複を完全にマージ）
                    df_db = df_db.drop_duplicates(subset=['saiten_question_id'])

                    # 主キーの欠損（妥当性）チェック
                    null_pk = df_db[df_db['saiten_question_id'].isna() | (df_db['saiten_question_id'] == '')]
                    if len(null_pk) > 0:
                        message = f"採点問題IDが空の行が {len(null_pk)} 件あります。これらの行は登録できません。"
                        st.session_state['csv_import_message'] = message
                        st.session_state['csv_import_message_type'] = 'error'
                        st.error(message)
                    else:
                        records = df_db.where(pd.notnull(df_db), None).to_dict(orient="records")
                        duplicate_count = len(df) - len(df_db)

                        with st.spinner("すべてのデータを一括登録中..."):
                            try:
                                # ⚡ 1,000件ずつの安全分割コミットロジック（Gateway Timeoutの粉砕）
                                chunk_size = 1000
                                total_inserted = 0
                                for i in range(0, len(records), chunk_size):
                                    chunk = records[i:i + chunk_size]
                                    supabase.table("tbl_scoring_question_management").upsert(
                                        chunk, on_conflict="saiten_question_id", ignore_duplicates=False
                                    ).execute()
                                    total_inserted += len(chunk)

                                success_parts = [f"合計 {total_inserted}件のデータの取り込みが完了しました"]
                                if duplicate_count > 0:
                                    success_parts.append(f"（ファイル間での重複行 {duplicate_count} 件は統合マージしました）")
                                success_message = "。".join(success_parts)
                                
                                # 📝 【操作ログ後付け実装】一括取り込みの成功を監査ログへ刻印
                                current_user_id = st.session_state.get("user_id", "UNKNOWN_USER")
                                uploaded_filenames = [f.name for f in uploaded_files]
                                
                                try:
                                    from views.log_common import insert_operation_log
                                    insert_operation_log(
                                        supabase=supabase,
                                        operator_id=current_user_id,
                                        action_type="CSV_IMPORT",
                                        target_id=f"FILES_{len(uploaded_files)}",
                                        description=f"CSV一括インポート成功（統合件数: {total_inserted}件, 重複マージ: {duplicate_count}件）。対象ファイル一覧: {', '.join(uploaded_filenames)}"
                                    )
                                except Exception:
                                    pass # ログ書き込みの失敗でメイン処理を阻害しないための安全弁
                                
                                st.session_state['csv_import_message'] = success_message
                                st.session_state['csv_import_message_type'] = 'success'
                                st.success(success_message)
                                
                            except Exception as db_error:
                                error_message = f"データベース登録エラー: {str(db_error)}"
                                st.session_state['csv_import_message'] = error_message
                                st.session_state['csv_import_message_type'] = 'error'
                                st.error(error_message)
                                
        except Exception as e:
            error_message = f"ファイル読み込みエラーが発生しました: {str(e)}"
            st.session_state['csv_import_message'] = error_message
            st.session_state['csv_import_message_type'] = 'error'
            st.error(error_message)

def show_file_io_sample(settings):
    """
    📄 タブ4: ファイルDL＆UP
    """
    st.title(settings.LABELS['tab3_title'])
    st.markdown(settings.LABELS['tab3_upload_section'])
    uploaded_file = st.file_uploader(settings.LABELS['file_uploader'], type=["csv", "txt", "xlsx"])

    if uploaded_file is not None:
        st.success(f"アップロード完了: {uploaded_file.name}")
        bytes_data = uploaded_file.getvalue()
        st.write("ファイルサイズ:", len(bytes_data), "bytes")

    st.header(settings.LABELS['download_header'])
    sample_text = "これはサンプルテキストです。\n必要に応じて内容を更新して保存してください。"
    st.download_button(
        settings.LABELS['download_btn'], sample_text, file_name='sample.txt', mime='text/plain'
    )

def show_data_io_management(supabase, settings):
    """
    📥 タブ5: 採点用データ出力・結果アップロード
    """
    st.header("📥 採点用データ管理（出力＆AI結果反映）")
    
    # 📋 機能を2つのサブタブに綺麗に分割
    tab5_sub1, tab5_sub2 = st.tabs(["CSV出力機能", "結果CSVアップロード登録"])
    
    # ==========================================================
    # 🟢 サブタブ1: CSV出力機能 (解答内容のダブルクォーテーション強制ラッピング)
    # ==========================================================
    with tab5_sub1:
        st.markdown("##### 1. 未処理データの出力")
        st.markdown("`tbl_scoring_question_management` テーブルから、AI採点判定（`ai_judge_mark`）が**未登録（Null）**のレコードを抽出してCSV形式でダウンロードします。")
        
        if st.button("🔍 対象データを抽出・プレビュー", key="tab5_fetch_btn", use_container_width=True):
            with st.spinner("データベースから未登録データを抽出中..."):
                try:
                    response = supabase.table("tbl_scoring_question_management") \
                        .select("saiten_question_id, response_id, answer, answer_strnum, ai_judge_mark") \
                        .is_("ai_judge_mark", "null") \
                        .execute()

                    if response.data and len(response.data) > 0:
                        df_export = pd.DataFrame(response.data)
                        
                        target_columns = ["saiten_question_id", "response_id", "answer", "answer_strnum"]
                        df_export = df_export[target_columns]

                        st.success(f"🎯 未登録のレコードが **{len(df_export)} 件** 見つかりました。")
                        
                        # 💡 【エラー100%永久消滅 ＆ バックスラッシュゼロ型・完全テキスト組み立て仕様】
                        # ライブラリの制約を完全にバイパスし、プレーンな文字列結合だけでCSVを1行ずつ直接生成します
                        csv_lines = []
                        
                        # ① ヘッダー行をカンマ区切りで作成
                        csv_lines.append(",".join(target_columns))
                        
                        # ② データ行をループで1行ずつ確実にテキスト合成
                        for _, row_data in df_export.iterrows():
                            s_id = str(row_data.get("saiten_question_id", "")).strip()
                            r_id = str(row_data.get("response_id", "")).strip()
                            ans_num = str(row_data.get("answer_strnum", "")).strip()
                            
                            # 💡 解答内容（answer）を取得し、文章内の「"」をCSVの国際ルールに合わせて「""」に安全置換
                            raw_ans = row_data.get("answer")
                            if pd.isna(raw_ans) or str(raw_ans).strip() == "":
                                ans_val = '""'
                            else:
                                # 🚨 手動の replace("\\", "") を撤廃し、DB内の綺麗なデータを100%そのまま活かします
                                # 文章内のダブルクォートだけを二重化して、前後は純粋なダブルクォーテーションでガッチリ囲みます
                                clean_ans = str(raw_ans).replace('"', '""')
                                ans_val = f'"{clean_ans}"'
                            
                            # 🎯 ご指定通り answer の列だけを囲んだ状態で、1行を合成
                            csv_lines.append(f"{s_id},{r_id},{ans_val},{ans_num}")
                        
                        # 💡 行同士の区切りを Windows 標準の「\r\n（CRLF）」でガッチャンコ！
                        # これにより、WindowsのエディタやExcelが改行コードを勘違いして「\」を表示するのを徹底ガードします
                        csv_data = "\r\n".join(csv_lines) + "\r\n"
                        
                        current_time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                        filename = f"scoring_data_unprocessed_{current_time_str}.csv"

                        # 📝 【操作ログ】CSVが抽出・ダウンロードされたアクションを監査ログに刻印
                        current_user_id = st.session_state.get("user_id", "UNKNOWN_USER")
                        try:
                            insert_operation_log(
                                supabase=supabase,
                                operator_id=current_user_id,
                                action_type="CSV_EXPORT",
                                target_id=filename,
                                description=f"管理者による未処理データのCSV抽出成功。対象件数: {len(df_export)}件（解答内容のカラムをダブルクォーテーションで強制保護）。"
                            )
                        except Exception:
                            pass

                        st.download_button(
                            label="📥 抽出データをCSVでダウンロード",
                            data=csv_data,
                            file_name=filename,
                            mime="text/csv",
                            use_container_width=True,
                            key="tab5_download_btn"
                        )

                        st.markdown("##### 📊 抽出データプレビュー（先頭最大100件）")
                        df_preview = df_export.head(100).copy()
                        df_preview.index = df_preview.index + 1
                        st.dataframe(df_preview, use_container_width=True)
                    else:
                        st.info("✨ 現在、AI採点判定（`ai_judge_mark`）が未登録のレコードはありません。")

                except Exception as e:
                    st.error(f"データ抽出エラーが発生しました: {e}")
                    
    # ==============================================================================
    # 🔵 サブタブ2: 結果CSVアップロード登録 (AI 4項目のダブルクォーテーション囲み対応)
    # ==============================================================================
    with tab5_sub2:
        st.markdown("##### 2. 採点結果CSV of アップロード登録")
        st.markdown("AIの採点結果（`ai_cp1~3`, `ai_reason`）がダブルクォーテーションで囲まれた状態のCSVファイルをアップロードし、データベースを更新します。")
        st.info("⚠️ 取り込み可能なCSVファイルは **UTF-8（BOMあり）形式のみ** です。")
        
        required_cols_tab5 = [
            "saiten_question_id", "response_id", "ai_cp1", "ai_cp2", "ai_cp3", "ai_reason", "ai_judge_mark"
        ]
        
        with st.expander("詳細なCSVフォーマット要件を確認する"):
            st.write("以下の**7つのカラム**がすべて含まれている必要があります（並び順は自由です）。")
            st.code(",".join(required_cols_tab5))
        
        uploaded_result_file = st.file_uploader(
            "採点結果CSVファイルを選択してください", 
            type=["csv"],
            key="tab5_result_csv_uploader"
        )

        if uploaded_result_file is not None:
            try:
                df_result_raw = None
                try:
                    uploaded_result_file.seek(0)
                    # 💡 【確定仕様】engine='python' にすることで、CSV側であらかじめダブルクォーテーションで
                    # 囲まれた内側にある複雑な改行文章を、ちぎらずに1つのセルとして完璧にパース・ロードします。
                    df_result_raw = pd.read_csv(
                        uploaded_result_file, 
                        encoding='utf-8-sig',
                        engine='python'
                    )
                except (UnicodeDecodeError, LookupError):
                    st.error("❌ ファイルの文字コードが正しくありません。UTF-8（BOMあり）形式のCSVファイルをアップロードしてください。")
                
                if df_result_raw is not None:
                    # 🚨 見えない制御文字（\r や半角スペース）を列名から100%完全に削ぎ落とす！
                    df_result_raw.columns = df_result_raw.columns.str.strip()
                    
                    if df_result_raw.empty:
                        st.warning("⚠️ アップロードされたファイルは空です。")
                    else:
                        missing_cols = [col for col in required_cols_tab5 if col not in df_result_raw.columns]
                        if missing_cols:
                            st.error(f"❌ 必須カラムが不足しています: {', '.join(missing_cols)}")
                        else:
                            uploaded_result_file.seek(0)
                            df_result = pd.read_csv(
                                uploaded_result_file, 
                                encoding='utf-8-sig', 
                                engine='python', # 💡 ここでも改行ちぎれバグを徹底ガード
                                dtype={
                                    'saiten_question_id': str, 'response_id': str, 'ai_cp1': str,
                                    'ai_cp2': str, 'ai_cp3': str, 'ai_reason': str, 'ai_judge_mark': str
                                }
                            )
                            # 列名を再度綺麗にトリム
                            df_result.columns = df_result.columns.str.strip()

                            # 文字列の両端の空白を安全にトリム（💡 セルの内側にある改行は維持されます）
                            df_result = df_result.apply(
                                lambda col: col.map(lambda x: x.strip() if isinstance(x, str) else x)
                                if col.dtype == object else col
                            )
                            df_result = df_result.drop_duplicates(subset=['saiten_question_id'])

                            st.write(f"📊 **読み込みプレビュー: {len(df_result)} 件のデータが正常にパースされました**")
                            st.dataframe(df_result.head(100), use_container_width=True)

                            if st.button("🔥 採点結果をデータベースに登録（一括更新）", key="tab5_insert_btn", use_container_width=True):
                                null_pk_result = df_result[df_result['saiten_question_id'].isna() | (df_result['saiten_question_id'] == '')]
                                
                                if len(null_pk_result) > 0:
                                    st.error(f"❌ `saiten_question_id` が空の行が {len(null_pk_result)} 件あります。これらは登録できません。")
                                else:
                                    csv_ids = df_result['saiten_question_id'].tolist()
                                    total_csv_count = len(csv_ids)
                                    
                                    with st.spinner("データの整合性を検証中..."):
                                        response = supabase.table("tbl_scoring_question_management") \
                                            .select("saiten_question_id") \
                                            .in_("saiten_question_id", csv_ids) \
                                            .execute()
                                        
                                        db_existing_ids = {row["saiten_question_id"] for row in response.data}
                                        total_db_count = len(db_existing_ids)
                                    
                                    if total_csv_count != total_db_count:
                                        missing_in_db = [id for id in csv_ids if id not in db_existing_ids]
                                        st.error(f"❌ 更新対象のデータがデータベースに存在しないため、処理を中断しました。新規追加は許可されていません。")
                                        st.error(f"💡 存在しない採点問題ID（先頭最大5件を表示）: {', '.join(missing_in_db[:5])}")
                                        st.warning(f"⚠️ CSVの件数（{total_csv_count}件）に対し、DBに存在するデータは（{total_db_count}件）しかありませんでした。")
                                    else:
                                        # 💡 現在の正確なUTC日時をISOフォーマット（末尾Z）で取得して更新日時を付与
                                        current_utc_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                                        df_result['updated_at'] = current_utc_time

                                        records_to_update = df_result.where(pd.notnull(df_result), None).to_dict(orient="records")
                                        
                                        with st.spinner("Supabaseのデータを更新中..."):
                                            chunk_size = 1000
                                            total_updated = 0
                                            
                                            for i in range(0, len(records_to_update), chunk_size):
                                                chunk = records_to_update[i:i + chunk_size]
                                                supabase.table("tbl_scoring_question_management").upsert(
                                                    chunk, on_conflict="saiten_question_id", ignore_duplicates=False
                                                ).execute()
                                                total_updated += len(chunk)
                                            
                                            # 📝 【操作ログ】AI結果インポート成功の履歴を監査ログへ刻印
                                            current_user_id = st.session_state.get("user_id", "UNKNOWN_USER")
                                            try:
                                                from log_common import insert_operation_log
                                                insert_operation_log(
                                                    supabase=supabase,
                                                    operator_id=current_user_id,
                                                    action_type="AI_RESULT_IMPORT",
                                                    target_id=str(uploaded_result_file.name),
                                                    description=f"AI採点結果CSVのインポート成功（対象: {total_updated} 件）。ダブルクォーテーションで保護されたAI理由・チェックポイントをパースしてDBを確定しました。"
                                                )
                                            except Exception:
                                                pass
                                            
                                            st.success(f"✅ 合計 {total_updated} 件のAI採点結果（更新日時含む）を正常に反映・更新しました！")
                                            
                                            import time as time_module
                                            time_module.sleep(1.0)
                                            st.rerun()
                                                        
            except Exception as upload_err:
                st.error(f"❌ ファイル処理中にエラーが発生しました: {str(upload_err)}")

# 💡 views/data_io_mgmt.py の一番最後（ファイルの最末尾）にこれをそのまま貼り付けてください
def show_graded_output(supabase):
    """
    📤 タブ6: 採点完了データ出力 (分割お渡し・完全版 1/4)
    採点完了日単位で全ての採点が終わっている日のみを抽出し、
    複数日を選択して日付昇順で1本の高精度クレンジングCSVとして一括出力する画面です。
    """
    st.header("📤 採点完了データ出力")
    st.markdown("採点完了日ごとに集計を行い、**すべての採点が完了している日付のデータのみ**を選択してCSVダウンロードできます。")

    # 1. 🔄 画面ロード時に最新の全データを一気に対称ロードしてメモリ上で集計
    with st.spinner("データベースから最新の採点状況を集計中..."):
        try:
            # 仕様書通り「contents_id」を確実に含めてロード
            all_res = supabase.table("tbl_scoring_question_management") \
                .select(
                    "saiten_question_id, response_id, contents_id, judge_mark_result, final_approver_id, "
                    "ai_cp1, ai_cp2, ai_cp3, ai_reason, ai_judge_mark, grading_comp_date"
                ) \
                .execute()
            all_data = all_res.data or []
            
            # 操作ログから「過去に出力確定された日付」を逆引き
            exported_dates_set = set()
            try:
                log_res = supabase.table("tbl_operation_logs") \
                    .select("target_id") \
                    .eq("action_type", "EXPORT_GRADED_DATA") \
                    .execute()
                if log_res.data:
                    for log_row in log_res.data:
                        t_id = log_row.get("target_id")
                        if t_id:
                            for d_part in str(t_id).split(","):
                                exported_dates_set.add(d_part.strip())
            except Exception:
                pass # ログテーブル読み込み不可時のセーフティ

        except Exception as e:
            st.error(f"データの読み込みに失敗しました: {e}")
            return

    if not all_data:
        st.info("💡 データベースにレコードが存在しません。")
        return

    df_all = pd.DataFrame(all_data)
    df_all["grading_comp_date_clean"] = df_all["grading_comp_date"].fillna("（未設定）").astype(str).str.strip()

    # 2. 📊 採点完了日単位での4大メトリクス集計
    summary_records = []
    unique_dates = sorted([d for d in df_all["grading_comp_date_clean"].unique() if d != "（未設定）"])

    for comp_date in unique_dates:
        df_sub = df_all[df_all["grading_comp_date_clean"] == comp_date]
        
        total_cnt = len(df_sub)
        graded_cnt = len(df_sub[df_sub["judge_mark_result"].isin(["O", "X", "*"])])
        hold_cnt = len(df_sub[df_sub["judge_mark_result"] == "H"])
        unprocessed_cnt = total_cnt - (graded_cnt + hold_cnt)
        
        if graded_cnt == total_cnt and total_cnt > 0:
            status_text = "✅ 出力可能（全件完了）"
        else:
            reasons = []
            if unprocessed_cnt > 0:
                reasons.append(f"未採点 {unprocessed_cnt}問")
            if hold_cnt > 0:
                reasons.append(f"保留 {hold_cnt}問")
            status_text = f"❌ 出力不可（{'/'.join(reasons)} 残り）"
        
        status_output = "📤 出力済" if comp_date in exported_dates_set else "⏳ 未出力"

        summary_records.append({
            "選択": False, 
            "採点完了日": comp_date,
            "総採点数": total_cnt,
            "採点済数": graded_cnt,
            "未採点数": unprocessed_cnt,
            "保留数": hold_cnt,
            "出力判定ステータス": status_text, 
            "データ出力": status_output
        })

    if not summary_records:
        st.info("💡 集計対象となる有効な採点完了日（日付データ）がありません。")
        return

    df_summary = pd.DataFrame(summary_records)

    st.markdown("### 📅 採点完了日ごとの集計一覧")

    # 表示フィルター
    filter_col1, _ = st.columns(2)
    with filter_col1:
        status_filter = st.radio(
            "🔍 表示フィルター",
            ["すべて表示", "🟢 出力可能 のみ", "🔴 出力不可 のみ"],
            horizontal=True,
            key="tab6_status_filter_radio"
        )
    
    if status_filter == "🟢 出力可能 のみ":
        df_display = df_summary[df_summary["出力判定ステータス"].str.contains("✅")]
    elif status_filter == "🔴 出力不可 のみ":
        df_display = df_summary[df_summary["出力判定ステータス"].str.contains("❌")]
    else:
        df_display = df_summary

    st.caption(f"表示中: {len(df_display)} 件 / 全件: {len(df_summary)} 件")

    # 3. 🗂️ チェックボックス選択エディタ
    edited_df = st.data_editor(
        df_display,
        hide_index=True,
        disabled=["採点完了日", "総採点数", "採点済数", "未採点数", "保留数", "出力判定ステータス", "データ出力"],
        use_container_width=True,
        key="tab6_date_editor"
    )

    if "tab6_selected_valid_df" not in st.session_state:
        st.session_state["tab6_selected_valid_df"] = None
        st.session_state["tab6_target_dates"] = []

    # 選択データをCSV生成ステージに送り込む
    if st.button("🔍 選択した日付の採点完了データを抽出する", key="tab6_extract_action_btn", use_container_width=True):
        selected_dates = edited_df[edited_df["選択"] == True]["採点完了日"].tolist()

        if not selected_dates:
            st.warning("⚠️ 出力したい採点完了日の「選択」チェックボックスにチェックを入れてください。")
            st.session_state["tab6_selected_valid_df"] = None
        else:
            valid_dates = edited_df[(edited_df["選択"] == True) & (edited_df["出力判定ステータス"].str.contains("✅"))]["採点完了日"].tolist()
            invalid_selected = set(selected_dates) - set(valid_dates)
            
            if invalid_selected:
                error_details = [f"・{inv} ： {edited_df[edited_df['採点完了日'] == inv]['出力判定ステータス'].values}" for inv in invalid_selected]
                st.error("❌ 選択された日付の中に、まだ採点作業が完了していない日付が含まれています。")
                for detail in error_details:
                    st.markdown(f"**{detail}**")
                st.session_state["tab6_selected_valid_df"] = None
            else:
                df_selected_raw = df_all[df_all["grading_comp_date_clean"].isin(valid_dates)]
                # 【要件】採点完了日の昇順 ＆ saiten_question_id の昇順でソート
                df_valid_sorted = df_selected_raw.sort_values(by=["grading_comp_date_clean", "saiten_question_id"], ascending=[True, True])
                
                st.session_state["tab6_selected_valid_df"] = df_valid_sorted
                st.session_state["tab6_target_dates"] = valid_dates
                st.rerun()

    # 4. 🖨️ 指定レイアウトでの完璧なCSVテキスト生成
    df_valid_sorted = st.session_state.get("tab6_selected_valid_df")
    valid_dates = st.session_state.get("tab6_target_dates", [])
    if df_valid_sorted is not None and not df_valid_sorted.empty:
        st.success(f"🎯 出力準備完了: **{len(df_valid_sorted)} 件** （対象日: {valid_dates}）")

        csv_lines = []
        # 📋【新仕様】ご提示いただいた全11項目の日本語ヘッダーを完全再現
        headers = [
            "採点問題ID", "レスポンス識別子", "コンテンツID", "採点結果", "採点確定者：WEBID",
            "採点メモ", "AI採点CheckPoint1", "AI採点CheckPoint2", "AI採点CheckPoint3", "AI採点判定理由", "AI採点判定結果"
        ]
        csv_lines.append(",".join(headers))

        for _, row in df_valid_sorted.iterrows():
            # 1. 採点問題ID (text)
            s_id = str(row.get("saiten_question_id", ""))
            # 2. レスポンス識別子 (text)
            r_id = str(row.get("response_id", ""))
            # 3. コンテンツID (text)
            c_id = str(row.get("contents_id", "")) if pd.notna(row.get("contents_id")) else ""
            # 4. 採点結果 (text)
            j_res = str(row.get("judge_mark_result", ""))
            
            # 5. 採点確定者：WEBID (文字列IDも数値も安全に落とし込むクレンジング)
            raw_app_id = row.get("final_approver_id")
            if pd.isna(raw_app_id) or str(raw_app_id).strip() in ["", "None", "null"]:
                app_id = ""
            else:
                app_id_str = str(raw_app_id).strip()
                app_id = app_id_str[:-2] if app_id_str.endswith(".0") else app_id_str

            # 内側の改行・ダブルクォーテーションを安全エスケープして外側を確実に包む
            def clean_quote(val):
                raw = str(val).replace('"', '""') if pd.notna(val) else ""
                return f'"{raw}"'

            # 6. 【追加】採点メモ (text) をダブルクォーテーションで囲む
            memo_clean = clean_quote(row.get("memo", ""))

            # 7〜10. 指定のテキスト項目をダブルクォーテーションで確実に囲む
            cp1_clean = clean_quote(row.get("ai_cp1", ""))
            cp2_clean = clean_quote(row.get("ai_cp2", ""))
            cp3_clean = clean_quote(row.get("ai_cp3", ""))
            reason_clean = clean_quote(row.get("ai_reason", ""))
            
            # 11. AI採点判定結果 (text)
            ai_judge = str(row.get("ai_judge_mark", "")) if pd.notna(row.get("ai_judge_mark")) else ""

            # 1本の物理カンマ区切り1行へ美しく組み立て
            line = f"{s_id},{r_id},{c_id},{j_res},{app_id},{memo_clean},{cp1_clean},{cp2_clean},{cp3_clean},{reason_clean},{ai_judge}"
            csv_lines.append(line)

        # Windows標準の「\r\n」で完全結合してバックスラッシュの混入を絶対防止
        final_csv_string = "\r\n".join(csv_lines)
        final_csv_bytes = final_csv_string.encode("utf-8-sig")

        current_time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"graded_all_p_output_{current_time_str}.csv"

        st.markdown("---")
        st.write("📢 **ステップ1: ファイルのダウンロード**")
        st.download_button(
            label="📥 選択した採点完了データを結合CSVでダウンロード",
            data=final_csv_bytes,
            file_name=filename,
            mime="text/csv",
            use_container_width=True,
            key="tab6_final_download_btn"
        )

        st.write("📢 **ステップ2: システムへの完了報告**")
        if st.button("🔥 上記データのダウンロード完了をシステムに確定報告（出力済に更新）", key="tab6_commit_btn", use_container_width=True):
            try:
                with st.spinner("操作監査ログに一括確定を刻印中..."):
                    login_user = st.session_state.get("user_id", "ADMIN_USER")
                    valid_pkey_list = df_valid_sorted["saiten_question_id"].tolist()

                    try:
                        from views.log_common import insert_operation_log
                    except ImportError:
                        try:
                            from log_common import insert_operation_log
                        except ImportError:
                            insert_operation_log = None

                    if insert_operation_log:
                        insert_operation_log(
                            supabase=supabase,
                            operator_id=login_user,
                            action_type="EXPORT_GRADED_DATA",
                            target_id=",".join(map(str, valid_dates)),
                            description=f"管理者が採点完了データの一括CSV出力（確定）を実行。対象日付: {valid_dates}、合計出力件数: {len(valid_pkey_list)}件。"
                        )
                    
                    st.session_state["tab6_selected_valid_df"] = None
                    st.session_state["tab6_target_dates"] = []
                    st.toast("✅ 監査操作ログにミリ秒刻印し、出力ステータスを同期しました！", icon="🚀")
                    time.sleep(1.0)
                    st.rerun()

            except Exception as commit_err:
                st.error(f"確定報告処理中にエラーが発生しました: {commit_err}")

        st.markdown("##### 📝 出力対象データ プレビュー (先頭50件)")
        df_preview = df_valid_sorted.copy()
        
        def clean_preview_app_id(val):
            if pd.isna(val) or str(val).strip() in ["", "None", "null"]:
                return ""
            v_str = str(val).strip()
            return v_str[:-2] if v_str.endswith(".0") else v_str

        # 📊 画面プレビューのグリッドも全11項目と完全同期
        df_preview_show = pd.DataFrame({
            "採点問題ID": df_preview["saiten_question_id"],
            "レスポンス識別子": df_preview["response_id"],
            "コンテンツID": df_preview.get("contents_id", "").fillna(""),
            "採点結果": df_preview["judge_mark_result"],
            "採点確定者：WEBID": df_preview["final_approver_id"].apply(clean_preview_app_id),
            "採点メモ": df_preview["memo"].fillna("") if "memo" in df_preview.columns else "",
            "AI採点CheckPoint1": df_preview.get("ai_cp1", "").fillna(""),
            "AI採点CheckPoint2": df_preview.get("ai_cp2", "").fillna(""),
            "AI採点CheckPoint3": df_preview.get("ai_cp3", "").fillna(""),
            "AI採点判定理由": df_preview.get("ai_reason", "").fillna(""),
            "AI採点判定結果": df_preview.get("ai_judge_mark", "").fillna("")
        })
        st.dataframe(df_preview_show.head(50), use_container_width=True, hide_index=True)
