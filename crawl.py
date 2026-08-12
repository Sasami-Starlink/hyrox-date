#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HYROX 開催・チケット追跡アプリ用クローラー
------------------------------------------------------------
アジア（日本＋東・東南アジア）の HYROX 開催情報とチケット状況を収集して
events.json を生成/更新します。

収集元:
- RoxRadar (https://www.roxradar.com/) … 全世界の HYROX 開催を集約したサイト。
  メイン一覧が完全サーバーレンダリングで、各カードに
    data-start / data-end / data-region / data-status / data-hyrox-code / 位置
  を持ち機械可読。詳細ページには JSON-LD(SportsEvent) と
  「チケット販売日(ticket-details)」がある。

方針:
- Asia-Pacific のうち、対象国（日本・韓国・中国・香港・台湾・シンガポール・タイ 等）に絞る。
- 表示は日本語。所要時間・フライト概算費用は travel.json（手動キュレーション）をマージ。
- first_seen は既存 events.json から引き継ぎ（新規のみ本日日付）。これで「新着」判定が安定。
- サイトに負荷をかけないよう 1件ごとに待機。個人利用を表明した User-Agent。
- 標準ライブラリのみで動作。

使い方:
    python3 crawl.py            # 収集して events.json を更新
    python3 crawl.py --fresh    # 既存 first_seen を無視して作り直し
"""

import urllib.request, urllib.error
import socket, re, json, html, time, gzip, io, sys, os
from datetime import datetime, date, timezone, timedelta

socket.setdefaulttimeout(20)
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "events.json")
TRAVEL = os.path.join(HERE, "travel.json")
JST = timezone(timedelta(hours=9))

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 "
                    "hyrox-tracker-personal (non-commercial)"}
SLEEP = 0.6

BASE = "https://www.roxradar.com"
LIST_URL = BASE + "/"

# 対象国（英語表記／RoxRadar の住所末尾でマッチ）。日本＋東・東南アジア。
ALLOW_COUNTRIES = {
    "Japan": "日本",
    "South Korea": "韓国", "Korea": "韓国",
    "China": "中国",
    "Hong Kong": "香港", "Hong Kong SAR": "香港",
    "Taiwan": "台湾",
    "Singapore": "シンガポール",
    "Thailand": "タイ",
    "Malaysia": "マレーシア",
    "Vietnam": "ベトナム",
    "Philippines": "フィリピン",
    "Indonesia": "インドネシア",
    "Macau": "マカオ", "Macao": "マカオ",
}

STATUS_MAP = {
    "on sale": "on_sale",
    "coming soon": "coming_soon",
    "not yet announced": "announced",
    "sold out": "sold_out",
    "registration closed": "closed",
    "past event": "past",
}


# ----------------------------- HTTP -----------------------------
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


# ----------------------------- パース補助 -----------------------------
def attr(block, name):
    m = re.search(r'%s="([^"]*)"' % re.escape(name), block)
    return html.unescape(m.group(1)).strip() if m else ""


def clean(s):
    return html.unescape(re.sub(r"\s+", " ", s or "")).strip()


def parse_date(s):
    """'January 21, 2027' / 'Jan 21, 2027' / '21 Jan 2027' → 'YYYY-MM-DD'（不明はNone）"""
    s = clean(s)
    if not s:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %b %Y", "%d %B %Y", "%B %d %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def country_from_address(street):
    """住所末尾から国名を推定 → (country_en, country_ja) or (None, None)"""
    if not street:
        return None, None
    tail = clean(street).split(",")[-1].strip()
    for en, ja in ALLOW_COUNTRIES.items():
        if en.lower() == tail.lower() or en.lower() in tail.lower():
            return en, ja
    return tail or None, None


# ----------------------------- 一覧の収集 -----------------------------
def parse_list(html_text):
    """RoxRadar トップの map-item ブロックを解析して基本情報リストを返す"""
    events = []
    # 各イベントは <div ... class="map-item"> で始まる。次の map-item まで（末尾は</body>）。
    blocks = re.split(r'(?=<div[^>]*class="map-item")', html_text)
    for blk in blocks:
        if 'class="map-item"' not in blk:
            continue
        region = attr(blk, "data-region")
        if "Asia" not in region:
            continue
        code = attr(blk, "data-hyrox-code")
        start = parse_date(attr(blk, "data-start"))
        end = parse_date(attr(blk, "data-end"))
        lat = attr(blk, "data-lat")
        lng = attr(blk, "data-lng")
        status_raw = attr(blk, "data-status")
        # スラッグ（詳細ページ）
        mslug = re.search(r'href="(/events/[^"#?]+)"', blk)
        slug = mslug.group(1).rstrip("/") if mslug else ""
        # フルネーム（例: HYROX Osaka 2027）
        mname = re.search(r'class="event_name">([^<]+)</div>', blk)
        if mname:
            name = clean(mname.group(1))
        else:
            m2 = re.search(r'class="event-name-wrap"><div>HYROX</div><div>([^<]+)</div>', blk)
            name = "HYROX " + clean(m2.group(1)) if m2 else (code or slug)
        if not slug:
            continue
        events.append({
            "slug": slug, "code": code, "name": name,
            "event_start": start, "event_end": end,
            "lat": lat, "lng": lng,
            "status_raw": status_raw,
            "detail_url": BASE + slug,
        })
    # スラッグ重複除去（同一イベントが地図と一覧で二重に出る場合）
    uniq = {}
    for e in events:
        uniq[e["slug"]] = e
    return list(uniq.values())


# ----------------------------- 詳細ページの収集 -----------------------------
def parse_detail(html_text):
    """JSON-LD と ticket-details から会場・国・販売日・ステータスを抽出"""
    out = {"venue": None, "country_en": None, "country_ja": None,
           "sale_date": None, "status_raw": None, "desc": None,
           "lat": None, "lng": None}
    # JSON-LD
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', html_text, re.S):
        try:
            d = json.loads(m.group(1))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if d.get("@type") in ("SportsEvent", "Event"):
            loc = d.get("location") or {}
            out["venue"] = clean(loc.get("name")) or out["venue"]
            addr = (loc.get("address") or {})
            en, ja = country_from_address(addr.get("streetAddress") or addr.get("addressRegion") or "")
            out["country_en"], out["country_ja"] = en, ja
            geo = loc.get("geo") or {}
            out["lat"] = str(geo.get("latitude") or "") or out["lat"]
            out["lng"] = str(geo.get("longitude") or "") or out["lng"]
            out["desc"] = clean(d.get("description"))
            break
    # ステータス（詳細ページ）
    ms = re.search(r'class="event-status"[^>]*>([^<]+)</div>', html_text)
    if ms:
        out["status_raw"] = clean(ms.group(1))
    # チケット販売日
    mt = re.search(r'class="ticket-details">([^<]+)</div>', html_text)
    if mt:
        out["sale_date"] = parse_date(mt.group(1))
    return out


# ----------------------------- travel.json -----------------------------
def load_travel():
    try:
        with open(TRAVEL, encoding="utf-8") as f:
            data = json.load(f)
        # code をキーにした辞書へ正規化
        by_code = {}
        for row in data.get("cities", []):
            for key in row.get("codes", []):
                by_code[key.upper()] = row
        return by_code
    except Exception as e:
        print(f"  ! travel.json 読み込み失敗: {e}", file=sys.stderr)
        return {}


# ----------------------------- メイン -----------------------------
def load_previous():
    try:
        with open(OUT, encoding="utf-8") as f:
            prev = json.load(f)
        return {e["id"]: e for e in prev.get("events", [])}
    except Exception:
        return {}


def main():
    fresh = "--fresh" in sys.argv
    today = date.today().isoformat()
    prev = {} if fresh else load_previous()
    travel_by_code = load_travel()

    print("RoxRadar 一覧を取得中 …")
    list_html = fetch(LIST_URL)
    if not list_html:
        print("一覧取得に失敗しました。既存 events.json を保持します。", file=sys.stderr)
        sys.exit(1)
    listed = parse_list(list_html)
    print(f"  Asia-Pacific イベント候補: {len(listed)} 件")

    events = []
    for e in listed:
        time.sleep(SLEEP)
        detail = parse_detail(fetch(e["detail_url"]))

        country_en = detail["country_en"]
        country_ja = detail["country_ja"]
        # 対象国フィルタ（住所から国が取れない場合は travel.json のコード有無で救済）
        code = (e["code"] or "").upper()
        tv = travel_by_code.get(code, {})
        if not country_ja:
            country_ja = tv.get("country_ja")
            country_en = country_en or tv.get("country_en")
        if country_en not in ALLOW_COUNTRIES and code not in travel_by_code:
            continue  # 対象国外（豪州・インド 等）

        status_raw = detail["status_raw"] or e["status_raw"] or ""
        status = STATUS_MAP.get(status_raw.lower(), "announced")

        eid = e["slug"].split("/")[-1]
        first_seen = prev.get(eid, {}).get("first_seen") or today

        city_ja = tv.get("city_ja")
        travel = tv.get("travel")  # dict or None
        travel_rank = tv.get("travel_rank", 900)

        events.append({
            "id": eid,
            "code": code,
            "name": e["name"],
            "city": city_ja or clean(e["name"].replace("HYROX", "")).rsplit(" ", 1)[0].strip(),
            "country": country_ja or country_en or "アジア",
            "region": "asia",
            "event_start": e["event_start"],
            "event_end": e["event_end"],
            "venue": detail["venue"],
            "lat": e["lat"] or detail["lat"],
            "lng": e["lng"] or detail["lng"],
            "ticket_status": status,
            "sale_date": detail["sale_date"],          # YYYY-MM-DD（判明分）
            "sale_start_jst": prev.get(eid, {}).get("sale_start_jst"),  # 正確な時刻が判明した場合に手当て
            "detail_url": e["detail_url"],
            "portal_url": tv.get("portal_url"),
            "first_seen": first_seen,
            "travel_rank": travel_rank,
            "travel": travel,
            "desc": detail["desc"],
        })

    # 並び順: 行きやすさ(travel_rank) → 開催日
    events.sort(key=lambda x: (x["travel_rank"], x["event_start"] or "9999"))

    payload = {
        "updated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "source": "roxradar.com",
        "scope": "日本＋東・東南アジア",
        "count": len(events),
        "events": events,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"✓ events.json を書き出しました（{len(events)} 件）")
    # サマリ
    from collections import Counter
    c = Counter(e["ticket_status"] for e in events)
    print("  ステータス内訳:", dict(c))


if __name__ == "__main__":
    main()
