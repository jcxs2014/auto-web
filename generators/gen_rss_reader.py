#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_rss_reader.py —— 自动拉取 RSS 订阅，统一排版成阅读页。

满足的需求：
  1. 自动拉取 RSS 订阅（由 GitHub Actions 每日 06/12/18 点运行）
  2. 保存文章到 GitHub 仓库（每源最近 7 天内的文章存于 rss/data/<slug>.json，含正文全文）
  3. 每个订阅源只保留最近 7 天内的文章（最多 10 篇），更陈旧的丢弃
  4. 统一排版（单一卡片样式，按日期倒序，可按分类筛选；点击进入整页文章视图/极简阅读器看全文）
  5. 旧文章自动删除（每次重跑只写最近 7 天的文章，旧文不进 JSON = 自然淘汰）
  6. 正文全文：对每篇展示中的文章用 trafilatura 抽取正文，存进仓库并内联展示（抓取失败则回退外链）
  7. 源健康检测（garss 式）：抓取状态持久化到 rss/feed_health.json，连续 3 次失败自动停用该源，
     页面单独提示；恢复成功自动启用。feeds.json 保持人工所有、不被改写。

订阅源来自仓库根 rss/feeds.json（用户直接编辑增删，push 后下次 CI 生效）。
"""

import json
import sys
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sources_util import fetch_rss, fetch_article_text
from nav_util import NAV_CSS, build_nav, FOOTER_CSS, build_footer

ROOT = Path(__file__).resolve().parent.parent
FEEDS_FILE = ROOT / "rss" / "feeds.json"
DATA_DIR = ROOT / "rss" / "data"
HEALTH_FILE = ROOT / "rss" / "feed_health.json"
OUT_HTML = ROOT / "rss" / "index.html"
TRANSLATE_HTML = ROOT / "rss" / "translate.html"
# 翻译中转页：真实 https URL、<html lang="en"> 的独立页。打开即被浏览器识别为外语，
# 弹出原生「是否翻译此页」提示（不离开本站、不跳原文站）。JS 按 ?src=<源slug>&id=<文章url>
# 读取 rss/data/<slug>.json 中对应文章的正文渲染。
TRANSLATE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="content-language" content="en">
<title>翻译 · RSS</title>
<style>
  body{font:16px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;max-width:760px;margin:0 auto;padding:24px 18px 80px;color:#1a1a1a;background:#fff;word-wrap:break-word}
  .bar{position:sticky;top:0;background:#f3f6fa;border-bottom:1px solid #e3e8ef;padding:10px 14px;font-size:13.5px;color:#555;margin:-24px -18px 22px}
  .bar a{color:#1a6fc4;text-decoration:none}.bar b{color:#111}
  h1{font-size:24px;line-height:1.35;margin:0 0 14px;font-weight:800}
  img{max-width:100%;height:auto;border-radius:8px;margin:1em 0}a{color:#1a6fc4}
  p{margin:0 0 1.1em}blockquote{border-left:3px solid #1a6fc4;margin:1em 0;padding:.3em 1em;color:#444}
  .err{padding:40px 0;color:#c0392b;font-size:15px}
</style>
</head>
<body>
  <div class="bar">🌐 浏览器可能提示「翻译此页」 · 来源：<b id="src"></b> · <a id="origin" href="#" target="_blank" rel="noopener noreferrer">查看原文 →</a></div>
  <h1 id="title"></h1>
  <div id="article"></div>
<script>
(function(){
  var q=new URLSearchParams(location.search);
  var src=q.get('src'), id=q.get('id');
  var titleEl=document.getElementById('title');
  var srcEl=document.getElementById('src');
  var artEl=document.getElementById('article');
  var originEl=document.getElementById('origin');
  if(!src||!id){titleEl.textContent='参数缺失（需要 src 与 id）';return;}
  fetch('data/'+encodeURIComponent(src)+'.json').then(function(r){
    if(!r.ok) throw new Error('HTTP '+r.status);
    return r.json();
  }).then(function(d){
    var entries=d.entries||[];
    var e=null;
    for(var i=0;i<entries.length;i++){ if(entries[i].url===id){e=entries[i];break;} }
    if(!e){titleEl.textContent='未找到该文章（可能已超出近 7 天保留期）';return;}
    document.title=(d.name||'翻译')+' · RSS';
    if(srcEl)srcEl.textContent=d.name||'';
    titleEl.textContent=e.title||'(无标题)';
    if(originEl&&e.url)originEl.href=e.url;
    artEl.innerHTML=e.content||e.summary||'(该源未抓取全文，仅有摘要)';
  }).catch(function(err){
    titleEl.textContent='加载失败：'+err.message;
  });
})();
</script>
</body>
</html>"""
PER_FEED = 10
MAX_AGE_DAYS = 7  # 仅保留最近 N 天内的文章，更陈旧的一律丢弃（避免 2019 之类旧文混入）
MAX_FAILS = 3      # 连续抓取失败达到该次数即自动停用该源（garss 式健康检测）

