import streamlit as st
import pandas as pd
import time
import os
from datetime import datetime, timezone
from supabase import create_client

# 🎯 【画面幅の拡張】ワイド解像度（1920x1200）をフル活用
st.set_page_config(
    page_title="試験データ管理システム", 
    page_icon="📊", 
    layout="wide"
)

# ⚙️ settingsモジュールのインポート
try:
    import settings
except ImportError:
    st.error("❌ settings.py が見つかりません。設定ファイルを作成してください。")
    st.stop()

# 🛡️ 設定ファイルからタブ権限を動的に取得・パースするロジック
def _parse_role_list(val):
    if isinstance(val, list):
        return [int(x) for x in val if str(x).isdigit()]
    if isinstance(val, int):
        return [val]
    if isinstance(val, str):
        return [int(x.strip()) for x in val.split(",") if x.strip().isdigit()]
    return []

if hasattr(settings, "TAB_ROLE_CONFIG") and settings.TAB_ROLE_CONFIG:
    TAB_ACCESS = {k: _parse_role_list(v) for k, v in settings.TAB_ROLE_CONFIG.items()}
else:
    TAB_ACCESS = {"tab1": [], "tab2": [], "tab3": [], "tab4": [], "tab5": [], "tab6": [], "tab7": []}


# 🌐 1. Supabaseの接続および画像ストレージURLの環境変数・シークレット一括ロード
@st.cache_resource
def init_connection():
    """
    🌐 Supabase接続設定（Render環境変数およびシークレットを完全パース）
    """
    # 💡 Renderの「Environment」から前後の空白を完全に剥ぎ取って安全にロード
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()
    storage_url = os.environ.get("SUPABASE_STORAGE_URL", "").strip()

    # 🚨 もし環境変数が空っぽだった場合、ローカルPC開発環境（secrets.toml）から安全に引き抜く
    if not url or not key:
        try:
            url = st.secrets["SUPABASE_URL"].strip()
            key = st.secrets["SUPABASE_KEY"].strip()
            # secrets.toml にSTORAGEキーがあるか安全にチェック
            if "SUPABASE_STORAGE_URL" in st.secrets:
                storage_url = st.secrets["SUPABASE_STORAGE_URL"].strip()
        except Exception:
            raise RuntimeError(
                "❌ Supabaseの認証情報またはストレージURLが見つかりません。"
                "Renderの環境変数（Environment Variables）に「SUPABASE_URL」と「SUPABASE_KEY」、"
                "および「SUPABASE_STORAGE_URL」が正しく登録されているか今一度確認してください。"
            )

    # 💡 【今回の最重要マージ】取得した最新のストレージURLを、システム全体が参照する settings クラスへ動的注入！
    # これにより、各採点子画面（question_list.py や hold_management.py）内の `settings.STORAGE_BASE_URL` が
    # 自動的にこの環境変数の値へと寸分の狂いもなくリアルタイムに同期されます。
    if storage_url:
        settings.STORAGE_BASE_URL = storage_url

    return create_client(url, key)


# 🌐 Supabase クライアント初期化実行
if not hasattr(st.session_state, "supabase_client"):
    try:
        supabase = init_connection()
        st.session_state["supabase_client"] = supabase
    except Exception as e:
        st.error(f"❌ Supabaseの初期化に失敗しました: {e}")
        st.stop()
else:
    supabase = st.session_state["supabase_client"]

# 🎨 共通コンポーネント：個別更新・一括更新用のポップアップ確認パネル関数
def display_confirm_panel(message, pending_key, container=st):
    """確認ダイアログを表示し、はい/いいえの選択結果を返します"""
    with container.container(border=True):
        st.warning(message)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅ はい、実行します", key=f"confirm_yes_{pending_key}", use_container_width=True):
                return True
        with c2:
            if st.button("❌ いいえ、戻ります", key=f"confirm_no_{pending_key}", use_container_width=True):
                return False
    return None

# 🔐 1. 未ログイン時の制御
if not st.session_state.get("logged_in", False):
    from views.login_view import show_login_page
    show_login_page(supabase, settings)

