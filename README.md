# ドコモメール検索 iPhone v0.3

既存の端末内検索に、受信箱を直接取得する接続画面とIMAP接続サーバーを追加。
GitHub Pagesの画面だけでは受信箱へ接続できません。接続サーバーの公開と秘密情報の設定が必要です。

## できること

- 受信箱を新しい順に10通ずつ取得、期間指定、続きの取得
- 既読にせず、メールを削除・移動しない読み取り専用接続
- 日本語MIMEの件名・送信者・本文を解析
- UIDVALIDITYとUIDを使った重複防止
- 添付ファイルを必要時に取得し端末内へ保存。文字PDFを解析し元メールに紐付けて検索
- 従来の .eml / PDF手動取込、送信者・件名・本文・PDF本文検索
- PDFライブラリが読み込めなくてもメール取込・件名検索は動作

## iPhoneから接続サーバーを用意する

1. [RenderのBlueprint作成](https://dashboard.render.com/select-repo?type=blueprint)をSafariで開き、ログイン。
2. GitHub接続で `mdaidai0503/docomo-mail-search` を選択。
3. `render.yaml` を使用。無料の試用用Webサービス1個を定義している。申込画面で料金・制限を確認。無料サービスは停止後の起動待ちなど制約がある。
4. 以下の環境変数をRenderの非公開設定へ入力。チャットやGitHubのファイルには記入しない。
   - `DOCOMO_USER`: **dアカウントID**（メールアドレスと異なる場合あり）
   - `DOCOMO_PASSWORD`: **IMAP専用パスワード**（dアカウントのログインパスワードではない）
   - `IMPORT_TOKEN`: 自動生成される取込キー。サービスのEnvironment画面で確認
   - `ALLOWED_ORIGIN`: `https://mdaidai0503.github.io`（初期設定済み）
5. 公開後の `https://……onrender.com` をアプリの「接続用サーバーURL」へ入力。
6. `IMPORT_TOKEN` の値をアプリの「取込キー」に入力。このキーで受信メールにアクセスできるため共有しない。
7. 初回は検索条件の開始日・終了日を空欄にし、「受信箱から10通取り込む」を押す。
8. 「続きの10通を取り込む」で過去のメールも取得。再取得は重複登録しない。

ドコモ側で「dアカウントでドコモメールを利用」が有効であることを確認。
既存のIMAP専用パスワードを確認して使用する。再発行すると既存メールアプリの設定変更が必要になる場合がある。
接続先はサーバーから `imap.spmode.ne.jp:993`（TLS）に固定。

## 検索と添付

- 件名のFAX番号を探す場合は対象「件名」。取込後に検索する。
- 添付ファイル名も対象「すべて」で検索できる。
- 結果を開き添付の「取得」を押すとダウンロード。PDFなら文字抽出も行う。もう一度押すと端末へファイルを保存して開ける。
- **画像だけのFAX PDFはOCR未対応**。添付ファイル名・メール件名・本文の検索はできる。
- 受信箱以外のフォルダは今回の対象外。最初の10通から順に取得する方式で、全件自動定期同期ではない。
- 8MBを超えるメールは取得前に除外し、画面に注意を表示。
- 取込期間はIMAPの受信日（INTERNALDATE）に基づく。画面表示は日本時間。
- 取得したメールと添付はIndexedDBに保存。Safariのプライベートブラウズ、ブラウザデータ消去、端末変更で失われる場合がある。通常のSafariで利用し、元メールは保管。
- 取込キーとサーバーURLはブラウザへ永続保存しない。ページを開き直すと再入力が必要。

## データの扱い

IMAP専用パスワードはサーバー環境変数にのみ設定。メールを接続サーバーが一時的にメモリで処理し、iPhoneに返す。サーバーはメールをファイルやDBへ保存しない。HTTPSと取込キー認証を使用。レスポンスは `no-store`。メール内容や認証情報をアプリログへ出力しない。
PDF解析ライブラリは外部CDNから取得するが、PDFの解析はブラウザ内で行う。

## 公開画面の更新

GitHub Pagesは引き続き `main` / `/(root)` を使用。ページを再読込し、上部の `v0.3` と受信箱取込欄を確認。古いキャッシュは新しいService Workerの起動時に更新。

## 開発・確認

- `server.py`: WSGI IMAP接続サーバー
- `requirements.txt`: Gunicorn実行環境
- `render.yaml`: 公開設定（認証情報は含まない）
- `test_server.py`: MIME解析、TLS接続のモック、読み取り専用、BODY.PEEK、期間・ページング、認証・CORS、添付取得、サイズ上限のテスト
- テスト: `python -m unittest -v test_server.py`
- 公開起動: `gunicorn server:app --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 180 --access-logfile /dev/null`

モックでの検証は実施。ドコモ実受信箱への接続、公開サーバー稼働、iPhone実機の確認は接続設定後に行う。

## 公式資料

- [ドコモのIMAP接続設定](https://www.docomo.ne.jp/service/docomo_mail/other/)
- [IMAP専用パスワード](https://www.docomo.ne.jp/faq/detail?categoryId=17&faqId=248285)
- [Render Blueprint](https://render.com/docs/blueprint-spec)
- [Renderの秘密情報の設定](https://render.com/docs/configure-environment-variables)
