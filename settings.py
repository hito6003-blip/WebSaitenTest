# settings.py などの環境設定ファイル
IS_TEST_MODE = True  # 🧪 ローカルテスト時は True。本番リリース時はここを False にするだけでボタンが消滅します。

# supabaseのストレージURL
STORAGE_BASE_URL = ""

# settings.py
# UI表示文字列を集中管理する辞書をここに置きます。
# 必要に応じてこのファイルだけ編集すれば表示文言を一括変更できます。

LABELS = {
    # ログイン
    "login_title": "## 🔐 採点者ログイン画面",
    "input_id_placeholder": "採点者IDを入力してください",
    "input_pass_placeholder": "パスワードを入力",
    "login_button": "ログイン",

    # タブラベル
    "tab1_label": "採点画面",
    "tab2_group_label": "採点管理画面",
    "tab3_label": "CSVデータ取り込み画面",
    "tab4_label": "ファイルのアップロード & ダウンロード",

    # Tab1
    "tab1_header": "採点画面",
    "tab2_group_header": "採点管理画面",
    "refresh_button": "最新の情報に更新",
    "col_grader": "採点者ID",
    "col_response": "問題ID",
    "col_count": "総採点数",
    "col_ungraded": "未採点数",
    "col_graded": "採点済数",
    "col_action": "採点開始",
    "col_grader_name": "採点担当者",
    "start_success": "🎉 選択されたレスポンス識別子 {resp} の採点を開始します！",

    # Tab2
    "tab2_header": "## CSVファイルのアップロード",
    "csv_uploader": "CSVファイルを選択してください",
    "csv_preview": "・取り込みデータプレビュー（CSV内の全データ: 計 {n} 件）",
    "db_insert_btn": "DBへ登録を実行",
    "db_success": "正常に {n} 件のデータを登録しました！",

    # Tab3
    "tab3_title": "## ファイルの UL & DL",
    "tab3_upload_section": "## 1. ファイルのアップロード",
    "file_uploader": "ファイルを選択してください",
    "download_header": "2. ファイルのダウンロード",
    "download_btn": "サンプルテキストをダウンロード",
}

# タブごとの表示許可ロール（タブ5をリーダー・特権用に設定する例：2=リーダー, 3=運用管理, 4=特権）
# 現場の運用に合わせて "1,2,3,4" など自由に変更してください。
TAB_ROLE_CONFIG = {
    "tab1": "1,2,3,4",
    "tab2": "2,3,4",
    "tab3": "4",
    "tab4": "",
    "tab5": "4" ,
    "tab6": "3,4",
    "tab7": "4"
}

#ログアウトするまでの時間（秒） （例：1800秒 ＝ 30分）
LOGIN_TIMEOUT_SECONDS = 300     #とりあえず15分か？ 　テストの時は300秒    
