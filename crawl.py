#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HYROX 開催・チケット追跡アプリ用クローラー（堅牢版）
------------------------------------------------------------
アジア（日本＋東・東南アジア）の HYROX 開催情報とチケット状況を収集して
events.json を生成/更新します。

収集元:
- RoxRadar チケットAPI（公開・認証不要）:
    https://tickets-api.roxradar.com/api/races/ticket-status?page=N
  各レースの city / country / 日程 / 正確な販売日時(tickets_date, UTC) /
  is_sold_out / カテゴリ別在庫(divisions … Doubles Mixed 含む) を返す。
  ※このAPIは「販売中・完売の（＝チケット化された）レース」中心。
   まだ販売前の“開催決定のみ”のレースは載らないことがある。

方針・堅牢性:
- 上記APIを主軸に構築（サイトHTML構造の変更に強い）。
- APIに載らない “開催決定のみ / 直近の過去” レースは、前回 events.json から持ち越し
  （＝大阪2027・名古屋2027 のような未発表の日本大会や、ユーザーの判断メモを守る）。
- **安全ガード**: 何らかの理由で最終的に 0 件になった場合は events.json を上書きせず、
  既存データを保持して終了（サイトが空になる事故の再発防止）。
- first_seen は前回から引き継ぎ（新着判定が安定）。id は API の slug（＝従来idと一致）。
- 表示は日本語。所要時間・費用は travel.json（手動）をマージ。標準ライブラリのみ。

使い方:
    python3 crawl.py            # 収集して events.json を更新
    python3 crawl.py --fresh    # 持ち越し無しで作り直し