NAV_LINKS = (
    '<a class="nav-link" href="../hotnews/index.html">🔥 综合热点</a>'
    '<a class="nav-link" href="../news/index.html">📰 新闻</a>'
    '<a class="nav-link" href="../aihot/aihot_daily_latest.html">🤖 AI HOT</a>'
    '<a class="nav-link" href="../arxiv-physics/arxiv_physics_latest.html">📄 arXiv</a>'
)

# 分类配色（源标签背景 / 分块色点用）。覆盖 feeds.json 中的所有分类。
CAT_COLOR = {
    # 中文博客（独立成块）
    "中文博客": "#e8543f",
    "外文博客": "#64748b",
    # 期刊（卫报/纽约时报/半岛电视台/德国之声/华盛顿邮报/金融时报/彭博/经济学人/大西洋月刊/连线 合并一类）
    "期刊": "#1d4ed8",
    # 读书（书评 / 阅读 / 文学 / 文化类）
    "读书": "#7c3aed",
    # 其余类型
    "周刊": "#db2777",
    "资讯": "#0aa67a",
    "技术": "#2563eb",
    "AI":   "#7b61ff",
    "广播": "#0891b2",
}
DEFAULT_COLOR = "#6b7688"

# 分块（section）的展示顺序：中文博客独立在前，其次外文博客，再次期刊，再其余类型
GROUP_ORDER = [
    "中文博客",
    "外文博客",
    "期刊",
    "读书",
    "周刊", "资讯", "技术", "AI", "广播",
]

