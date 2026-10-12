#!/usr/bin/env python3
"""
Собирает addons_table.html: каталог аддонов MajorDoMo Connect с иконками,
системными именами, авторами, описаниями и статистикой установок.

Использование:
    python3 build_addons_table.py                # аддоны 1..350
    python3 build_addons_table.py --count 400
    python3 build_addons_table.py --refresh      # скачать всё заново

Нужен только стандартный Python 3. Данные кэшируются в addons_cache.json,
повторный запуск докачивает только недостающие страницы.
"""
import argparse
import concurrent.futures as cf
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

PAGE_URL = 'https://connect.smartliving.ru/addons/category0/{n}.html'
ICON_FALLBACK = 'https://connect.majordomohome.com/cms/addons/{n}_icon.png'
SITE = 'https://connect.smartliving.ru'
CACHE = 'addons_cache.json'
CACHE_VER = 2
UA = 'Mozilla/5.0 (addons-table builder)'

CATEGORIES = {1: 'Оборудование', 2: 'Мультимедиа', 3: 'Информация',
              4: 'Взаимодействие', 5: 'Система', 6: 'Модификации'}

RE_TITLE = re.compile(r'<title>\s*(.*?)\s*</title>', re.I | re.S)
RE_H3 = re.compile(r'<h3[^>]*>(.*?)</h3>', re.I | re.S)
RE_ICON = re.compile(r'''(?:src|href)=["']([^"']*/cms/addons/\d+_icon\.(?:png|jpe?g|gif|webp|svg))["']''', re.I)
RE_BACK = re.compile(r'''href=["'][^"']*category(\d+)\.html["'][^>]*>\s*(?:&lt;|<){3}''', re.I)
RE_A = re.compile(r'''<a\b[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>''', re.I | re.S)
RE_TAG = re.compile(r'<[^>]+>')

LINK_LABELS = {'Скачать': 'download', 'Обсуждение': 'forum', 'Страница проекта': 'project'}


def to_text(fragment):
    """HTML -> текст с переводами строк на месте блочных тегов."""
    s = re.sub(r'(?is)<(script|style)\b.*?</\1>', ' ', fragment)
    s = re.sub(r'(?i)<br\s*/?>|</(p|div|li|tr|h\d|table)>', '\n', s)
    s = html.unescape(RE_TAG.sub(' ', s))
    s = re.sub(r'[ \t\xa0]+', ' ', s)
    return re.sub(r'\n\s*\n+', '\n', s).strip()


def absolute(url):
    if url.startswith('http'):
        return url
    return SITE + '/' + url.lstrip('/')


def parse(n, page):
    rec = {'n': n, 'v': CACHE_VER, 'ok': False, 'sysname': '', 'title': '', 'author': '',
           'category': '', 'desc': '', 'installs': None, 'month': None, 'icon': '', 'links': {}}
    text = to_text(page)

    m = re.search(r'Системное\s+имя\s+модуля\s*:\s*(\S+)', text)
    if not m:
        return rec          # пустая или списочная страница
    rec['ok'] = True
    rec['sysname'] = m.group(1)

    m = RE_H3.search(page) or RE_TITLE.search(page)
    if m:
        rec['title'] = to_text(m.group(1))
    m = re.search(r'Автор\s*:\s*([^\n]+)', text)
    if m:
        rec['author'] = m.group(1).strip()
    m = re.search(r'Всего установок\s*:\s*(\d+)', text)
    if m:
        rec['installs'] = int(m.group(1))
    m = re.search(r'За прошедший месяц\s*:\s*(\d+)', text)
    if m:
        rec['month'] = int(m.group(1))

    # описание: всё между строкой «Автор» и «Всего установок»
    m = re.search(r'Автор\s*:[^\n]*\n(.*?)(?:Всего установок|$)', text, re.S)
    if m:
        rec['desc'] = re.sub(r'\s*\n\s*', '\n', m.group(1)).strip()

    m = RE_BACK.search(page)
    if m:
        rec['category'] = CATEGORIES.get(int(m.group(1)), '')
    m = RE_ICON.search(page)
    if m:
        rec['icon'] = absolute(m.group(1))
    for href, inner in RE_A.findall(page):
        label = to_text(inner)
        if label in LINK_LABELS:
            rec['links'][LINK_LABELS[label]] = absolute(html.unescape(href))
    return rec