"""

import urllib.request, urllib.error
import socket, re, json, time, gzip, io, sys, os
from datetime import datetime, date, timezone, timedelta

socket.setdefaulttimeout(20)
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "events.json")
TRAVEL = os.path.join(HERE, "travel.json")
JST = timezone(timedelta(hours=9))

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 "
                    "hyrox-tracker-personal (non-commercial)"}

TICKETS_API = "https://tickets-api.roxradar.com/api/races/ticket-status"
STATUS_PAGE = "https://alerts.roxradar.com/hyrox-ticket-status"  # 「確認」リンク先（カテゴリ別在庫の一覧）

# 対象国（ISOコード）。日本＋東・東南アジア。
COUNTRY_JA = {
    "JP": "日本", "KR": "韓国", "CN": "中国", "HK": "香港", "TW": "台湾",
    "SG": "シンガポール", "TH": "タイ", "MY": "マレーシア", "VN": "ベトナム",
    "PH": "フィリピン", "ID": "インドネシア", "MO": "マカオ",
}
ASIA_CODES = set(COUNTRY_JA.keys())

# 都市名(英, 小文字) → travel.json の都市コード
CITY_CODE = {
    "osaka": "OSA", "nagoya": "NGO", "tokyo": "TYO", "chiba": "CHB", "yokohama": "YOK",
    "seoul": "SEL", "bangkok": "BKK", "singapore": "SGP", "shenzhen": "SZX",
    "beijing": "PEK", "guangzhou": "CAN", "sanya": "SYX", "kuala lumpur": "KUL",
    "taipei": "TPE", "hong kong": "HKG", "shanghai": "SHA", "hangzhou": "HGH",
    "macau": "MAC", "macao": "MAC",
}

# 古すぎる過去大会は持ち越さない（肥大化防止）
KEEP_PAST_DAYS = 400


def fetch(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.GzipFile(fileobj=io.BytesIO(data)).read()
                return data.decode("utf-8", "replace")
        except Exception as e:
            if i == tries - 1:
                print(f"  ! fetch失敗 {url}: {e}", file=sys.stderr)
                return ""
            time.sleep(1.2 * (i + 1))
    return ""


def norm_name(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def to_jst_iso(s):
    """ISO(UTC) 文字列 → JST の ISO8601（分まで）。不可なら None"""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(JST)
        return dt.isoformat(timespec="minutes")
    except Exception:
        return None


def mix_status(row):
    """divisions から Doubles Mixed（ミックスダブルス）の在庫を判定"""
    if row.get("is_sold_out"):
        return "sold_out"
    for d in row.get("divisions", []):
        if norm_name(d.get("name")) in ("doubles mixed", "mixed doubles"):
            return "available" if d.get("is_available") else "sold_out"
    return "unknown"


def load_travel():
    try:
        with open(TRAVEL, encoding="utf-8") as f:
            data = json.load(f)
        by_code = {}
        for row in data.get("cities", []):
            for key in row.get("codes", []):
                by_code[key.upper()] = row
        return by_code
    except Exception as e:
        print(f"  ! travel.json 読み込み失敗: {e}", file=sys.stderr)
        return {}


def load_previous():
    try:
        with open(OUT, encoding="utf-8") as f:
            prev = json.load(f)
        return {e["id"]: e for e in prev.get("events", [])}
    except Exception:
        return {}


def fetch_ticket_races():
    """チケットAPIを全ページ取得して行リストで返す。失敗時は None（＝上書きしない合図）。"""
    rows = []
    page = 1
    while True:
        raw = fetch(f"{TICKETS_API}?page={page}")
        if not raw:
            return None if not rows else rows
        try:
            d = json.loads(raw)
        except Exception:
            return None if not rows else rows
        rows.extend(d.get("data", []))
        meta = d.get("meta", {})
        if page >= (meta.get("last_page") or 1):
            break
        page += 1
    return rows


def build_event(row, prev, travel_by_code, today):
    eid = row.get("slug")
    city_en = (row.get("city") or "").strip()
    code = CITY_CODE.get(city_en.lower())
    tv = travel_by_code.get(code, {}) if code else {}
    p = prev.get(eid, {})

    event_start = (row.get("start_date") or "")[:10] or None
    event_end = (row.get("end_date") or "")[:10] or None
    sale_start_jst = to_jst_iso(row.get("tickets_date"))
    second_sale_jst = to_jst_iso(row.get("second_tickets_date"))

    if row.get("is_sold_out"):
        status = "sold_out"
    else:
        status = "on_sale"
    if event_end and event_end < today:
        status = "past"

    return {
        "id": eid,
        "code": code or "",
        "name": row.get("name"),
        "city": tv.get("city_ja") or city_en,
        "country": COUNTRY_JA.get(row.get("country")) or row.get("country_full") or "アジア",
        "region": "asia",
        "event_start": event_start,
        "event_end": event_end,
        "venue": p.get("venue"),  # APIに会場は無いので、以前取得済みなら維持
        "ticket_status": status,
        "sale_date": (sale_start_jst[:10] if sale_start_jst else p.get("sale_date")),
        "sale_start_jst": sale_start_jst or p.get("sale_start_jst"),
        "second_sale_jst": second_sale_jst,
        "mix_doubles": mix_status(row),
        "divisions_available": [x["name"] for x in row.get("divisions", []) if x.get("is_available")],
        "detail_url": STATUS_PAGE,
        "portal_url": tv.get("portal_url"),
        "first_seen": p.get("first_seen") or today,
        "travel_rank": tv.get("travel_rank", 900),
        "travel": tv.get("travel"),
        "presale_jst": p.get("presale_jst"),
    }


def main():
    fresh = "--fresh" in sys.argv
    today = date.today().isoformat()
    cutoff = (date.today() - timedelta(days=KEEP_PAST_DAYS)).isoformat()
    prev = {} if fresh else load_previous()
    travel_by_code = load_travel()

    print("RoxRadar チケットAPI を取得中 …（カテゴリ別在庫・正確な販売日時）")
    rows = fetch_ticket_races()
    if rows is None:
        print("APIの取得に失敗しました。既存 events.json を保持して終了します。", file=sys.stderr)
        return  # 上書きしない（サイトは既存データのまま）
    print(f"  取得レース総数: {len(rows)}")

    built = {}
    for row in rows:
        if row.get("country") not in ASIA_CODES:
            continue
        if not row.get("slug"):
            continue
        ev = build_event(row, prev, travel_by_code, today)
        built[ev["id"]] = ev
    print(f"  アジア対象（API由来）: {len(built)} 件")

    # APIに載らない前回イベントを持ち越し（未発表の開催決定・直近の過去。ユーザーの判断メモ保護）
    carried = 0
    if not fresh:
        for eid, pe in prev.items():
            if eid in built:
                continue
            ee = pe.get("event_end") or pe.get("event_start") or ""
            if ee and ee < cutoff:
                continue  # 古すぎる過去は捨てる
            pe = dict(pe)
            if ee and ee < today and pe.get("ticket_status") not in ("past",):
                pe["ticket_status"] = "past"
            built[eid] = pe
            carried += 1
    print(f"  前回からの持ち越し: {carried} 件")

    events = list(built.values())

    # 安全ガード: 0件なら絶対に上書きしない（空サイト事故の再発防止）
    if not events:
        print("最終的に 0 件のため、events.json は更新しません（既存データを保持）。", file=sys.stderr)
        return

    events.sort(key=lambda x: (x.get("travel_rank", 900), x.get("event_start") or "9999"))

    payload = {
        "updated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "source": "roxradar tickets-api",
        "scope": "日本＋東・東南アジア",
        "count": len(events),
        "events": events,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"✓ events.json を書き出しました（{len(events)} 件）")
    from collections import Counter
    print("  ステータス内訳:", dict(Counter(e["ticket_status"] for e in events)))


if __name__ == "__main__":
    main()
