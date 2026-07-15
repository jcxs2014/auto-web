#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_rss_reader.py —— 自动拉取 RSS 订阅，统一排版成阅读页。

满足的需求：
  1. 自动拉取 RSS 订阅（由 GitHub Actions 每日 06/12/18 点运行）
  2. 保存文章到 GitHub 仓库（每源最近 7 天内的文章存于 rss/data/<slug>.json）
  3. 每个订阅源只保留最近 7 天内的文章（最多 10 篇），更陈旧的丢弃
  4. 统一排版（单一卡片样式，按日期倒序，可按分类筛选）
  5. 旧文章自动删除（每次重跑只写最近 7 天的文章，旧文不进 JSON = 自然淘汰）

订阅源来自仓库根 rss/feeds.json（用户直接编辑增删，push 后下次 CI 生效）。
"""

import json
import sys
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources_util import fetch_rss
from nav_util import NAV_CSS, build_nav, FOOTER_CSS, build_footer

ROOT = Path(__file__).resolve().parent.parent
FEEDS_FILE = ROOT / "rss" / "feeds.json"
DATA_DIR = ROOT / "rss" / "data"
OUT_HTML = ROOT / "rss" / "index.html"
PER_FEED = 10
MAX_AGE_DAYS = 7  # 仅保留最近 N 天内的文章，更陈旧的一律丢弃（避免 2019 之类旧文混入）

NAV_LINKS = (
    '<a class="nav-link" href="../hotnews/index.html">🔥 综合热点</a>'
    '<a class="nav-link" href="../news/index.html">📰 新闻</a>'
    '<a class="nav-link" href="../aihot/aihot_daily_latest.html">🤖 AI HOT</a>'
    '<a class="nav-link" href="../arxiv-physics/arxiv_physics_latest.html">📄 arXiv</a>'
)

# 分类配色（源标签背景用）
CAT_COLOR = {
    "意大利": "#e63946",
    "欧盟":   "#1d8fe1",
    "国际":   "#7b61ff",
    "科技":   "#0aa67a",
    "学术":   "#e08e0b",
}
DEFAULT_COLOR = "#6b7688"

# 主题变量与页面样式（与 hotnews 模板保持一致，支持明暗切换）
PAGE_CSS = """
:root{
  --bg:#f4f7fb; --card:#ffffff; --text:#1a2233; --muted:#6b7688; --line:#e6ebf2;
  --accent:#e63946; --accent2:#1d8fe1; --nav-bg:rgba(255,255,255,.72);
  --pill-bg:rgba(0,0,0,.05); --shadow:0 2px 10px rgba(20,40,80,.06);
}
[data-theme="dark"]{
  --bg:#0f1420; --card:#161d2e; --text:#e6ebf5; --muted:#8b97ad; --line:#26314a;
  --accent:#ff5c6a; --accent2:#4cb3ff; --nav-bg:rgba(20,28,44,.72);
  --pill-bg:rgba(255,255,255,.08); --shadow:0 2px 14px rgba(0,0,0,.35);
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  background:var(--bg);color:var(--text);line-height:1.6;overflow-x:hidden;transition:background .25s,color .25s}
__NAV_CSS__
__FOOTER_CSS__
.hero{padding:34px 22px 10px;text-align:center}
.hero h1{font-size:clamp(22px,6vw,30px);letter-spacing:.5px}
.hero p{color:var(--muted);margin-top:8px;font-size:14px}
.hero .hero-window{margin-top:12px;font-size:13px;color:var(--muted)}
.updated-badge{display:inline-block;background:var(--pill-bg);color:var(--accent2);
  border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-weight:600}
.wrap{max-width:1080px;margin:0 auto;padding:8px 18px 60px}
.filters{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;
  position:sticky;top:60px;z-index:20;padding:12px 0 14px;background:var(--bg)}
.filter{border:1px solid var(--line);background:var(--pill-bg);color:var(--text);
  padding:6px 14px;border-radius:999px;font-size:13px;cursor:pointer;white-space:nowrap;transition:border-color .15s,filter .15s}