def fetch(n):
    try:
        req = urllib.request.Request(PAGE_URL.format(n=n), headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            page = r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return {'n': n, 'v': CACHE_VER, 'ok': False, 'error': f'HTTP {e.code}'}
    except Exception as e:
        return {'n': n, 'v': CACHE_VER, 'ok': False, 'error': str(e)}
    return parse(n, page)


def load_cache(path=CACHE):
    try:
        with open(path, encoding='utf-8') as f:
            return {int(k): v for k, v in json.load(f).items() if v.get('v') == CACHE_VER}
    except (OSError, ValueError):
        return {}


def save_cache(data, path=CACHE):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({str(k): v for k, v in sorted(data.items())}, f, ensure_ascii=False, indent=1)


def build_html(count, data):
    items = []
    for n in range(1, count + 1):
        d = dict(data.get(n) or {'n': n, 'ok': False})
        d['page'] = PAGE_URL.format(n=n)
        d['icon'] = d.get('icon') or ICON_FALLBACK.format(n=n)
        d.pop('v', None)
        d.pop('error', None)
        items.append(d)
    payload = json.dumps(items, ensure_ascii=False).replace('</', '<\\/')
    found = sum(1 for d in items if d.get('ok'))
    return TEMPLATE.replace('__DATA__', payload).replace('__COUNT__', str(count)).replace('__FOUND__', str(found)).replace('__UPDATED__', time.strftime('%d.%m.%Y %H:%M UTC', time.gmtime()))


TEMPLATE = r'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Каталог аддонов MajorDoMo</title>
<style>
  :root { --bg:#f3f5f7; --fg:#1d2630; --muted:#68747f; --card:#fff; --line:#dbe1e7;
          --accent:#2f7dd1; --chip:#e8eef5; --missing:#f3e6e6; --mark:#fff0b3; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#13181e; --fg:#e3e8ee; --muted:#8a96a3; --card:#1c232b; --line:#2b3440;
            --accent:#5aa2ee; --chip:#26303b; --missing:#352227; --mark:#5a4b12; color-scheme:dark; }
  }
  * { box-sizing:border-box; }
  body { margin:0; padding:16px; background:var(--bg); color:var(--fg);
         font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
  header { position:sticky; top:0; z-index:5; background:var(--bg); padding-block:4px 12px;
           display:flex; flex-wrap:wrap; gap:8px 12px; align-items:center; }
  h1 { font-size:18px; margin:0; margin-right:auto; }
  .meta { color:var(--muted); font-variant-numeric:tabular-nums; }
  input[type=search], select { padding:5px 8px; border:1px solid var(--line); border-radius:6px;
           background:var(--card); color:var(--fg); font:inherit; }
  input[type=search] { min-width:14em; flex:1 1 14em; max-width:24em; }
  label { color:var(--muted); display:flex; gap:6px; align-items:center; }

  /* сетка подстраивает число колонок под ширину окна */
  #grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(260px, 1fr)); gap:10px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px;
          display:grid; grid-template-columns:56px minmax(0,1fr); gap:4px 10px; align-content:start; }
  .card.missing { background:var(--missing); }
  .card img { width:56px; height:56px; object-fit:contain; grid-row:span 3; }
  .card img.broken { visibility:hidden; }
  .head { display:flex; gap:6px; align-items:baseline; min-width:0; }
  .num { color:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; flex:none; }
  .title { font-weight:600; color:inherit; text-decoration:none; overflow-wrap:anywhere; }
  .title:hover { color:var(--accent); }
  .sys { font:12px ui-monospace,Menlo,Consolas,monospace; overflow-wrap:anywhere; }
  .sub { color:var(--muted); font-size:12px; display:flex; flex-wrap:wrap; gap:4px 8px; align-items:center; }
  .chip { background:var(--chip); border-radius:4px; padding:0 6px; }
  .desc { grid-column:1 / -1; margin:4px 0 0; font-size:13px; white-space:pre-line;
          display:-webkit-box; -webkit-line-clamp:4; -webkit-box-orient:vertical; overflow:hidden; cursor:pointer; }
  .desc.open { display:block; }
  .foot { grid-column:1 / -1; display:flex; flex-wrap:wrap; gap:4px 12px; font-size:12px;
          color:var(--muted); font-variant-numeric:tabular-nums; margin-top:4px; }
  .foot a { color:var(--accent); text-decoration:none; }
  .foot a:hover { text-decoration:underline; }
  .stat { margin-right:auto; }
  mark { background:var(--mark); color:inherit; }
  body.hide-missing .card.missing { display:none; }
</style>
</head>
<body>
<header>
  <h1>Каталог аддонов MajorDoMo</h1>
  <span class="meta" id="stat">№1–__COUNT__ · модулей найдено: __FOUND__</span>
  <span class="meta">обновлено __UPDATED__</span>
  <input id="q" type="search" placeholder="Поиск: имя, автор, описание">
  <select id="cat"><option value="">Все категории</option></select>
  <select id="sort">
    <option value="n">По номеру</option>
    <option value="installs">По установкам</option>
    <option value="month">За месяц</option>
    <option value="sysname">По имени</option>
  </select>
  <label><input type="checkbox" id="hideMissing" checked> скрыть пустые</label>
</header>
<main id="grid"></main>

<script>
const ITEMS = __DATA__;
const grid = document.getElementById('grid');
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

[...new Set(ITEMS.map(d => d.category).filter(Boolean))].sort()
  .forEach(c => $('cat').add(new Option(c, c)));

function hl(text, q) {
  const s = esc(text);
  if (!q) return s;
  const i = s.toLowerCase().indexOf(esc(q));
  return i < 0 ? s : s.slice(0, i) + '<mark>' + s.slice(i, i + esc(q).length) + '</mark>' + s.slice(i + esc(q).length);
}

function card(d, q) {
  if (!d.ok) return `<div class="card missing"><img src="${esc(d.icon)}" alt="" loading="lazy"
      onerror="this.classList.add('broken')"><div class="head"><span class="num">№${d.n}</span>
      <a class="title" href="${esc(d.page)}" target="_blank" rel="noopener">нет данных</a></div></div>`;
  const L = d.links || {};
  return `<div class="card">
    <a href="${esc(d.page)}" target="_blank" rel="noopener" style="grid-row:span 3">
      <img src="${esc(d.icon)}" alt="" loading="lazy" onerror="this.classList.add('broken')"></a>
    <div class="head"><span class="num">№${d.n}</span>
      <a class="title" href="${esc(d.page)}" target="_blank" rel="noopener">${hl(d.title || d.sysname, q)}</a></div>
    <div class="sys">${hl(d.sysname, q)}</div>
    <div class="sub">${d.author ? '<span>' + hl(d.author, q) + '</span>' : ''}
      ${d.category ? '<span class="chip">' + esc(d.category) + '</span>' : ''}</div>
    ${d.desc ? '<p class="desc" title="Нажмите, чтобы развернуть">' + hl(d.desc, q) + '</p>' : ''}
    <div class="foot">
      <span class="stat">установок ${d.installs ?? '—'} · за месяц ${d.month ?? '—'}</span>
      ${L.download ? `<a href="${esc(L.download)}" target="_blank" rel="noopener">Скачать</a>` : ''}
      ${L.forum ? `<a href="${esc(L.forum)}" target="_blank" rel="noopener">Обсуждение</a>` : ''}
      ${L.project ? `<a href="${esc(L.project)}" target="_blank" rel="noopener">Проект</a>` : ''}
    </div></div>`;
}

function render() {
  const q = $('q').value.trim().toLowerCase();
  const cat = $('cat').value, sort = $('sort').value;
  let list = ITEMS.filter(d => {
    if (cat && d.category !== cat) return false;
    if (!q) return true;
    return [d.sysname, d.title, d.author, d.desc].some(v => (v || '').toLowerCase().includes(q));
  });
  if (sort !== 'n') list = [...list].sort((a, b) =>
    sort === 'sysname' ? (!a.sysname - !b.sysname) || (a.sysname || '').localeCompare(b.sysname || '')
                       : (b[sort] ?? -1) - (a[sort] ?? -1));
  grid.innerHTML = list.map(d => card(d, q)).join('');
  $('stat').textContent = `№1–__COUNT__ · модулей найдено: __FOUND__` +
    (q || cat ? ` · показано: ${list.filter(d => d.ok).length}` : '');
}

grid.addEventListener('click', e => {
  if (e.target.classList.contains('desc')) e.target.classList.toggle('open');
});
['q', 'cat', 'sort'].forEach(id => $(id).addEventListener('input', render));
$('hideMissing').addEventListener('change', e => document.body.classList.toggle('hide-missing', e.target.checked));
document.body.classList.toggle('hide-missing', $('hideMissing').checked);
render();
</script>
</body>
</html>
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--count', type=int, default=350, help='сколько аддонов обойти (1..N), по умолчанию 350')
    ap.add_argument('--threads', type=int, default=8, help='сколько страниц качать одновременно')
    ap.add_argument('--refresh', action='store_true',
                    help='перекачать все страницы (при сетевой ошибке остаются данные из кэша)')
    ap.add_argument('--cache', default=CACHE, help='файл кэша, по умолчанию addons_cache.json')
    ap.add_argument('-o', '--out', default='addons_table.html')
    a = ap.parse_args()

    data = load_cache(a.cache)
    if a.refresh:
        todo = list(range(1, a.count + 1))
    else:
        todo = [n for n in range(1, a.count + 1)
                if n not in data or (data[n].get('error') and data[n]['error'] != 'HTTP 404')]

    if todo:
        print(f'Скачиваю {len(todo)} страниц…')
        tty = sys.stdout.isatty()
        errors = 0
        with cf.ThreadPoolExecutor(max_workers=a.threads) as ex:
            for i, rec in enumerate(ex.map(fetch, todo), 1):
                old = data.get(rec['n'])
                net_error = rec.get('error') and rec['error'] != 'HTTP 404'
                if net_error:
                    errors += 1
                if not (net_error and old and old.get('ok')):   # не затираем хорошие данные сбоем сети
                    data[rec['n']] = rec
                mark = rec.get('sysname') or rec.get('error') or '—'
                line = f'[{i}/{len(todo)}] №{rec["n"]}: {mark}'
                if tty:
                    print('\r' + line.ljust(60), end='', flush=True)
                elif rec.get('ok') or net_error:
                    print(line)
        print()
        if errors:
            print(f'Сетевых ошибок: {errors}')
        save_cache(data, a.cache)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(build_html(a.count, data))
    found = sum(1 for n in range(1, a.count + 1) if data.get(n, {}).get('ok'))
    print(f'Готово: {os.path.abspath(a.out)} — модулей найдено {found} из {a.count}')
    if found == 0:
        sys.exit('Ни одного модуля не найдено — сайт недоступен или изменилась разметка')


if __name__ == '__main__':
    main()
