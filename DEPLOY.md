# セットアップ & 公開手順（HYROX ウォッチ）

スマホ用WEBアプリ（PWA）。GitHub Pages で公開し、GitHub Actions が
**毎日 昼12時・深夜0時（JST）** に情報収集 → 変化があれば **LINE通知** → 自動再公開します。

---

## 0. ローカルで動かす（確認用）
```bash
cd hyrox-app
python3 crawl.py            # events.json を最新化（アジアの開催・チケット状況を収集）
python3 -m http.server 8791 # http://localhost:8791/ をスマホ/PCで開く
```

---

## 1. LINE通知の準備（初回だけ・10分）

「友だち追加するだけ」で二人とも通知を受け取れます。

1. **LINE Developers** (https://developers.line.biz/) にLINEアカウントでログイン。
2. **プロバイダー**を新規作成（名前は何でも可：例「hyrox」）。
3. その中に **Messaging API チャネル**を新規作成（＝LINE公式アカウントができる）。
   - チャネル名：例「HYROXウォッチ」
4. 作成後、**「Messaging API」タブ**で:
   - **チャネルアクセストークン（長期）** を発行 → コピー（後で GitHub Secret `LINE_TOKEN` に貼る）。
   - **友だち追加URL / QRコード** を控える（後で `config.js` に貼る）。
5. **応答設定**で「あいさつメッセージ」「応答メッセージ」はお好みでOFFに（Bot送信のみ使うため）。
6. 二人のスマホで、その公式アカウントを **友だち追加**（これが「登録」です）。

> 送信は「ブロードキャスト（友だち全員へ）」。二人なら無料枠で十分です。

---

## 2. GitHub リポジトリを作る & 公開

1. GitHub で **新規リポジトリ**を作成（例 `hyrox-tracker-xxxx`。public/private どちらでも可）。
2. このフォルダを push（SSHデプロイ鍵方式でもHTTPSでもOK）:
   ```bash
   git init && git add -A && git commit -m "init: HYROX ウォッチ"
   git branch -M main
   git remote add origin <あなたのリポジトリURL>
   git push -u origin main
   ```
3. GitHub の **Settings → Pages** で **Source = GitHub Actions** に設定。
4. GitHub の **Settings → Secrets and variables → Actions**:
   - **Secrets** に `LINE_TOKEN` = さっきのチャネルアクセストークンを追加。
   - （任意）`APP_URL` を **Variables** に公開URL（下で判明）を入れると、通知本文にアプリのリンクが付く。
5. **`config.js`** を開き、`lineAddUrl` に **1で控えた友だち追加URL**を貼って push:
   ```js
   window.HYROX_CONFIG = { lineAddUrl: "https://lin.ee/xxxxxxx" };
   ```
   → アプリ上部に「LINEで通知を受け取る」ボタンが出ます。
6. **Actions → 「HYROX 収集＆通知＆公開」→ Run workflow** で手動実行 → 公開URLが発行されます
   （`https://<ユーザー名>.github.io/<リポジトリ名>/`）。スマホで開き「ホーム画面に追加」でアプリ化。

以降は **毎日自動**で更新・通知されます（手動更新は Run workflow）。

---

## 通知の仕組み（③）
- Actions が収集 → **前回の events.json と差分**を比較（`notify.py`）。
- 変化（🆕新規開催 / 🎫販売開始 / 📅販売日決定）があるときだけ **LINEにプッシュ**。
- 海外の販売開始は **日本時間に換算**。提携ジムの **先行目安（一般販売の約24〜48時間前）** も本文に含む。
- メールも併用したい場合は Secrets に `MAIL_USERNAME` / `MAIL_PASSWORD` / `MAIL_TO` を追加すればLINEと両方に送信。

## カスタマイズ
- **対象国**：`crawl.py` の `ALLOW_COUNTRIES` を編集（既定＝日本＋東・東南アジア）。
- **所要時間・費用**：`travel.json` を手で編集（東京起点の概算目安。都市コードでマッチ）。
- **通知時刻**：`.github/workflows/update.yml` の cron（UTC）を編集（既定 03:00/15:00 UTC = 12:00/0:00 JST）。
- **先行チケットの正確な日時が判明**したら、`events.json` 該当イベントに `presale_jst`（ISO8601）を入れると、目安ではなく確定表示になります（次回収集で保持）。

## データ源
- 開催・日程・会場・ステータス・販売日：**RoxRadar**（全世界の HYROX を集約、サーバーレンダリングで機械可読）。
- 提携ジム先行：HYROX共通仕様（一般販売の約24〜48時間前）を自動算出。正確な日時・コードは各ジム配布のため要確認。
