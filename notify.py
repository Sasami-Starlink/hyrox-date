#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
差分検知＆メール通知
------------------------------------------------------------
前回 events.json と今回 events.json を比較し、
  🆕 新規開催決定
  🎫 チケット販売開始（ステータスが on_sale / coming_soon に変化）
  📅 販売日 決定/変更（sale_date が新たに判明・変化）
があればメールで知らせます。海外の販売開始は日本時間で表示。

使い方:
    python3 notify.py <prev.json> <curr.json>          # 変化があればメール送信（環境変数が揃っていれば）
    python3 notify.py <prev.json> <curr.json> --dry-run # 送信せず本文を標準出力（確認用）

送信に使う環境変数（GitHub Secrets を想定）:
    LINE_TOKEN     … LINE Messaging API チャネルアクセストークン（既定チャネル・これだけでOK）
    MAIL_USERNAME  … （任意・メールも併用する場合）SMTP ログインID（例: your@gmail.com）
    MAIL_PASSWORD  … SMTP パスワード（Gmail はアプリパスワード）
    MAIL_TO        … 宛先（カンマ区切りで複数可）
    MAIL_FROM      … 差出人（省略時 MAIL_USERNAME）
    SMTP_HOST      … 省略時 smtp.gmail.com
    SMTP_PORT      … 省略時 465（SSL）
    APP_URL        … アプリの公開URL（本文リンク用・任意）