# 主题变量与页面样式（与 hotnews 模板保持一致，支持明暗切换）
PAGE_CSS = """
:root{
  --bg:#f4f7fb; --card:#ffffff; --text:#1a2233; --muted:#6b7688; --line:#e6ebf2;
  --accent:#e63946; --accent2:#1d8fe1; --nav-bg:rgba(255,255,255,.72);
  --pill-bg:rgba(0,0,0,.05); --code-bg:#eef2f7; --shadow:0 2px 10px rgba(20,40,80,.06);
}
[data-theme="dark"]{
  --bg:#0f1420; --card:#161d2e; --text:#e6ebf5; --muted:#8b97ad; --line:#26314a;
  --accent:#ff5c6a; --accent2:#4cb3ff; --nav-bg:rgba(20,28,44,.72);
  --pill-bg:rgba(255,255,255,.08); --code-bg:#0b0f18; --shadow:0 2px 14px rgba(0,0,0,.35);
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
.readmore-btn{display:inline-block;background:var(--pill-bg);color:var(--accent2);border:1px solid var(--line);
  border-radius:8px;padding:5px 12px;font-size:13px;cursor:pointer;font-weight:600;margin-top:2px;align-self:flex-start;transition:border-color .15s}
.readmore-btn:hover{border-color:var(--accent2)}
.notice{padding:14px 16px;border-radius:10px;background:var(--pill-bg);color:var(--muted);font-size:13.5px;margin-bottom:16px;line-height:1.7}
.foot{text-align:center;color:var(--muted);font-size:12.5px;padding:26px 12px 10px}

/* ===== 分块标题（中文博客 / 各家报刊 各自成块）===== */
.section-head{grid-column:1/-1;display:flex;align-items:center;gap:10px;
  margin:24px 2px 6px;padding:7px 0 8px;border-bottom:2px solid var(--line)}
.section-head:first-child{margin-top:6px}
.section-head .dot{width:11px;height:11px;border-radius:3px;flex:none}
.section-head h2{font-size:17px;font-weight:800;letter-spacing:.3px;line-height:1.2}
.section-head .cnt{font-size:12px;color:var(--muted);margin-left:2px}
.section-head .cnt::before{content:"· ";opacity:.6}

/* ===== 整页文章视图（极简阅读器）===== */
.reader-overlay{position:fixed;inset:0;z-index:1000;background:var(--bg);
  overflow-y:auto;display:none}
.reader-overlay.open{display:block;animation:readerin .22s ease}
@keyframes readerin{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.reader-bar{position:sticky;top:0;z-index:2;display:flex;align-items:center;gap:12px;
  padding:12px 18px;background:var(--nav-bg);backdrop-filter:saturate(1.4) blur(10px);
  -webkit-backdrop-filter:saturate(1.4) blur(10px);border-bottom:1px solid var(--line)}
.reader-back{display:inline-flex;align-items:center;gap:6px;cursor:pointer;
  border:1px solid var(--line);background:var(--pill-bg);color:var(--text);
  border-radius:999px;padding:6px 15px;font-size:14px;font-weight:600;transition:border-color .15s}
.reader-back:hover{border-color:var(--accent2)}
.reader-srctag{color:var(--muted);font-size:13px;margin-left:auto;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;max-width:50vw}
.reader-article{max-width:720px;margin:0 auto;padding:38px 24px 96px}
.reader-title{font-size:clamp(23px,5vw,33px);line-height:1.3;font-weight:800;letter-spacing:.2px;margin-bottom:16px;overflow-wrap:anywhere;font-style:normal}
.reader-meta{display:flex;flex-wrap:wrap;gap:12px;align-items:center;
  color:var(--muted);font-size:13.5px;padding-bottom:18px;margin-bottom:26px;
  border-bottom:1px solid var(--line);font-style:normal}
.reader-meta .src-pill{font-size:11.5px}
.reader-origin{color:var(--accent2);text-decoration:none;font-weight:600;margin-left:auto;white-space:nowrap}
.reader-origin:hover{text-decoration:underline}
.reader-translate{display:inline-flex;align-items:center;gap:4px;color:var(--accent2);
  text-decoration:none;font-weight:600;white-space:nowrap;border:1px solid var(--accent2);
  border-radius:999px;padding:3px 12px;transition:background .15s;background:none;font:inherit;cursor:pointer}
.reader-translate:hover{background:var(--accent-soft)}
.reader-body{font-size:17px;line-height:1.85;color:var(--text);overflow-wrap:anywhere;word-break:break-word;text-align:justify;text-justify:inter-ideograph;hyphens:auto;font-style:normal}
.reader-body::selection{background:var(--accent2);color:#fff}
.reader-body>p{margin:0 0 1.15em}
.reader-body>h1,.reader-body>h2,.reader-body>h3,.reader-body>h4{line-height:1.35;font-weight:800;margin:1.6em 0 .7em}
.reader-body>h2{font-size:1.5em}.reader-body>h3{font-size:1.28em}.reader-body>h4{font-size:1.12em}
.reader-body a{color:var(--accent2);text-decoration:underline;text-underline-offset:2px}
.reader-body strong,.reader-body b{font-weight:700}
/* 中和斜体：trafilatura 抽取常把整段正文误标为斜体（<i>/<em>），且中文斜体为强制倾斜、观感差，
   统一按正常字形显示；真正的强调仍由 <strong>/<b> 加粗承担 */
.reader-body em,.reader-body i,.reader-body cite,.reader-body var,.reader-body dfn{font-style:normal}
.reader-body blockquote{margin:1.1em 0;padding:.4em 1.1em;border-left:3px solid var(--accent2);
  background:var(--pill-bg);color:var(--muted);border-radius:0 8px 8px 0}
.reader-body blockquote p{margin:.4em 0}
.reader-body pre{margin:1.1em 0;padding:14px 16px;background:var(--code-bg);border:1px solid var(--line);
  border-radius:10px;overflow:auto;font-size:13.5px;line-height:1.6}
.reader-body code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.92em;
  background:var(--code-bg);border:1px solid var(--line);border-radius:5px;padding:.1em .4em}
.reader-body pre code{border:0;background:none;padding:0}
.reader-body ul,.reader-body ol{margin:0 0 1.1em;padding-left:1.5em}
.reader-body li{margin:.35em 0}
.reader-body img{max-width:100%;height:auto;border-radius:10px;margin:1em 0;display:block}
.reader-body figure{margin:1.2em 0;text-align:center}
.reader-body figure img{margin:0 auto}
.reader-body figcaption{font-size:13px;color:var(--muted);margin-top:.4em}
.reader-body hr{border:0;border-top:1px solid var(--line);margin:1.6em 0}
.reader-body table{border-collapse:collapse;width:100%;margin:1.1em 0;font-size:14px}
.reader-body th,.reader-body td{border:1px solid var(--line);padding:7px 10px;text-align:left}
.reader-body th{background:var(--pill-bg);font-weight:700}
@media(max-width:760px){.reader-article{padding:26px 18px 80px}.reader-body{font-size:16px}}
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
  <p>自动聚合你订阅的 RSS · 每个来源保留最近 7 天文章（最多 10 篇）· 点击进入整页阅读视图看正文全文</p>
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

<!-- 整页文章视图（极简阅读器）：点击卡片「阅读全文」后铺满全屏展示正文 -->
<div class="reader-overlay" id="reader" role="dialog" aria-modal="true" aria-label="文章阅读视图">
  <div class="reader-bar">
    <button class="reader-back" type="button" onclick="closeReader()">← 返回列表</button>
    <span class="reader-srctag" id="reader-srctag"></span>
  </div>
  <article class="reader-article">
    <h1 class="reader-title" id="reader-title"></h1>
    <div class="reader-meta" id="reader-meta"></div>
    <div class="reader-body" id="reader-body"></div>
  </article>
</div>
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
      grid.querySelectorAll('.card,.section-head').forEach(function(c){
        c.style.display=(cat==='all'||c.getAttribute('data-cat')===cat)?'':'none';
      });
    });});
    // 整页文章视图：从卡片读取标题/来源/日期/正文/原文链接，注入阅读器并铺满全屏
    var reader=document.getElementById('reader');
    window.openReader=function(btn){
      var card=btn.closest('.card'); if(!card)return;
      var srcEl=card.querySelector('.src-pill');
      var dateEl=card.querySelector('.card-date');
      var titleEl=card.querySelector('.card-title');
      var bodyEl=card.querySelector('.article-body');
      // 阅读区标注文章语言（语义/无障碍用；浏览器原生翻译是文档级，不会因此弹提示）
      var lang=bodyEl?bodyEl.getAttribute('lang'):'';
      if(lang){reader.setAttribute('lang',lang);}else{reader.removeAttribute('lang');}
      document.getElementById('reader-srctag').textContent=srcEl?srcEl.textContent:'';
      document.getElementById('reader-title').textContent=titleEl?titleEl.textContent:'';
      var meta=document.getElementById('reader-meta'); meta.innerHTML='';
      if(srcEl){var p=document.createElement('span');p.className='src-pill';
        p.style.background=srcEl.style.background;p.textContent=srcEl.textContent;meta.appendChild(p);}
      if(dateEl&&dateEl.textContent){var d=document.createElement('span');d.textContent=dateEl.textContent;meta.appendChild(d);}
      var href=titleEl?titleEl.getAttribute('href'):'';
      if(href){
        // 非中文文章：点「翻译」打开站内 translate.html（真实 https URL、lang=en），
        // 浏览器据此弹出原生「是否翻译此页」提示（不离开本站）
        if(lang && lang!=='zh' && lang!=='zh-CN'){
          var card=btn.closest('.card');
          var dslug=card?card.getAttribute('data-slug'):'';
          if(dslug){
            var t=document.createElement('a');t.className='reader-translate';
            t.href='translate.html?src='+encodeURIComponent(dslug)+'&id='+encodeURIComponent(href);
            t.target='_blank';t.rel='noopener noreferrer';t.textContent='🌐 翻译';
            meta.appendChild(t);
          }
        }
        var a=document.createElement('a');a.className='reader-origin';a.href=href;
        a.target='_blank';a.rel='noopener noreferrer';a.textContent='查看原文 →';meta.appendChild(a);
      }
      document.getElementById('reader-body').innerHTML=bodyEl?bodyEl.innerHTML:'';
      reader.classList.add('open'); reader.scrollTop=0;
      document.body.style.overflow='hidden';
      try{history.pushState({reader:1},'');}catch(e){}
    };
    window.closeReader=function(){
      reader.classList.remove('open');
      reader.removeAttribute('lang');
      document.body.style.overflow='';
    };
    // ESC 关闭 + 浏览器返回键关闭
    document.addEventListener('keydown',function(e){
      if(e.key==='Escape'&&reader.classList.contains('open'))closeReader();
    });
    window.addEventListener('popstate',function(){
      if(reader.classList.contains('open'))closeReader();
    });
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


def load_health():
    """读取各源的健康状态：{url: {fails, last_ok, last_err, last_try, disabled}}。"""
    if not HEALTH_FILE.exists():
        return {}
    try:
        d = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_health(health):
    try:
        HEALTH_FILE.write_text(
            json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


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
    unavailable = []   # 本次抓取失败（瞬时），下次自动重试
    no_recent = []     # 抓取成功，但最近 N 天内没有新文章（低频博客）→ 非故障
    disabled = []      # 连续失败达阈值、已自动停用（含失败原因）
    health = load_health()
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M")
    for f in feeds:
        name = f.get("name") or f.get("url")
        url = f["url"]
        cat = f.get("category", "其它")
        # 健康状态（跨运行持久化，用于自动停用判定）
        h = health.setdefault(
            url, {"fails": 0, "last_ok": "", "last_err": "", "last_try": "", "disabled": False})
        try:
            raw = fetch_rss(url, name, max_n=PER_FEED)
            err = ""
        except Exception as ex:
            raw = []
            err = str(ex)[:160]
        # 新鲜度过滤：丢弃超过 MAX_AGE_DAYS 天的陈旧文章；
        # 抓取时拿不到可靠时间（ts<=0）的条目无法判断是否过期，保守保留。
        entries = [e for e in raw
                   if (e.get("ts") or 0) <= 0 or (e.get("ts") or 0) >= cutoff]
        # 全文抓取：仅对通过新鲜度过滤、会展示的文章抽取正文（存进仓库 + 内联展示），
        # 失败/无正文的条目留空，回退为外链「阅读全文」。
        # fulltext 标志：feeds.json 中 "fulltext": false 的源（多为付费墙，必抽不到全文）
        # 跳过抽取，直接复用 RSS 自带摘要，节省 CI 时间并避免无效请求。
        do_fulltext = f.get("fulltext", True)
        for e in entries:
            if do_fulltext:
                try:
                    e["content"] = fetch_article_text(e.get("url", ""))
                except Exception:
                    e["content"] = ""
            else:
                e["content"] = ""
        # 更新健康状态（garss 式：成功清零，失败累加；达阈值则停用，恢复成功自动启用）
        if raw:
            h["fails"] = 0
            h["disabled"] = False
            h["last_err"] = ""
            h["last_ok"] = now_iso
        else:
            h["fails"] = h.get("fails", 0) + 1
            h["last_err"] = err or "返回为空或解析失败"
            h["last_try"] = now_iso
            if h["fails"] >= MAX_FAILS:
                h["disabled"] = True
        if h["disabled"]:
            disabled.append((name, h))     # 已停用 → 单独提示
        elif not raw:
            unavailable.append(name)       # 真没抓到 → 源可能有问题
        elif not entries:
            no_recent.append(name)         # 抓到了，但都是 N 天前的旧文
        # 保存该源最新 10 篇到仓库（失败也写空，便于识别）
        slug = slugify(name)
        store = {
            "name": name, "url": url, "category": cat,
            "lang": f.get("lang", ""), "entries": entries[:PER_FEED],
        }
        (DATA_DIR / f"{slug}.json").write_text(
            json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
        for e in entries:
            e["_cat"] = cat
            e["_src"] = name
            e["_lang"] = f.get("lang", "")   # 源语言，落到正文 DOM 供浏览器翻译识别
            all_entries.append(e)
    save_health(health)

    # 排序：先按分块顺序（GROUP_ORDER），块内按发布时间倒序
    # （优先用抓取时算好的 ts，缺失时回退解析 date 字符串）
    def _sort_key(e):
        cat = e["_cat"]
        gi = GROUP_ORDER.index(cat) if cat in GROUP_ORDER else len(GROUP_ORDER)
        return (gi, -(e.get("ts") or _date_ts(e.get("date"))))
    all_entries.sort(key=_sort_key)

    # 分类筛选条（按 GROUP_ORDER 排列，未列出的类排在最后）
    seen = []
    for e in all_entries:
        if e["_cat"] not in seen:
            seen.append(e["_cat"])
    seen.sort(key=lambda c: GROUP_ORDER.index(c) if c in GROUP_ORDER else len(GROUP_ORDER))
    cats = seen
    filters_html = '<button class="filter active" data-cat="all">全部</button>'
    for c in cats:
        filters_html += f'<button class="filter" data-cat="{esc(c)}">{esc(c)}</button>'

    # 卡片（按分块顺序，每块首个卡片前插入全宽分块标题）
    cards_html = ""
    last_cat = None
    for e in all_entries:
        cat = e["_cat"]
        color = CAT_COLOR.get(cat, DEFAULT_COLOR)
        if cat != last_cat:
            cnt = sum(1 for x in all_entries if x["_cat"] == cat)
            cards_html += (
                f'<div class="section-head" data-cat="{esc(cat)}">'
                f'<span class="dot" style="background:{color}"></span>'
                f'<h2>{esc(cat)}</h2>'
                f'<span class="cnt">{cnt} 篇</span></div>')
            last_cat = cat
        date = e.get("date", "")
        summ = e.get("summary", "")
        url = e.get("url", "")
        body = e.get("content", "")
        lang = e.get("_lang", "")
        src_slug = slugify(e.get("_src", ""))
        if body:
            lang_attr = f' lang="{esc(lang)}"' if lang else ""
            action = (f'<button class="readmore-btn" type="button" '
                      f'onclick="openReader(this)">📖 阅读全文</button>'
                      + f'<div class="article-body" hidden{lang_attr}>{body}</div>')
        else:
            action = (f'<a class="readmore" href="{esc(url)}" '
                      f'target="_blank" rel="noopener noreferrer">阅读全文 →</a>')
        cards_html += (
            f'<div class="card" data-cat="{esc(cat)}" data-slug="{esc(src_slug)}">'
            f'<div class="card-top">'
            f'<span class="src-pill" style="background:{color}">{esc(e["_src"])}</span>'
            f'<span class="card-date">{esc(date)}</span></div>'
            f'<a class="card-title" href="{esc(url)}" '
            f'target="_blank" rel="noopener noreferrer">{esc(e.get("title",""))}</a>'
            + (f'<div class="card-summary">{esc(summ)}</div>' if summ else "")
            + action
            + f'</div>'
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
    if disabled:
        items = []
        for n, h in disabled:
            items.append(
                f"<strong>{esc(n)}</strong>"
                f"（连续 {h.get('fails', 0)} 次失败：{esc(h.get('last_err', ''))}；"
                f"最近尝试 {esc(h.get('last_try', ''))}）")
        parts.append(
            f"🚫 以下订阅源已连续 {MAX_FAILS} 次抓取失败，已自动停用（不再抓取，"
            f"直到某次恢复成功自动启用）：<br>" + "<br>".join(items) + "。")
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
    # 翻译中转页：真实 https URL、lang=en 的独立页，打开即触发浏览器原生翻译提示
    TRANSLATE_HTML.write_text(TRANSLATE_TEMPLATE, encoding="utf-8")
    print(f"RSS 阅读页已生成：{len(feeds)} 个订阅源，"
          f"{len(all_entries)} 篇近{MAX_AGE_DAYS}天文章，"
          f"{len(unavailable)} 个抓取失败，{len(no_recent)} 个无近{MAX_AGE_DAYS}天新文，"
          f"{len(disabled)} 个已停用 -> {OUT_HTML}")


if __name__ == "__main__":
    main()