# 📄 2. ログイン成功後の全共通ガード＆ルーティング処理
else:
    current_user_id = st.session_state.get("user_id")
    current_user_name = st.session_state.get("user_name")
    current_role_id = st.session_state.get("role_id", None)

    # ⏱️ 共通自動ログアウトシステム
    current_time = time.time()
    last_activity = st.session_state.get("last_activity_time")
    
    if last_activity is not None:
        elapsed_time = current_time - last_activity
        if elapsed_time > settings.LOGIN_TIMEOUT_SECONDS:
            try:
                if current_user_id:
                    supabase.table("graders").update({"login_status": False}).eq("grader_id", current_user_id).execute()
                    supabase.table("tbl_scoring_question_management") \
                        .update({"is_locked": False, "locked_by_webid": None, "locked_at": None}) \
                        .eq("locked_by_webid", current_user_id).execute()
            except Exception:
                pass
            
            # 🚨 【自動リセット対策】放置時も古い記憶（ステップ位置など）を完全に破壊して初期化
            for key in list(st.session_state.keys()):
                del st.session_state[key]
                
            st.error(f"⏳ 一定時間操作がなかったため、安全のため自動ログアウトし、編集ロックをすべて解放しました。")
            st.rerun()

            
    st.session_state["last_activity_time"] = current_time
    # ─── 📊 共通ヘッダーエリア ───
    col_title, col_logout = st.columns([4, 1])
    with col_title:
        st.markdown("## 📊 試験データ管理システム")
        st.caption(f"ログイン中: {current_user_name} (採点者ID: {current_user_id}) / Role: {current_role_id}")
    with col_logout:
        st.write("") 
        if st.button("ログアウト", use_container_width=True, key="main_logout_btn"):
            try:
                if current_user_id:
                    # 1. 採点者のログインステータスをオフにする
                    supabase.table("graders").update({"login_status": False}).eq("grader_id", current_user_id).execute()
                    
                    # 💡 自分が掴んでいた問題のロックを一括解放
                    supabase.table("tbl_scoring_question_management") \
                        .update({"is_locked": False, "locked_by_webid": None, "locked_at": None}) \
                        .eq("locked_by_webid", current_user_id).execute()
            except Exception:
                pass

            # 🚨 【今回の最重要対策】Streamlitのセッションに記憶されている全変数を完全に消去！
            for key in list(st.session_state.keys()):
                del st.session_state[key]
                
            # 💡 ログアウト完了後に真っ新な状態で強制再起動
            st.rerun()


    # ─── 🛡️ タブ表示権限の動的コントロール ───
    def role_allowed(tab_key, role):
        if role is None:
            return False
        allowed = TAB_ACCESS.get(tab_key, [])
        return role in allowed

    show_tab1 = role_allowed("tab1", current_role_id)
    show_tab2 = role_allowed("tab2", current_role_id)
    show_tab3 = role_allowed("tab3", current_role_id)
    show_tab4 = role_allowed("tab4", current_role_id)
    show_tab5 = role_allowed("tab5", current_role_id)  
    show_tab6 = role_allowed("tab6", current_role_id)  
    show_tab7 = role_allowed("tab7", current_role_id)  

    # 🧪【新設】テストモード用のセッション状態を初期化（デフォルトは本番モード=False）
    if "is_test_mode_active" not in st.session_state:
        st.session_state["is_test_mode_active"] = False

    # 💡 管理者（role_id == 4）の場合のみ、テストモードへ切り替える隠しタブ権限を有効化
    show_tab8 = (current_role_id == 4)

    visible_tab_labels = []
    visible_tab_keys = []
    
    if show_tab1:
        visible_tab_labels.append(settings.LABELS["tab1_label"])
        visible_tab_keys.append("tab1")
    if show_tab2:
        visible_tab_labels.append(settings.LABELS["tab2_group_label"])
        visible_tab_keys.append("tab2")
    if show_tab3:
        visible_tab_labels.append(settings.LABELS.get("tab3_header", "CSV一括取り込み"))
        visible_tab_keys.append("tab3")
    if show_tab4:
        visible_tab_labels.append(settings.LABELS['tab3_title'])
        visible_tab_keys.append("tab4")
    if show_tab5:
        visible_tab_labels.append("📥 採点用データ管理")
        visible_tab_keys.append("tab5")
    if show_tab6:
        visible_tab_labels.append("📤 採点完了データ出力") 
        visible_tab_keys.append("tab6")                         
    if show_tab7:
        visible_tab_labels.append("🟣 保留（H）レコード管理")
        visible_tab_keys.append("tab7")
    
    # 👑【新設】8番目のテスト管理タブを動的にマウント
    if show_tab8:
        visible_tab_labels.append("🛠️ テスト用デバッグ")
        visible_tab_keys.append("tab8")

    # ─── 🔄 タブの生成と画面呼び出し ───
    if visible_tab_labels:
        tab_objs = st.tabs(visible_tab_labels)
        tab_map = dict(zip(visible_tab_keys, tab_objs))
        
        if "tab1" in tab_map:
            with tab_map["tab1"]:
                from views.question_list import show_question_list
                show_question_list(supabase, settings, current_user_id, current_role_id)
                
        if "tab2" in tab_map:
            with tab_map["tab2"]:
                from views.progress_mgmt import show_progress_management
                show_progress_management(supabase, settings, display_confirm_panel)
                
        if "tab3" in tab_map:
            with tab_map["tab3"]:
                from views.data_io_mgmt import show_csv_import
                show_csv_import(supabase, settings)
                
        if "tab4" in tab_map:
            with tab_map["tab4"]:
                from views.data_io_mgmt import show_file_io_sample
                show_file_io_sample(settings)
                
        if "tab5" in tab_map:
            with tab_map["tab5"]:
                from views.data_io_mgmt import show_data_io_management
                show_data_io_management(supabase, settings)
                
        if "tab6" in tab_map:
            with tab_map["tab6"]:
                from views.data_io_mgmt import show_graded_output
                show_graded_output(supabase)
                
        if "tab7" in tab_map:
            with tab_map["tab7"]:
                from views.hold_management import show_hold_management_page
                show_hold_management_page(supabase, settings, display_confirm_panel, current_user_id)

        # 👑【新設】8番目のタブ：テスト用デバッグ画面の実装
        if "tab8" in tab_map:
            with tab_map["tab8"]:
                st.markdown("### 🛠️ テスト用デバッグ管理")
                st.markdown("このタブは特権管理者（role_id=4）にのみ露出する安全な検証エリアです。")
                
                # 💡 トグルスイッチでテストモードのON/OFFを完全コントロール！
                # 他の画面や settings.py を一切書き換えることなく、この状態がシステム全体に安全連動します。
                is_test = st.toggle(
                    "🧪 テストモードを有効化する（デバッグツールを露出）", 
                    value=st.session_state["is_test_mode_active"],
                    key="toggle_test_mode_switch"
                )
                st.session_state["is_test_mode_active"] = is_test

                if is_test:
                    st.success("🟢 現在テストモードが【有効】です。個別採点ページ等でテスト用の拡張操作が行えます。")
                    
                    # 💡 先ほど作成した「問題テーブル全削除ツール」をここにマウント！
                    # テストモードがONの時だけ、この下に物理削除ボタンが綺麗に出現します。
                    try:
                        from views.progress_mgmt import show_danger_zone_test_tools
                        show_danger_zone_test_tools(supabase)
                    except ImportError:
                        # まだ views/progress_mgmt.py 内に関数を定義していない場合のセーフティフォールバック
                        st.caption("ℹ️ テーブル全削除ロジック関数は、views/progress_mgmt.py 内への配置をお待ちしています。")
                else:
                    st.info("⚪ 現在は通常の本番モードです。検証用の破壊的ツールは安全に封印されています。")
    else:
        st.warning(f"表示対象のタブがありません。管理者にお問い合わせください。(role_id: {current_role_id})")
        tab_map = {}

        