.filter:hover{border-color:var(--accent2)}
.filter.active{background:var(--accent2);color:#fff;border-color:var(--accent2)}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px;align-items:start}
@media(max-width:760px){.grid{grid-template-columns:1fr}}
.card{position:relative;display:flex;flex-direction:column;gap:7px;text-decoration:none;
  background:var(--card);border:1px solid var(--line);border-radius:13px;padding:14px 15px;
  box-shadow:var(--shadow);transition:transform .12s,border-color .12s,background .25s;min-width:0;overflow-wrap:anywhere}
.card:hover{transform:translateY(-2px);border-color:var(--accent2)}
.card-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.src-pill{font-size:11.5px;font-weight:700;color:#fff;padding:2px 9px;border-radius:999px;white-space:nowrap}
.card-date{font-size:12px;color:var(--muted);margin-left:auto;white-space:nowrap}
.card-title{font-size:15.5px;font-weight:600;color:var(--text);text-decoration:none;line-height:1.4;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.card-title:hover{color:var(--accent2)}
.card-summary{font-size:13px;color:var(--muted);line-height:1.55;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.readmore{display:inline-block;color:var(--accent2);font-weight:600;text-decoration:none;font-size:13px;margin-top:2px}
.readmore:hover{text-decoration:underline}
.notice{padding:14px 16px;border-radius:10px;background:var(--pill-bg);color:var(--muted);font-size:13.5px;margin-bottom:16px;line-height:1.7}
.foot{text-align:center;color:var(--muted);font-size:12.5px;padding:26px 12px 10px}
"""

TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>📡 RSS 阅读 · 自动订阅聚合</title>
<script>
(function(){try{var t=localStorage.getItem('theme');if(t)document.documentElement.setAttribute('data-theme',t);}catch(e){}})();
</script>
<style>
__PAGE_CSS__
</style>
</head>
<body>
__NAV_HTML__
<header class="hero">
  <h1>📡 RSS 阅读</h1>
  <p>自动聚合你订阅的 RSS · 每个来源保留最近 7 天文章（最多 10 篇）· 统一排版</p>
  <p class="hero-window">本页更新：<span class="updated-badge">__UPDATED__</span> · 共 <strong>__TOTAL__</strong> 篇</p>
</header>
<main class="wrap">
  __NOTICE__
  <div class="filters">__FILTERS__</div>
  <div class="grid" id="feed-grid">__CARDS__</div>
</main>
__FOOTER_HTML__
<footer class="foot">
  订阅源配置见仓库 <code>rss/feeds.json</code> · 每源最新 10 篇，旧文自动淘汰 · 由 GitHub Actions 定时拉取
</footer>
<script>
  (function(){
    var btn=document.getElementById('theme-toggle');
    if(btn){btn.addEventListener('click',function(){
      var d=document.documentElement.getAttribute('data-theme')==='dark';
      document.documentElement.setAttribute('data-theme',d?'light':'dark');
      try{localStorage.setItem('theme',d?'light':'dark');}catch(e){}
      btn.textContent=d?'🌙 暗色':'☀️ 亮色';
    });}
    var nav=document.querySelector('.nav');
    var ham=document.getElementById('nav-hamburger');
    if(nav&&ham){ham.addEventListener('click',function(){
      var open=nav.classList.toggle('menu-open');
      ham.setAttribute('aria-expanded',open?'true':'false');
    });
      document.addEventListener('click',function(e){if(nav.classList.contains('menu-open')&&!nav.contains(e.target)){nav.classList.remove('menu-open');ham.setAttribute('aria-expanded','false');}});
    }
    var filters=document.querySelectorAll('.filter');
    var grid=document.getElementById('feed-grid');
    filters.forEach(function(f){f.addEventListener('click',function(){
      filters.forEach(function(x){x.classList.remove('active');});
      f.classList.add('active');
      var cat=f.getAttribute('data-cat');
      grid.querySelectorAll('.card').forEach(function(c){
        c.style.display=(cat==='all'||c.getAttribute('data-cat')===cat)?'':'none';
      });
    });});
  })();
</script>
</body>
</html>
"""


def slugify(s):
    s = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in (s or "").lower())
    return s.strip("-") or "feed"


def load_feeds():
    if not FEEDS_FILE.exists():
        return []
    try:
        d = json.loads(FEEDS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(d, dict):
        d = d.get("feeds", [])
    return [f for f in d if isinstance(f, dict) and f.get("url")]


def _date_ts(s):
    if not s:
        return 0
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%a, %d %b %Y %H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 1], fmt).timestamp()
        except Exception:
            continue
    return 0


def esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def main():
    feeds = load_feeds()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 新鲜度截点：只保留该时间戳之后的文章，更早的丢弃
    cutoff = time.time() - MAX_AGE_DAYS * 86400

    all_entries = []
    unavailable = []   # 抓取失败：源不可达 / 返回为空 / 解析错误 → 真正的源不可用
    no_recent = []     # 抓取成功，但最近 N 天内没有新文章（低频博客）→ 非故障
    for f in feeds:
        name = f.get("name") or f.get("url")
        cat = f.get("category", "其它")
        try:
            raw = fetch_rss(f["url"], name, max_n=PER_FEED)
        except Exception:
            raw = []
        # 新鲜度过滤：丢弃超过 MAX_AGE_DAYS 天的陈旧文章；
        # 抓取时拿不到可靠时间（ts<=0）的条目无法判断是否过期，保守保留。
        entries = [e for e in raw
                   if (e.get("ts") or 0) <= 0 or (e.get("ts") or 0) >= cutoff]
        if not raw:
            unavailable.append(name)      # 真没抓到 → 源可能有问题
        elif not entries:
            no_recent.append(name)        # 抓到了，但都是 N 天前的旧文
        # 保存该源最新 10 篇到仓库（旧文不写入 = 自动删除）
        slug = slugify(name)
        store = {
            "name": name, "url": f["url"], "category": cat,
            "lang": f.get("lang", ""), "entries": entries[:PER_FEED],
        }
        (DATA_DIR / f"{slug}.json").write_text(
            json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
        for e in entries:
            e["_cat"] = cat
            e["_src"] = name
            all_entries.append(e)

    # 按发布时间倒序（优先用抓取时算好的 ts，缺失时回退解析 date 字符串）
    all_entries.sort(
        key=lambda e: e.get("ts") or _date_ts(e.get("date")), reverse=True)

    # 分类筛选条
    cats = []
    for e in all_entries:
        if e["_cat"] not in cats:
            cats.append(e["_cat"])
    filters_html = '<button class="filter active" data-cat="all">全部</button>'
    for c in cats:
        filters_html += f'<button class="filter" data-cat="{esc(c)}">{esc(c)}</button>'

    # 卡片
    cards_html = ""
    for e in all_entries:
        cat = e["_cat"]
        color = CAT_COLOR.get(cat, DEFAULT_COLOR)
        date = e.get("date", "")
        summ = e.get("summary", "")
        cards_html += (
            f'<a class="card" data-cat="{esc(cat)}" href="{esc(e.get("url",""))}" '
            f'target="_blank" rel="noopener noreferrer">'
            f'<div class="card-top">'
            f'<span class="src-pill" style="background:{color}">{esc(e["_src"])}</span>'
            f'<span class="card-date">{esc(date)}</span></div>'
            f'<div class="card-title">{esc(e.get("title",""))}</div>'
            + (f'<div class="card-summary">{esc(summ)}</div>' if summ else "")
            + f'<span class="readmore">阅读全文 →</span>'
            f'</a>'
        )
    if not cards_html:
        cards_html = '<p class="notice">本次抓取未获取到任何文章（订阅源暂不可达或返回为空）。下次自动更新会重试。</p>'

    notice_html = ""
    parts = []
    if unavailable:
        parts.append(
            '⚠️ 以下订阅源本次抓取失败（不可达或返回为空），已跳过，下次自动更新会重试：'
            + "、".join(f"<strong>{esc(u)}</strong>" for u in unavailable) + "。")
    if no_recent:
        parts.append(
            f"🗓️ 以下订阅源已抓取成功，但最近 {MAX_AGE_DAYS} 天内没有新文章（多为低频个人博客），暂不展示："
            + "、".join(f"<strong>{esc(u)}</strong>" for u in no_recent) + "。")
    if parts:
        notice_html = "".join(f'<p class="notice">{p}</p>' for p in parts)

    now = datetime.now()
    updated = now.strftime("%Y-%m-%d %H:%M")

    html = (TEMPLATE
            .replace("__PAGE_CSS__", PAGE_CSS.replace("__NAV_CSS__", NAV_CSS).replace("__FOOTER_CSS__", FOOTER_CSS))
            .replace("__NAV_HTML__", build_nav("📡 RSS 阅读", NAV_LINKS))
            .replace("__FOOTER_HTML__", build_footer())
            .replace("__UPDATED__", updated)
            .replace("__TOTAL__", str(len(all_entries)))
            .replace("__NOTICE__", notice_html)
            .replace("__FILTERS__", filters_html)
            .replace("__CARDS__", cards_html))
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"RSS 阅读页已生成：{len(feeds)} 个订阅源，"
          f"{len(all_entries)} 篇近{MAX_AGE_DAYS}天文章，"
          f"{len(unavailable)} 个抓取失败，{len(no_recent)} 个无近{MAX_AGE_DAYS}天新文 -> {OUT_HTML}")


if __name__ == "__main__":
    main()
