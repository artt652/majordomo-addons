#!/usr/bin/env python3
"""
Собирает addons_table.html: каталог аддонов MajorDoMo Connect.

Источники данных:
  * JSON маркета (connect.majordomohome.com/market) — название, автор,
    категория, описание, дата последнего обновления и комментарий к нему;
  * страницы аддонов — иконка, число установок и модули, которых нет в маркете
    (они помечаются «не опубликовано»);
  * GitHub — последний коммит для неопубликованных модулей. Токен берётся из
    переменной окружения GITHUB_TOKEN (без него лимит 60 запросов в час).

Использование:
    python3 build_addons_table.py                # аддоны 1..350
    python3 build_addons_table.py --count 400
    python3 build_addons_table.py --refresh      # скачать всё заново

Нужен только стандартный Python 3. Данные кэшируются в addons_cache.json и
market_cache.json, повторный запуск докачивает только недостающее.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import email.utils
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

PAGE_URL = 'https://connect.smartliving.ru/addons/category0/{n}.html'
ICON_DEFAULT = 'https://raw.githubusercontent.com/sergejey/majordomo/master/img/modules/default.png'
SITE = 'https://connect.smartliving.ru'
CACHE = 'addons_cache.json'
MARKET_URL = 'https://connect.majordomohome.com/market/?lang=ru&all=1'
MARKET_CACHE = 'market_cache.json'
SITE_URL = 'https://artt652.github.io/majordomo-addons/'
RSS_LIMIT = 60
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
    # только иконка с номером этого аддона: на странице бывают иконки других модулей
    for src in RE_ICON.findall(page):
        if re.search(r'/%d_icon\.' % n, src):
            rec['icon'] = absolute(src)
            break
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


def http_get(url, headers=None, timeout=30):
    h = {'User-Agent': UA}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


# ---------- маркет ----------

def load_market(path, offline=False):
    """{id: запись маркета}. При ошибке сети берёт сохранённую копию."""
    if not offline:
        try:
            data = json.loads(http_get(MARKET_URL, timeout=60))
            plugins = data['PLUGINS']
            if plugins:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False)
                print(f'Маркет: {len(plugins)} опубликованных модулей')
                return {int(p['ID']): p for p in plugins}
        except Exception as e:
            print(f'Маркет недоступен ({e}), беру сохранённую копию')
    try:
        with open(path, encoding='utf-8') as f:
            return {int(p['ID']): p for p in json.load(f)['PLUGINS']}
    except (OSError, ValueError, KeyError):
        print('Сохранённой копии маркета нет — каталог соберётся только со страниц')
        return {}


# ---------- GitHub ----------

RE_GH = re.compile(r'github\.com/([^/\s]+)/([^/\s#?]+?)(?:\.git)?'
                   r'(?:/archive/(?:refs/heads/)?([^/\s]+?)\.(?:tar\.gz|zip))?(?:[/?#]|$)', re.I)


def github_last_commit(url):
    """Последний коммит репозитория по ссылке на архив или репозиторий.

    Возвращает {'date', 'comment', 'url'}; {'missing': True}, если репозитория
    нет; None при временной ошибке (лимит, сеть) — тогда проверим в следующий раз.
    """
    m = RE_GH.search(url or '')
    if not m:
        return None
    owner, repo, branch = m.groups()
    headers = {'Accept': 'application/vnd.github+json'}
    if os.environ.get('GITHUB_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GITHUB_TOKEN']
    base = f'https://api.github.com/repos/{owner}/{repo}/commits?per_page=1'
    # сначала ветка из ссылки на архив, затем ветка по умолчанию:
    # архив master.tar.gz работает и у репозиториев, где основная ветка — main
    tries = [base]
    if branch and not re.fullmatch(r'v?\d+(\.\d+)*', branch):
        tries.insert(0, base + '&sha=' + urllib.request.quote(branch))
    for api in tries:
        try:
            c = json.loads(http_get(api, headers))[0]
            break
        except urllib.error.HTTPError as e:
            if e.code in (404, 409, 422) and api != tries[-1]:
                continue                    # нет такой ветки — пробуем ветку по умолчанию
            if e.code in (404, 451):        # репозиторий удалён или скрыт
                return {'missing': True, 'checked': int(time.time())}
            if e.code == 409:               # пустой репозиторий
                return {'empty': True, 'checked': int(time.time())}
            return None
        except (IndexError, ValueError):
            return {'empty': True, 'checked': int(time.time())}
        except Exception:
            return None
    date = c['commit']['committer']['date']     # 2026-10-09T22:22:45Z
    return {'date': date.replace('T', ' ').rstrip('Z')[:19],
            'comment': c['commit']['message'].split('\n', 1)[0].strip(),
            'url': c.get('html_url', ''), 'checked': int(time.time())}


# ---------- сборка записей ----------

def merge(n, page, mk):
    """Одна карточка из страницы аддона и записи маркета."""
    page = page or {}
    d = {'n': n, 'ok': bool(mk) or bool(page.get('ok')), 'published': bool(mk),
         'icon': page.get('icon') if re.search(r'/%d_icon\.' % n, page.get('icon') or '') else ICON_DEFAULT,
         'installs': page.get('installs'), 'month': page.get('month'),
         'links': dict(page.get('links') or {}), 'page': PAGE_URL.format(n=n)}
    if mk:
        d.update(sysname=mk.get('MODULE_NAME', ''), title=mk.get('TITLE', ''),
                 author=mk.get('AUTHOR', ''), category=mk.get('CATEGORY_RU') or mk.get('CATEGORY', ''),
                 desc=re.sub(r'\r\n?', '\n', mk.get('DESCRIPTION_RU') or page.get('desc') or '').strip(),
                 updated=mk.get('LATEST_VERSION', ''), update_comment=mk.get('LATEST_VERSION_COMMENT', ''),
                 update_url=mk.get('LATEST_VERSION_URL', ''), price=(mk.get('PRICE') or '').strip(),
                 page=mk.get('URL') or d['page'])
        if mk.get('REPOSITORY_URL'):
            d['links']['download'] = mk['REPOSITORY_URL']
        if mk.get('SUPPORT_URL'):
            d['links']['forum'] = mk['SUPPORT_URL']
        if d['price'] in ('0', '0.00'):
            d['price'] = ''
    elif page.get('ok'):
        g = page.get('git') or {}
        d.update(sysname=page.get('sysname', ''), title=page.get('title', ''),
                 author=page.get('author', ''), category=page.get('category', ''),
                 desc=page.get('desc', ''), updated=g.get('date', ''),
                 update_comment=g.get('comment', ''), update_url=g.get('url', ''),
                 repo_missing=bool(g.get('missing')), repo_empty=bool(g.get('empty')))
    return d


def build_html(count, data, market):
    last = max([count] + list(market))
    items = [merge(n, data.get(n), market.get(n)) for n in range(1, last + 1)]
    payload = json.dumps(items, ensure_ascii=False).replace('</', '<\\/')
    pub = sum(1 for d in items if d['published'])
    unpub = sum(1 for d in items if d['ok'] and not d['published'])
    return (TEMPLATE.replace('__DATA__', payload).replace('__PUB__', str(pub))
            .replace('__UNPUB__', str(unpub)).replace('__ICON_DEFAULT__', ICON_DEFAULT)
            .replace('__UPDATED__', time.strftime('%d.%m.%Y %H:%M UTC', time.gmtime()))), items


# ---------- RSS ----------

def _rss_date(s):
    """'2026-10-09 22:22:45' (UTC) -> RFC 822 для <pubDate>."""
    try:
        t = dt.datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None
    return email.utils.format_datetime(t)


def build_rss(items, site_url, published_only, self_name, limit=RSS_LIMIT):
    """Лента последних обновлений модулей, новые сверху."""
    esc = html.escape
    pool = [d for d in items if d['ok'] and d.get('updated') and (d['published'] or not published_only)]
    pool.sort(key=lambda d: d['updated'], reverse=True)
    out = []
    for d in pool[:limit]:
        pub_date = _rss_date(d['updated'])
        if not pub_date:
            continue
        name = d.get('title') or d.get('sysname') or f'№{d["n"]}'
        cm = (d.get('update_comment') or '').strip()
        title = f'{name}: {cm}' if cm else f'{name}: обновление'
        if not d['published']:
            title = '[не опубликовано] ' + title
        meta = ' · '.join(x for x in (d.get('sysname'), d.get('author'), d.get('category')) if x)
        upd = esc(d['updated'][:16]) + ' UTC'
        if d.get('update_url'):
            upd = f'<a href="{esc(d["update_url"])}">{upd}</a>'
        body = [f'<p><img src="{esc(d["icon"])}" width="48" height="48" alt=""> {esc(meta)}</p>',
                f'<p>Обновление {upd}' + (f': {esc(cm)}' if cm else '') + '</p>']
        if d.get('desc'):
            desc = d['desc'] if len(d['desc']) <= 600 else d['desc'][:600].rsplit(' ', 1)[0] + '…'
            body.append('<p>' + esc(desc).replace('\n', '<br>') + '</p>')
        links = d.get('links') or {}
        if links.get('download'):
            body.append(f'<p><a href="{esc(links["download"])}">Скачать</a></p>')
        guid = f'mjdm-addon-{d["n"]}-{d["updated"].replace(" ", "T")}'
        out.append(
            '  <item>\n'
            f'    <title>{esc(title)}</title>\n'
            f'    <link>{esc(d["page"])}</link>\n'
            f'    <guid isPermaLink="false">{esc(guid)}</guid>\n'
            f'    <pubDate>{pub_date}</pubDate>\n'
            + (f'    <category>{esc(d["category"])}</category>\n' if d.get('category') else '')
            + (f'    <dc:creator>{esc(d["author"])}</dc:creator>\n' if d.get('author') else '')
            + f'    <description>{esc("".join(body))}</description>\n'
            '  </item>')
    what = 'опубликованных модулей' if published_only else 'всех модулей, включая неопубликованные'
    now = email.utils.format_datetime(dt.datetime.now(dt.timezone.utc))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/">\n<channel>\n'
            f'  <title>Обновления аддонов MajorDoMo{"" if published_only else " (все)"}</title>\n'
            f'  <link>{esc(site_url)}</link>\n'
            f'  <atom:link href="{esc(site_url + self_name)}" rel="self" type="application/rss+xml"/>\n'
            f'  <description>Последние обновления {what} из каталога MajorDoMo Connect</description>\n'
            '  <language>ru</language>\n'
            f'  <lastBuildDate>{now}</lastBuildDate>\n'
            '  <ttl>720</ttl>\n'
            + '\n'.join(out) + '\n</channel>\n</rss>\n')


TEMPLATE = r'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Каталог аддонов MajorDoMo</title>
<link rel="alternate" type="application/rss+xml" title="Обновления аддонов MajorDoMo" href="feed.xml">
<link rel="alternate" type="application/rss+xml" title="Обновления аддонов MajorDoMo (все)" href="feed-all.xml">
<style>
  :root { --bg:#f3f5f7; --fg:#1d2630; --muted:#68747f; --card:#fff; --line:#dbe1e7;
          --accent:#2f7dd1; --chip:#e8eef5; --missing:#f3e6e6; --mark:#fff0b3;
          --warn-bg:#fdf0d5; --warn-fg:#8a5a00; --fresh:#1f7a45; --ver-bg:#e2f1e7; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#13181e; --fg:#e3e8ee; --muted:#8a96a3; --card:#1c232b; --line:#2b3440;
            --accent:#5aa2ee; --chip:#26303b; --missing:#352227; --mark:#5a4b12;
            --warn-bg:#3d3018; --warn-fg:#f0c674; --fresh:#6fd39a; --ver-bg:#1d3a2a; color-scheme:dark; }
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
  .btn { padding:5px 10px; border:1px solid var(--line); border-radius:6px; background:var(--card);
         color:var(--fg); font:inherit; cursor:pointer; white-space:nowrap; }
  .btn:hover { border-color:var(--accent); }
  .btn:focus-visible, select:focus-visible, input:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
  .btn.link { border-color:transparent; background:none; color:var(--muted); padding-inline:4px; }
  .btn.link:hover { color:var(--accent); }
  a.btn { text-decoration:none; }
  .rss { color:var(--warn-fg); }

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
  .chip.unpub { background:var(--warn-bg); color:var(--warn-fg); font-weight:600; }
  .chip.ver { background:var(--ver-bg); color:var(--fresh); font:600 11px ui-monospace,Menlo,Consolas,monospace; }
  .upd { grid-column:1 / -1; font-size:12px; color:var(--muted); display:flex; flex-wrap:wrap;
         gap:2px 8px; align-items:baseline; font-variant-numeric:tabular-nums; min-width:0; }
  .upd a { color:inherit; }
  .upd .date.fresh { color:var(--fresh); font-weight:600; }
  .upd .cm { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; min-width:0; flex:1 1 8em; }
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
  <span class="meta" id="stat">опубликовано __PUB__ · не опубликовано __UNPUB__</span>
  <span class="meta">собрано __UPDATED__</span>
  <input id="q" type="search" placeholder="Поиск: имя, автор, описание">
  <select id="cat"><option value="">Все категории</option></select>
  <select id="pub">
    <option value="1" selected>Опубликованные</option>
    <option value="">Все модули</option>
    <option value="0">Не опубликованные</option>
  </select>
  <select id="sort">
    <option value="n">По номеру</option>
    <option value="updated">По обновлению</option>
    <option value="installs">По установкам</option>
    <option value="month">За месяц</option>
    <option value="sysname">По имени</option>
  </select>
  <button type="button" id="dir" class="btn" aria-label="Направление сортировки"></button>
  <label><input type="checkbox" id="hideMissing" checked> скрыть пустые</label>
  <button type="button" id="reset" class="btn link" title="Вернуть настройки по умолчанию">сбросить</button>
  <a class="btn link rss" href="feed.xml" title="RSS: обновления опубликованных модулей">RSS</a>
  <a class="btn link rss" href="feed-all.xml" title="RSS: обновления всех модулей, включая неопубликованные">RSS (все)</a>
</header>
<main id="grid"></main>

<script>
const ITEMS = __DATA__;
const grid = document.getElementById('grid');
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const RE_VER = /^v?\d+(\.\d+)+[\w.-]*$/i;
const DAY = 864e5, NOW = Date.now();
const ICON_DEFAULT = '__ICON_DEFAULT__';
const IMG_ERR = `if(this.src!==ICON_DEFAULT){this.src=ICON_DEFAULT}else{this.classList.add('broken')}`;

[...new Set(ITEMS.map(d => d.category).filter(Boolean))].sort()
  .forEach(c => $('cat').add(new Option(c, c)));

function hl(text, q) {
  const s = esc(text);
  if (!q) return s;
  const i = s.toLowerCase().indexOf(esc(q));
  return i < 0 ? s : s.slice(0, i) + '<mark>' + s.slice(i, i + esc(q).length) + '</mark>' + s.slice(i + esc(q).length);
}

function ago(t) {
  const d = Math.floor((NOW - t) / DAY);
  if (d < 1) return 'сегодня';
  if (d < 2) return 'вчера';
  if (d < 31) return d + ' дн. назад';
  if (d < 365) return Math.floor(d / 30.4) + ' мес. назад';
  const y = Math.floor(d / 365);
  return y + (y % 10 === 1 && y % 100 !== 11 ? ' год' : (y % 10 >= 2 && y % 10 <= 4 && (y % 100 < 10 || y % 100 >= 20)) ? ' года' : ' лет') + ' назад';
}

function updLine(d) {
  if (!d.published && d.repo_missing) return '<div class="upd">репозиторий на GitHub недоступен</div>';
  if (!d.published && d.repo_empty) return '<div class="upd">репозиторий на GitHub пуст</div>';
  if (!d.updated) return d.published ? '' : '<div class="upd">нет данных о коммитах</div>';
  const t = Date.parse(d.updated.replace(' ', 'T') + 'Z');
  const [y, m, dd] = d.updated.slice(0, 10).split('-');
  const date = `${dd}.${m}.${y}`;
  const fresh = NOW - t < 90 * DAY ? ' fresh' : '';
  const cm = (d.update_comment || '').trim();
  const isVer = RE_VER.test(cm);
  const what = d.published ? 'обновлён' : 'коммит';
  const dateHtml = d.update_url
    ? `<a class="date${fresh}" href="${esc(d.update_url)}" target="_blank" rel="noopener" title="${esc(d.updated)} UTC">${date}</a>`
    : `<span class="date${fresh}" title="${esc(d.updated)} UTC">${date}</span>`;
  return `<div class="upd">${isVer ? `<span class="chip ver">${esc(cm)}</span>` : ''}
    <span>${what} ${dateHtml} · ${ago(t)}</span>
    ${cm && !isVer ? `<span class="cm" title="${esc(cm)}">${esc(cm)}</span>` : ''}</div>`;
}

function card(d, q) {
  if (!d.ok) return `<div class="card missing"><img src="${esc(d.icon)}" alt="" loading="lazy"
      onerror="${IMG_ERR}"><div class="head"><span class="num">№${d.n}</span>
      <a class="title" href="${esc(d.page)}" target="_blank" rel="noopener">нет данных</a></div></div>`;
  const L = d.links || {};
  return `<div class="card">
    <a href="${esc(d.page)}" target="_blank" rel="noopener" style="grid-row:span 3">
      <img src="${esc(d.icon)}" alt="" loading="lazy" onerror="${IMG_ERR}"></a>
    <div class="head"><span class="num">№${d.n}</span>
      <a class="title" href="${esc(d.page)}" target="_blank" rel="noopener">${hl(d.title || d.sysname, q)}</a></div>
    <div class="sys">${hl(d.sysname, q)}</div>
    <div class="sub">${d.published ? '' : '<span class="chip unpub">не опубликовано</span>'}
      ${d.author ? '<span>' + hl(d.author, q) + '</span>' : ''}
      ${d.category ? '<span class="chip">' + esc(d.category) + '</span>' : ''}
      ${d.price ? '<span class="chip">' + esc(String(+d.price)) + ' ₽</span>' : ''}</div>
    ${updLine(d)}
    ${d.desc ? '<p class="desc" title="Нажмите, чтобы развернуть">' + hl(d.desc, q) + '</p>' : ''}
    <div class="foot">
      <span class="stat">установок ${d.installs ?? '—'} · за месяц ${d.month ?? '—'}</span>
      ${L.download ? `<a href="${esc(L.download)}" target="_blank" rel="noopener">Скачать</a>` : ''}
      ${L.forum ? `<a href="${esc(L.forum)}" target="_blank" rel="noopener">Обсуждение</a>` : ''}
      ${L.project ? `<a href="${esc(L.project)}" target="_blank" rel="noopener">Проект</a>` : ''}
    </div></div>`;
}

// направление по умолчанию для каждого вида сортировки: 1 — по возрастанию, -1 — по убыванию
const SORT_DIR = { n: 1, updated: -1, installs: -1, month: -1, sysname: 1 };
const SORT_KEY = {
  n: d => d.n,
  updated: d => d.updated || null,
  installs: d => d.installs ?? null,
  month: d => d.month ?? null,
  sysname: d => (d.sysname || '').toLowerCase() || null,
};
const DIR_LABEL = {
  n: ['1 → 9', '9 → 1'], sysname: ['А → Я', 'Я → А'],
  updated: ['старые сначала', 'новые сначала'],
  installs: ['меньше сначала', 'больше сначала'], month: ['меньше сначала', 'больше сначала'],
};
let dir = 1;

function showDir() {
  const sort = $('sort').value;
  $('dir').textContent = (dir > 0 ? '↑ ' : '↓ ') + DIR_LABEL[sort][dir > 0 ? 0 : 1];
  $('dir').title = dir > 0 ? 'По возрастанию — нажмите, чтобы изменить' : 'По убыванию — нажмите, чтобы изменить';
}

// ---- настройки в браузере ----
const STORE = 'mjdm-addons-settings';
const DEFAULTS = { cat: '', pub: '1', sort: 'n', dir: 1, hideMissing: true };

function save() {
  try {
    localStorage.setItem(STORE, JSON.stringify({ cat: $('cat').value, pub: $('pub').value,
      sort: $('sort').value, dir, hideMissing: $('hideMissing').checked }));
  } catch (e) { /* хранилище недоступно — работаем без него */ }
}

function apply(st) {
  const has = (sel, v) => [...$(sel).options].some(o => o.value === v);
  $('cat').value = has('cat', st.cat) ? st.cat : '';
  $('pub').value = has('pub', st.pub) ? st.pub : DEFAULTS.pub;
  $('sort').value = has('sort', st.sort) ? st.sort : DEFAULTS.sort;
  dir = st.dir === -1 ? -1 : 1;
  $('hideMissing').checked = st.hideMissing !== false;
  document.body.classList.toggle('hide-missing', $('hideMissing').checked);
  showDir();
}

function load() {
  let st = {};
  try { st = JSON.parse(localStorage.getItem(STORE) || '{}') || {}; } catch (e) { st = {}; }
  apply(Object.assign({}, DEFAULTS, st));
}

function render() {
  const q = $('q').value.trim().toLowerCase();
  const cat = $('cat').value, pub = $('pub').value, sort = $('sort').value;
  let list = ITEMS.filter(d => {
    if (cat && d.category !== cat) return false;
    if (pub && (!d.ok || String(+d.published) !== pub)) return false;
    if (!q) return true;
    return [d.sysname, d.title, d.author, d.desc, d.update_comment].some(v => (v || '').toLowerCase().includes(q));
  });
  const key = SORT_KEY[sort];
  list = [...list].sort((a, b) => {
    const x = key(a), y = key(b);
    if (x === null || y === null) return (x === null) - (y === null) || a.n - b.n;   // пустые всегда в конце
    const c = typeof x === 'string' ? x.localeCompare(y) : x - y;
    return c * dir || a.n - b.n;
  });
  grid.innerHTML = list.map(d => card(d, q)).join('');
  $('stat').textContent = 'опубликовано __PUB__ · не опубликовано __UNPUB__' +
    ` · показано: ${list.filter(d => d.ok).length}`;
}

grid.addEventListener('click', e => {
  if (e.target.classList.contains('desc')) e.target.classList.toggle('open');
});
$('q').addEventListener('input', render);
['cat', 'pub'].forEach(id => $(id).addEventListener('input', () => { save(); render(); }));
$('sort').addEventListener('input', () => { dir = SORT_DIR[$('sort').value]; showDir(); save(); render(); });
$('dir').addEventListener('click', () => { dir = -dir; showDir(); save(); render(); });
$('hideMissing').addEventListener('change', e => {
  document.body.classList.toggle('hide-missing', e.target.checked); save();
});
$('reset').addEventListener('click', () => {
  try { localStorage.removeItem(STORE); } catch (e) {}
  $('q').value = ''; apply(DEFAULTS); render();
});
load();
render();
</script>
</body>
</html>
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--count', type=int, default=350,
                    help='сколько страниц аддонов обойти (1..N), по умолчанию 350; '
                         'модули из маркета с большим номером попадут в каталог всё равно')
    ap.add_argument('--threads', type=int, default=8, help='сколько страниц качать одновременно')
    ap.add_argument('--refresh', action='store_true',
                    help='перекачать все страницы и коммиты (при сетевой ошибке остаются данные из кэша)')
    ap.add_argument('--cache', default=CACHE, help='файл кэша страниц, по умолчанию addons_cache.json')
    ap.add_argument('--json', help='куда дополнительно сохранить итоговые данные в JSON')
    ap.add_argument('--site-url', default=SITE_URL, help='адрес сайта для ссылок в RSS, по умолчанию ' + SITE_URL)
    ap.add_argument('-o', '--out', default='addons_table.html')
    a = ap.parse_args()

    cache_dir = os.path.dirname(os.path.abspath(a.cache))
    market = load_market(os.path.join(cache_dir, MARKET_CACHE))
    last = max([a.count] + list(market))

    data = load_cache(a.cache)
    if a.refresh:
        todo = list(range(1, last + 1))
    else:
        todo = [n for n in range(1, last + 1)
                if n not in data or (data[n].get('error') and data[n]['error'] != 'HTTP 404')]

    tty = sys.stdout.isatty()
    if todo:
        print(f'Скачиваю {len(todo)} страниц…')
        errors = 0
        with cf.ThreadPoolExecutor(max_workers=a.threads) as ex:
            for i, rec in enumerate(ex.map(fetch, todo), 1):
                old = data.get(rec['n'])
                net_error = rec.get('error') and rec['error'] != 'HTTP 404'
                if net_error:
                    errors += 1
                if old and old.get('git'):
                    rec['git'] = old['git']                     # коммит обновим отдельно
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

    # неопубликованные модули: последний коммит с GitHub
    git_todo = [n for n in range(1, last + 1)
                if n not in market and data.get(n, {}).get('ok')
                and (a.refresh or not data[n].get('git'))]
    if git_todo:
        print(f'GitHub: проверяю {len(git_todo)} неопубликованных модулей…')
        urls = [data[n].get('links', {}).get('download', '') for n in git_todo]
        with cf.ThreadPoolExecutor(max_workers=4) as ex:
            for n, g in zip(git_todo, ex.map(github_last_commit, urls)):
                if g is not None:
                    data[n]['git'] = g
                elif not RE_GH.search(data[n].get('links', {}).get('download', '')):
                    data[n]['git'] = {'checked': int(time.time())}   # не GitHub — не проверяем каждый раз
        got = sum(1 for n in git_todo if data[n].get('git', {}).get('date'))
        print(f'GitHub: дата коммита есть у {got} из {len(git_todo)}')

    if todo or git_todo:
        save_cache(data, a.cache)

    page, items = build_html(a.count, data, market)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write(page)
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump([d for d in items if d['ok']], f, ensure_ascii=False, indent=1)
    site = a.site_url.rstrip('/') + '/'
    out_dir = os.path.dirname(os.path.abspath(a.out))
    for name, only_pub in (('feed.xml', True), ('feed-all.xml', False)):
        with open(os.path.join(out_dir, name), 'w', encoding='utf-8') as f:
            f.write(build_rss(items, site, only_pub, name))

    pub = sum(1 for d in items if d['published'])
    unpub = sum(1 for d in items if d['ok'] and not d['published'])
    print(f'Готово: {os.path.abspath(a.out)} — опубликовано {pub}, не опубликовано {unpub}')
    if pub + unpub == 0:
        sys.exit('Ни одного модуля не найдено — сайт недоступен или изменилась разметка')


if __name__ == '__main__':
    main()