標準ライブラリのみで動作します。
"""

import sys, os, json, smtplib, ssl
import urllib.request, urllib.error
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
WD = ['月', '火', '水', '木', '金', '土', '日']
STATUS_LABEL = {
    'on_sale': '販売中', 'coming_soon': 'まもなく販売',
    'announced': '開催決定・販売前', 'sold_out': '完売', 'closed': '受付終了', 'past': '終了',
}
# 「販売が動いた」とみなすステータス
SALE_ACTIVE = {'on_sale', 'coming_soon'}


def load(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def fmt_date(d):
    if not d:
        return '日程調整中'
    try:
        dt = datetime.strptime(d, '%Y-%m-%d')
        return f"{dt.year}年{dt.month}月{dt.day}日({WD[dt.weekday()]})"
    except Exception:
        return d


def fmt_sale(ev):
    if ev.get('sale_start_jst'):
        try:
            dt = datetime.fromisoformat(ev['sale_start_jst']).astimezone(JST)
            return f"日本時間 {dt.month}月{dt.day}日({WD[dt.weekday()]}) {dt:%H:%M} 開始"
        except Exception:
            pass
    if ev.get('sale_date'):
        return f"{fmt_date(ev['sale_date'])} 販売"
    return '販売日 未定'


def fmt_presale(ev):
    """提携ジム会員の先行目安（一般販売の約24〜48時間前）。国内/海外で注記を変える。"""
    tail = '所属の提携ジム経由でコード配布' if ev.get('country') == '日本' \
        else '開催国の提携ジム/コード次第（国をまたぐ資格は要確認）'
    if ev.get('presale_jst'):
        try:
            dt = datetime.fromisoformat(ev['presale_jst']).astimezone(JST)
            return f"日本時間 {dt.month}月{dt.day}日({WD[dt.weekday()]}) {dt:%H:%M} 開始・{tail}"
        except Exception:
            pass
    if ev.get('sale_start_jst'):
        try:
            base = datetime.fromisoformat(ev['sale_start_jst']).astimezone(JST)
            early = base - timedelta(hours=48)
            return f"一般販売の約24〜48時間前（目安 {early.month}月{early.day}日頃〜）・{tail}"
        except Exception:
            pass
    if ev.get('sale_date'):
        return f"一般販売の約24〜48時間前・{tail}"
    return None


MIX_LABEL = {'available': '在庫あり🟢', 'sold_out': '完売🔴', 'unknown': '要確認'}


def diff(prev, curr):
    pmap = {e['id']: e for e in prev.get('events', [])}
    new_events, sale_open, sale_date_set, mix_changes = [], [], [], []
    for e in curr.get('events', []):
        p = pmap.get(e['id'])
        if p is None:
            new_events.append(e)
            continue
        # ステータスが「販売系」に変化
        if e.get('ticket_status') in SALE_ACTIVE and p.get('ticket_status') not in SALE_ACTIVE:
            sale_open.append(e)
        # 販売日が新たに判明 or 変化（正確時刻含む）
        changed_date = (e.get('sale_date') and e.get('sale_date') != p.get('sale_date'))
        changed_time = (e.get('sale_start_jst') and e.get('sale_start_jst') != p.get('sale_start_jst'))
        if (changed_date or changed_time) and e not in sale_open:
            sale_date_set.append(e)
        # MIXダブルス在庫の変化（在庫復活・完売など）。available/sold_out 間の変化を通知。
        if e.get('mix_doubles') in ('available', 'sold_out') \
                and e.get('mix_doubles') != p.get('mix_doubles') and e not in sale_open:
            mix_changes.append(e)
    return new_events, sale_open, sale_date_set, mix_changes


def build_body(new_events, sale_open, sale_date_set, mix_changes, app_url):
    lines = []
    if sale_open:
        lines.append('🎫 チケット販売が動きました')
        for e in sale_open:
            lines.append(f"　・{e['city']}（{e['country']}） {fmt_date(e.get('event_start'))}")
            lines.append(f"　　→ {fmt_sale(e)}　[{STATUS_LABEL.get(e['ticket_status'], e['ticket_status'])}]")
            pre = fmt_presale(e)
            if pre:
                lines.append(f"　　🏋 先行: {pre}")
        lines.append('')
    if sale_date_set:
        lines.append('📅 販売日が決定/更新されました')
        for e in sale_date_set:
            lines.append(f"　・{e['city']}（{e['country']}） {fmt_date(e.get('event_start'))}")
            lines.append(f"　　→ {fmt_sale(e)}")
            pre = fmt_presale(e)
            if pre:
                lines.append(f"　　🏋 先行: {pre}")
        lines.append('')
    if mix_changes:
        lines.append('👫 MIXダブルス（夫婦ペア）の在庫が変化しました')
        for e in mix_changes:
            lines.append(f"　・{e['city']}（{e['country']}） {fmt_date(e.get('event_start'))}")
            lines.append(f"　　→ MIXダブルス: {MIX_LABEL.get(e.get('mix_doubles'), e.get('mix_doubles'))}")
        lines.append('')
    if new_events:
        lines.append('🆕 新しい開催が決まりました（行くか判断してね）')
        for e in new_events:
            tv = e.get('travel') or {}
            price = tv.get('flight_price', '')
            lines.append(f"　・{e['city']}（{e['country']}） {fmt_date(e.get('event_start'))}")
            extra = []
            if tv.get('total_hint'):
                extra.append(f"東京から{tv['total_hint']}")
            if price:
                extra.append(price)
            if extra:
                lines.append('　　' + ' / '.join(extra))
        lines.append('')
    if app_url:
        lines.append(f"▼ アプリで詳細を見る・判断する\n{app_url}")
    lines.append('\n― HYROX ウォッチ（毎日 昼12時・深夜0時 更新）')
    return '\n'.join(lines)


def build_subject(new_events, sale_open, sale_date_set, mix_changes):
    parts = []
    if sale_open:
        parts.append(f"🎫販売{len(sale_open)}件")
    if mix_changes:
        parts.append(f"👫MIX{len(mix_changes)}件")
    if sale_date_set:
        parts.append(f"📅販売日{len(sale_date_set)}件")
    if new_events:
        parts.append(f"🆕新規{len(new_events)}件")
    return 'HYROX更新: ' + '・'.join(parts)


def send_line(subject, body):
    """LINE Messaging API のブロードキャストで、公式アカウントの友だち全員に送信。
    環境変数 LINE_TOKEN（チャネルアクセストークン・長期）が必要。お二人=友だち2人でOK。"""
    token = os.environ.get('LINE_TOKEN')
    if not token:
        return False
    text = (subject + '\n\n' + body).strip()[:4900]  # LINEテキスト上限に配慮
    payload = json.dumps({'messages': [{'type': 'text', 'text': text}]}).encode('utf-8')
    req = urllib.request.Request(
        'https://api.line.me/v2/bot/message/broadcast',
        data=payload, method='POST',
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req) as r:
            print(f'✓ LINE配信 (HTTP {r.status})')
        return True
    except urllib.error.HTTPError as e:
        print(f'! LINE配信 失敗 HTTP {e.code}: {e.read().decode("utf-8", "replace")[:200]}', file=sys.stderr)
        return False


def send_mail(subject, body):
    user = os.environ.get('MAIL_USERNAME')
    pw = os.environ.get('MAIL_PASSWORD')
    to = os.environ.get('MAIL_TO', '')
    if not (user and pw and to):
        print('（メール環境変数が未設定のため送信スキップ。本文は上に表示）')
        return False
    sender = os.environ.get('MAIL_FROM', user)
    host = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
    port = int(os.environ.get('SMTP_PORT', '465'))
    recipients = [a.strip() for a in to.split(',') if a.strip()]

    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = formataddr((str(Header('HYROX ウォッチ', 'utf-8')), sender))
    msg['To'] = ', '.join(recipients)

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=ctx) as s:
        s.login(user, pw)
        s.sendmail(sender, recipients, msg.as_string())
    print(f'✓ メール送信: {recipients}')
    return True


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    dry = '--dry-run' in sys.argv
    if len(args) < 2:
        print('usage: notify.py <prev.json> <curr.json> [--dry-run]', file=sys.stderr)
        sys.exit(2)
    prev, curr = load(args[0]), load(args[1])

    # 初回（前回データ無し）は棚卸し扱い。大量メールを避けて送信しない。
    if not prev.get('events'):
        print('前回データ無し（初回）。通知はスキップして基準を確定します。')
        return

    new_events, sale_open, sale_date_set, mix_changes = diff(prev, curr)
    if not (new_events or sale_open or sale_date_set or mix_changes):
        print('変更なし。通知しません。')
        return

    # アプリURLは常に本文へ記載（環境変数 APP_URL があれば優先、無ければ公開URL）
    app_url = os.environ.get('APP_URL') or 'https://sasami-starlink.github.io/hyrox-date/'
    subject = build_subject(new_events, sale_open, sale_date_set, mix_changes)
    body = build_body(new_events, sale_open, sale_date_set, mix_changes, app_url)
    print('=' * 48)
    print('件名:', subject)
    print('-' * 48)
    print(body)
    print('=' * 48)
    if dry:
        return
    sent = False
    # LINE（既定チャネル）→ メール（任意）の順で、設定済みのものに送る
    if os.environ.get('LINE_TOKEN'):
        sent = send_line(subject, body) or sent
    if os.environ.get('MAIL_USERNAME'):
        try:
            sent = send_mail(subject, body) or sent
        except Exception as e:
            print(f'! メール送信失敗: {e}', file=sys.stderr)
    if not sent:
        print('（通知チャネル未設定＝LINE_TOKEN等が無いため送信スキップ。本文は上に表示）')


if __name__ == '__main__':
    main()
