'use strict';

/* ------------------------------------------------------------------
 * HYROX ウォッチ — PWA フロント
 *  ⓪ 棚卸し（初回は現行の全開催）／① 判断（行く・検討中・興味なし）
 *  ② 参加予定の追跡表示（販売開始）／③ 通知はメール側で実施
 * 判断と既読はこの端末の localStorage に保存。
 * ------------------------------------------------------------------ */

const LS_DECIDE = 'hyrox_decisions_v1';   // { [id]: 'going'|'maybe'|'skip' }
const LS_LASTSEEN = 'hyrox_lastseen_v1';  // 'YYYY-MM-DD'（この日以降の first_seen を新着扱い）

const STATUS_LABEL = {
  on_sale: '販売中',
  coming_soon: 'まもなく販売',
  announced: '開催決定・販売前',
  sold_out: '完売',
  closed: '受付終了',
  past: '終了',
};

const WEEKDAY = ['日', '月', '火', '水', '木', '金', '土'];
// 端末のタイムゾーンに関係なく、常に日本時間(Asia/Tokyo)で日付要素を取り出す。
const WD_EN2JP = { Sun: '日', Mon: '月', Tue: '火', Wed: '水', Thu: '木', Fri: '金', Sat: '土' };
function jstFields(input) {
  const d = (input instanceof Date) ? input : new Date(input);
  if (isNaN(d)) return null;
  const f = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Tokyo', year: 'numeric', month: 'numeric', day: 'numeric',
    hour: '2-digit', minute: '2-digit', hour12: false, weekday: 'short',
  });
  const p = {};
  for (const part of f.formatToParts(d)) p[part.type] = part.value;
  return { y: +p.year, mo: +p.month, d: +p.day, hh: p.hour, mm: p.minute, wd: WD_EN2JP[p.weekday] };
}

let STATE = {
  events: [],
  view: 'triage',
  decisions: loadJSON(LS_DECIDE, {}),
  lastSeen: localStorage.getItem(LS_LASTSEEN) || '',
  meta: {},
};

/* ---------- utils ---------- */
function loadJSON(k, def) {
  try { return JSON.parse(localStorage.getItem(k)) || def; } catch { return def; }
}
function saveDecisions() {
  localStorage.setItem(LS_DECIDE, JSON.stringify(STATE.decisions));
}
function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}
function fmtDateRange(start, end) {
  if (!start) return '日程調整中';
  const s = jstFields(start + 'T00:00:00+09:00');
  const label = (f) => `${f.mo}月${f.d}日(${f.wd})`;
  if (!end || end === start) return `${s.y}年 ${label(s)}`;
  const e = jstFields(end + 'T00:00:00+09:00');
  return `${s.y}年 ${label(s)}〜${label(e)}`;
}
function fmtJstDateTime(iso) {
  const f = jstFields(iso);
  if (!f) return '';
  return `日本時間 ${f.mo}月${f.d}日(${f.wd}) ${f.hh}:${f.mm}`;
}
function fmtSale(ev) {
  if (ev.ticket_status === 'sold_out') return { text: '完売', cls: 'sale-tbd' };
  // 未販売で正確な開始日時（JST）が判明 → 何日何時まで表示
  if (ev.sale_start_jst && ev.ticket_status !== 'on_sale') {
    return { text: `${fmtJstDateTime(ev.sale_start_jst)} 販売開始`, cls: 'sale-hi' };
  }
  if (ev.ticket_status === 'on_sale') {
    const when = ev.sale_start_jst ? `（${fmtJstDateTime(ev.sale_start_jst)}〜）` : '';
    return { text: `販売中${when}`, cls: 'sale-hi' };
  }
  if (ev.sale_date) {
    const f = jstFields(ev.sale_date + 'T00:00:00+09:00');
    return { text: `${f.y}年${f.mo}月${f.d}日 販売予定`, cls: 'sale-hi' };
  }
  return { text: '販売日 未定（決まり次第お知らせ）', cls: 'sale-tbd' };
}
// 👫 ミックスダブルス（夫婦ペア）の在庫
function mixLabel(ev) {
  const m = ev.mix_doubles;
  if (!m) return null;                       // 未販売＝表示しない
  if (m === 'available') return { text: '在庫あり', cls: 'mix-ok', icon: '🟢' };
  if (m === 'sold_out') return { text: '完売', cls: 'mix-no', icon: '🔴' };
  return { text: '要確認', cls: 'mix-unk', icon: '⚪️' };
}
function fmtDateShort(d) {
  const f = jstFields(d + 'T00:00:00+09:00');
  return `${f.mo}/${f.d}`;
}
// 提携ジム会員の先行（HYROX共通の特典＝一般販売の約24〜48時間前）。正確な先行日時が判明すれば最優先。
// 資格は開催国側の提携ジム/コードに紐づく地域運用のため、日本大会と海外大会で注記を変える。
function presaleTail(ev) {
  return ev.country === '日本'
    ? '所属の提携ジム経由でコード配布'
    : '開催国の提携ジム/コード次第（国をまたぐ資格は要確認）';
}
function fmtPresale(ev) {
  if (['sold_out', 'past', 'closed'].includes(ev.ticket_status)) return null;
  if (ev.presale_jst) {
    return `${fmtJstDateTime(ev.presale_jst)} 開始（提携ジム会員）・${presaleTail(ev)}`;
  }
  if (ev.sale_start_jst) {
    const early = jstFields(new Date(new Date(ev.sale_start_jst).getTime() - 48 * 3600 * 1000));
    return `一般販売の約24〜48時間前（目安：日本時間 ${early.mo}月${early.d}日頃〜）・${presaleTail(ev)}`;
  }
  if (ev.sale_date) {
    return `一般販売（${fmtDateShort(ev.sale_date)}）の約24〜48時間前・${presaleTail(ev)}`;
  }
  return `一般販売が決まると目安を表示・${presaleTail(ev)}`;
}
function isNew(ev) {
  if (!STATE.lastSeen) return false;         // 初回は棚卸し扱い（新着なし）
  return (ev.first_seen || '') > STATE.lastSeen;
}
function decisionOf(id) { return STATE.decisions[id] || null; }

/* ---------- rendering ---------- */
function cardEl(ev) {
  const dec = decisionOf(ev.id);
  const card = el('div', 'card' + (dec === 'going' ? ' going' : dec === 'skip' ? ' skip' : ''));

  // top: city + status
  const top = el('div', 'card-top');
  const left = el('div');
  const city = el('div', 'card-city', escapeHTML(ev.city || ev.name) + (isNew(ev) ? '<span class="tag-new">NEW</span>' : ''));
  const country = el('div', 'card-country', `${escapeHTML(ev.country || '')}${ev.venue ? ' ・ ' + escapeHTML(ev.venue) : ''}`);
  left.appendChild(city); left.appendChild(country);
  const st = el('div', 'status ' + ev.ticket_status, STATUS_LABEL[ev.ticket_status] || ev.ticket_status);
  top.appendChild(left); top.appendChild(st);
  card.appendChild(top);

  // rows: date + sale
  const rows = el('div', 'rows');
  rows.appendChild(row('📅', fmtDateRange(ev.event_start, ev.event_end)));
  const sale = fmtSale(ev);
  rows.appendChild(row('🎫', `<span class="${sale.cls}">${sale.text}</span>`));
  const mix = mixLabel(ev);
  if (mix) {
    const link = ev.detail_url ? ` <a class="mix-check" href="${escapeAttr(ev.detail_url)}" target="_blank" rel="noopener">確認›</a>` : '';
    rows.appendChild(row('👫', `<span class="mix ${mix.cls}">MIXダブルス: ${mix.icon} ${mix.text}</span>${link}`));
  }
  const pre = fmtPresale(ev);
  if (pre) rows.appendChild(row('🏋', `<span class="presale">先行: ${escapeHTML(pre)}</span>`));
  card.appendChild(rows);

  // travel
  if (ev.travel) {
    const t = ev.travel;
    const box = el('div', 'travel-box');
    box.appendChild(el('div', 'tt', '東京から'));
    const g = el('div', 'travel-grid');
    if (t.flight) g.appendChild(row('✈️', escapeHTML(t.flight)));
    if (t.access) g.appendChild(row('🚃', escapeHTML(t.access)));
    if (t.total_hint) g.appendChild(row('⏱', escapeHTML(t.total_hint)));
    if (t.flight_price) g.appendChild(row('💴', `<span class="price">${escapeHTML(t.flight_price)}</span>`));
    box.appendChild(g);
    card.appendChild(box);
  }

  // actions
  const acts = el('div', 'actions');
  acts.appendChild(actBtn(ev, 'going', '行く'));
  acts.appendChild(actBtn(ev, 'maybe', '検討中'));
  acts.appendChild(actBtn(ev, 'skip', '興味なし'));
  card.appendChild(acts);

  // detail link
  if (ev.detail_url) {
    const p = el('div', null, `<a class="detail-link" href="${escapeAttr(ev.detail_url)}" target="_blank" rel="noopener">詳細・会場情報 ↗</a>`);
    p.style.marginTop = '10px';
    card.appendChild(p);
  }
  return card;
}
function row(k, vHTML) {
  const r = el('div', 'row');
  r.appendChild(el('span', 'k', k));
  r.appendChild(el('span', 'v', vHTML));
  return r;
}
function actBtn(ev, act, label) {
  const on = decisionOf(ev.id) === act;
  const b = el('button', 'act' + (on ? ' on' : '') , label);
  b.dataset.act = act;
  b.addEventListener('click', () => {
    STATE.decisions[ev.id] = (decisionOf(ev.id) === act) ? undefined : act;
    if (!STATE.decisions[ev.id]) delete STATE.decisions[ev.id];
    saveDecisions();
    render();
  });
  return b;
}
function escapeHTML(s) { return String(s == null ? '' : s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }
function escapeAttr(s) { return escapeHTML(s).replace(/"/g, '&quot;'); }

function filteredEvents() {
  const evs = STATE.events;
  if (STATE.view === 'going') return evs.filter(e => decisionOf(e.id) === 'going');
  if (STATE.view === 'all') return evs;
  // triage: 未判断 or 検討中（興味なしと参加予定は除外）。新着を上に。
  return evs
    .filter(e => { const d = decisionOf(e.id); return d !== 'skip' && d !== 'going'; })
    .sort((a, b) => (isNew(b) - isNew(a)) || (a.travel_rank - b.travel_rank) || ((a.event_start || '') > (b.event_start || '') ? 1 : -1));
}

function render() {
  // counts
  const evs = STATE.events;
  const triageN = evs.filter(e => { const d = decisionOf(e.id); return d !== 'skip' && d !== 'going'; }).length;
  const goingN = evs.filter(e => decisionOf(e.id) === 'going').length;
  setText('cnt-triage', triageN);
  setText('cnt-going', goingN);
  setText('cnt-all', evs.length);

  // tabs active
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.view === STATE.view));

  const list = document.getElementById('list');
  list.innerHTML = '';

  if (STATE.view === 'going') { renderGoing(list); return; }

  const items = filteredEvents();
  if (!items.length) {
    const msg = STATE.view === 'going'
      ? '「行く」に印を付けた大会がここに集まります。<br>販売開始が近づくとメールでもお知らせします。'
      : STATE.view === 'triage'
        ? 'すべて判断済みです 🎉<br>新しい開催が決まると、ここに新着で表示されます。'
        : '対象の大会がまだありません。';
    list.appendChild(el('div', 'empty', `<span class="big">🏁</span>${msg}`));
    return;
  }
  items.forEach(ev => list.appendChild(cardEl(ev)));
}
function setText(id, v) { const e = document.getElementById(id); if (e) e.textContent = v; }

function todayStr() {
  const f = jstFields(new Date());
  return `${f.y}-${String(f.mo).padStart(2, '0')}-${String(f.d).padStart(2, '0')}`;
}
function sectionHeader(title, n) {
  return el('div', 'section-h', `${escapeHTML(title)}<span>${n}</span>`);
}
// ⓪ 「行く」ページ：未開催（これから）／開催済み に分けて表示
function renderGoing(list) {
  const going = STATE.events.filter(e => decisionOf(e.id) === 'going');
  if (!going.length) {
    list.appendChild(el('div', 'empty', '<span class="big">🏁</span>「行く」に印を付けた大会がここに集まります。<br>「未開催（これから）」と「開催済み」に分かれて表示されます。'));
    return;
  }
  const today = todayStr();
  const endOf = e => (e.event_end || e.event_start || '');
  const upcoming = going.filter(e => endOf(e) >= today)
    .sort((a, b) => ((a.event_start || '') > (b.event_start || '') ? 1 : -1));
  const past = going.filter(e => endOf(e) < today)
    .sort((a, b) => ((a.event_start || '') < (b.event_start || '') ? 1 : -1));
  if (upcoming.length) {
    list.appendChild(sectionHeader('未開催（これから）', upcoming.length));
    upcoming.forEach(e => list.appendChild(cardEl(e)));
  }
  if (past.length) {
    list.appendChild(sectionHeader('開催済み', past.length));
    past.forEach(e => list.appendChild(cardEl(e)));
  }
}

/* ---------- boot ---------- */
async function boot() {
  try {
    const res = await fetch('./events.json?_=' + Date.now());
    const data = await res.json();
    STATE.events = data.events || [];
    STATE.meta = data;
    const uf = data.updated_at ? jstFields(data.updated_at) : null;
    const updTxt = uf ? `更新 ${uf.mo}/${uf.d} ${uf.hh}:${uf.mm}` : '';
    setText('meta', `${data.scope || 'アジア'}・${STATE.events.length}大会　${updTxt}`);
  } catch (e) {
    setText('meta', 'データ取得に失敗しました');
    document.getElementById('list').appendChild(el('div', 'empty', '<span class="big">⚠️</span>events.json を読み込めませんでした。'));
    return;
  }

  setupLineBanner();
  render();

  // 既読カーソル更新（新着の判定は今回の描画で確定。次回以降のために今日へ進める）
  const maxSeen = STATE.events.reduce((m, e) => (e.first_seen && e.first_seen > m ? e.first_seen : m), STATE.lastSeen || '');
  const today = todayStr();
  localStorage.setItem(LS_LASTSEEN, maxSeen > today ? maxSeen : today);
}

// LINE友だち追加バナー（config.js に lineAddUrl を設定すると表示）
function setupLineBanner() {
  const url = (window.HYROX_CONFIG && window.HYROX_CONFIG.lineAddUrl || '').trim();
  const b = document.getElementById('lineBanner');
  if (!b || !url) return;
  b.href = url;
  b.hidden = false;
  // PWA(ホーム画面追加)でも確実にLINEを開くため、明示的に遷移させる
  b.addEventListener('click', (e) => {
    e.preventDefault();
    window.location.href = url;
  });
}

document.getElementById('tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('.tab');
  if (!btn) return;
  STATE.view = btn.dataset.view;
  render();
});
document.getElementById('refreshBtn').addEventListener('click', () => location.reload());

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('./sw.js').catch(() => {}));
}

boot();
