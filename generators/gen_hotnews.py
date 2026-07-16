#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成「综合中文热点」单文件 HTML 仪表盘。

数据源（沙箱可达、无需鉴权）：
  1. 百度热搜     https://top.baidu.com/api/board?platform=wise&tab=realtime
  2. 微博热搜     https://60s-api.viki.moe/v2/weibo   (60s 公共代理)
  3. 今日头条热榜  https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc
  4. B站热门      https://api.bilibili.com/x/web-interface/popular?ps=20
  5. 国际热点     Google News 英文头条(RSS)，失败回退 Hacker News；标题服务端翻译成中文
  6. 官方新闻     中国新闻网即时新闻 RSS，含正文摘要；卡片可点击「展开详情」查看全文+原文链接

设计：
  - 每个平台独立成区，区内从 1 开始排名（与 arXiv 页一致）。
  - 网格列数固定 桌面3 / 平板2 / 手机1；每平台取 3 的倍数篇数，末行无孤卡。
  - 国际热点区为双语：中文翻译(主) + 英文原文(副)，生成时服务端翻译烤进 HTML。
  - 亮色默认 + 明暗切换（与现有仪表盘一致）。
  - 纯内联 CSS/JS，无外部资源。所有外链 target=_blank rel=noopener。
  - 抓取失败的单平台跳过并提示，其余正常渲染。

输出：
  auto-web/hotnews/index.html

用法：
  python3 gen_hotnews.py          # 生成到默认 OUTPUT_DIR
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from archive_util import write_archive
from nav_util import NAV_CSS, build_nav, FOOTER_CSS, build_footer

# 基于脚本位置推导仓库根（generators/ 的上一级），CI 与本地通用
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOT_OUTPUT_DIR = os.path.join(_BASE_DIR, "hotnews")
NEWS_OUTPUT_DIR = os.path.join(_BASE_DIR, "news")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TIMEOUT = 15

CST = ZoneInfo("Asia/Shanghai")

# 每平台取 3 的倍数，保证网格末行满
PER_PLATFORM = {
    "baidu": 30,
    "weibo": 30,
    "toutiao": 30,
    "bili": 15,
    "intl": 30,
    # 新闻页各源（卡片含摘要，单源条数适度收敛）
    "chinanews": 18, "repubblica": 18, "france24": 18, "sole24": 18,
    "politico": 18, "eu_google": 18, "bbc": 18, "nhk": 18,
}


def fetch_json(url, ua=UA, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fetch_xml(url, ua=UA, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return ET.fromstring(r.read())


def translate_texts(texts, sl="en", tl="zh-CN", max_workers=10):
    """并发调用 Google 翻译非官方端点，返回与输入等长的翻译列表（失败填空）。"""
    def one(t):
        if not t:
            return ""
        try:
            q = urllib.parse.quote(t)
            url = (f"https://translate.googleapis.com/translate_a/single"
                   f"?client=gtx&sl={sl}&tl={tl}&dt=t&q={q}")
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            return "".join(seg[0] for seg in d[0] if seg and seg[0])
        except Exception:
            return ""
    if not texts:
        return []
    out = [""] * len(texts)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(one, t): i for i, t in enumerate(texts)}
        for f in as_completed(futs):
            out[futs[f]] = f.result()
    return out


def fmt_hot(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n >= 1_0000_0000:
        return f"{n/1_0000_0000:.1f}亿"
    if n >= 1_0000:
        return f"{n/1_0000:.1f}万"
    return str(n)


# ---------- 各平台解析器：返回 [{title, url, hot, extra}] ----------
def parse_baidu(d):
    out = []
    try:
        entries = d["data"]["cards"][0]["content"][0]["content"]
    except (KeyError, IndexError, TypeError):
        return out
    for e in entries:
        word = (e.get("word") or "").strip()
        url = e.get("url") or ""
        if not word:
            continue
        out.append({"title": word, "url": url, "hot": "", "extra": "🔝" if e.get("isTop") else ""})
    return out


def parse_weibo(d):
    out = []
    for e in (d.get("data") or []):
        title = (e.get("title") or "").strip()
        link = e.get("link") or ""
        if not title:
            continue
        out.append({"title": title, "url": link, "hot": fmt_hot(e.get("hot_value")), "extra": ""})
    return out


def parse_toutiao(d):
    out = []
    for e in (d.get("data") or []):
        title = (e.get("Title") or "").strip()
        url = e.get("Url") or ""
        if not title:
            continue
        out.append({"title": title, "url": url, "hot": fmt_hot(e.get("HotValue")), "extra": (e.get("Label") or "")})
    return out


def parse_bili(d):
    out = []
    for e in ((d.get("data") or {}).get("list") or []):
        title = (e.get("title") or "").strip()
        bvid = e.get("bvid") or ""
        url = f"https://www.bilibili.com/video/{bvid}" if bvid else (e.get("short_link_v2") or "")
        owner = (e.get("owner") or {}).get("name", "")
        stat = e.get("stat") or {}
        views = fmt_hot(stat.get("view"))
        extra = f"@{owner}" if owner else ""
        if not title:
            continue
        out.append({"title": title, "url": url, "hot": (f"{views}播放" if views else ""), "extra": extra})
    return out


def parse_google_news(root):
    out = []
    chan = root.find("channel")
    if chan is None:
        return out
    for item in chan.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = item.findtext("link") or ""
        if not title:
            continue
        out.append({"title": title, "url": link, "hot": "", "extra": ""})
    return out


def parse_hn(d):
    out = []
    for h in (d.get("hits") or []):
        title = (h.get("title") or "").strip()
        url = h.get("url") or h.get("story_url") or ""
        if not title:
            continue
        out.append({"title": title, "url": url, "hot": fmt_hot(h.get("points")), "extra": ""})
    return out


_TAG_RE = re.compile(r"<[^>]+>")
def clean_html(html):
    """去掉 HTML 标签、反转义实体、压缩空白，得到纯文本摘要。"""
    if not html:
        return ""
    txt = _TAG_RE.sub(" ", html)
    txt = (txt.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
              .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " ")
              .replace("&#8230;", "…").replace("&#39;", "'"))
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def fmt_pubdate(s):
    """RFC822 发布时间 -> 北京 'MM-DD HH:MM'（失败返回空）。"""
    if not s:
        return ""
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(CST)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return ""


def freshen_news(items, max_age_days=7, now=None):
    """新闻时效性保护：按发布时间倒序，丢弃超过 max_age_days 天的陈旧条目。

    设计要点：
      - 仅用于新闻页（新闻按时间排序才有意义；热榜按热度排序，不可乱序）。
      - 无法解析发布时间的条目保守保留（无法判断是否过期，宁留勿删）。
      - 排序后最新鲜的内容自然排到各区头条，避免 RSS 顺序错乱导致的旧闻置顶。
    """
    if now is None:
        now = datetime.now(timezone.utc)

    def _dt(it):
        s = it.get("published", "")
        if not s:
            return None
        try:
            d = parsedate_to_datetime(s)
        except Exception:
            return None
        if d is None:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d

    kept, dropped = [], 0
    for it in items:
        d = _dt(it)
        if d is not None and (now - d).days > max_age_days:
            dropped += 1
            continue
        kept.append(it)
    kept.sort(key=lambda x: (_dt(x) or datetime.min.replace(tzinfo=timezone.utc)),
              reverse=True)
    if dropped:
        print(f"    [时效] 丢弃 {dropped} 条超过 {max_age_days} 天的陈旧内容")
    return kept



def parse_chinanews(root):
    """中新网 RSS：返回 [{title, url, summary, published}]，summary 为纯文本摘要。"""
    out = []
    for item in root.iter("item"):
        title = clean_html(item.findtext("title") or "").strip()
        link = item.findtext("link") or ""
        desc = item.findtext("description") or ""
        pub = item.findtext("pubDate") or ""
        if not title:
            continue
        summary = clean_html(desc)[:300]
        out.append({"title": title, "url": link, "summary": summary, "published": pub})
    return out


def fetch_chinanews():
    """官方新闻：中新网即时新闻 RSS（沙箱可达、含正文摘要）。返回 (items, note)。"""
    note = ""
    try:
        root = fetch_xml("https://www.chinanews.com.cn/rss/scroll-news.xml")
        items = parse_chinanews(root)
        if items:
            return items, note
        note = "中新网返回空"
    except Exception as e:
        note = f"中新网失败({type(e).__name__})"
    return [], note


def fetch_rss_news(url, name):
    """通用新闻 RSS 加载器：返回 (items, note)。items 结构与 parse_chinanews 一致。"""
    note = ""
    try:
        root = fetch_xml(url)
        items = parse_chinanews(root)
        if items:
            return items, note
        note = f"{name} 返回空"
    except Exception as e:
        note = f"{name}失败({type(e).__name__})"
    return [], note


# ---- 新闻页：意大利 / 欧盟 / 国际 等多源 RSS（均为探测可达的稳定源）----
def fetch_repubblica():
    return fetch_rss_news("https://www.repubblica.it/rss/homepage/rss2.0.xml", "共和报")

def fetch_france24():
    return fetch_rss_news("https://www.france24.com/en/rss", "France24 国际台")

def fetch_sole24():
    return fetch_rss_news("https://www.ilsole24ore.com/rss/italia.xml", "24小时太阳报")

def fetch_politico():
    return fetch_rss_news("https://www.politico.eu/feed/", "Politico Europe")

def fetch_eu_google():
    return fetch_rss_news("https://news.google.com/rss/search?q=European%20Union&hl=en-US&gl=US&ceid=US:en", "欧盟·Google News")

def fetch_bbc():
    return fetch_rss_news("https://feeds.bbci.co.uk/news/rss.xml", "BBC News")

def fetch_nhk():
    return fetch_rss_news("https://www3.nhk.or.jp/rss/news/cat0.xml", "NHK 日本")


def fetch_intl():
    """国际热点：Google News 英文头条为主，失败回退 Hacker News。返回 (items, note)。"""
    note = ""
    # 主源：Google News Top Stories (EN)
    try:
        root = fetch_xml("https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en")
        items = parse_google_news(root)
        if items:
            return items, note
        note = "GoogleNews 返回空"
    except Exception as e:
        note = f"GoogleNews 失败({type(e).__name__})"
    # 回退：Hacker News
    try:
        d = fetch_json("https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=40")
        items = parse_hn(d)
        if items:
            return items, note + "，已回退 Hacker News"
    except Exception as e:
        note += f"；HN 也失败({type(e).__name__})"
    return [], note


# 综合热点页：国内热榜 + 国际热点（不含新闻，新闻独立成页）
HOT_SOURCES = [
    {"id": "baidu", "name": "百度热搜", "emoji": "🔍", "anchor": "baidu",
     "url": "https://top.baidu.com/api/board?platform=wise&tab=realtime", "parser": parse_baidu},
    {"id": "weibo", "name": "微博热搜", "emoji": "🔥", "anchor": "weibo",
     "url": "https://60s-api.viki.moe/v2/weibo", "parser": parse_weibo},
    {"id": "toutiao", "name": "今日头条热榜", "emoji": "📰", "anchor": "toutiao",
     "url": "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc", "parser": parse_toutiao},
    {"id": "bili", "name": "B站热门", "emoji": "📺", "anchor": "bili",
     "url": "https://api.bilibili.com/x/web-interface/popular?ps=20", "parser": parse_bili},
    {"id": "intl", "name": "国际热点", "emoji": "🌍", "anchor": "intl",
     "loader": fetch_intl, "bilingual": True},
]

# 新闻页：中新网 + 意大利 + 欧盟 + 国际（标题翻译为中文，含摘要可展开）
NEWS_SOURCES = [
    {"id": "chinanews", "name": "中国新闻网", "emoji": "🇨🇳", "anchor": "chinanews",
     "loader": fetch_chinanews, "expandable": True, "bilingual": True, "sl": "zh-CN"},
    {"id": "repubblica", "name": "共和报 (意大利)", "emoji": "🇮🇹", "anchor": "repubblica",
     "loader": fetch_repubblica, "expandable": True, "bilingual": True, "sl": "auto"},
    {"id": "france24", "name": "France24 国际台", "emoji": "🇫🇷", "anchor": "france24",
     "loader": fetch_france24, "expandable": True, "bilingual": True, "sl": "auto"},
    {"id": "sole24", "name": "24小时太阳报 (意大利)", "emoji": "🇮🇹", "anchor": "sole24",
     "loader": fetch_sole24, "expandable": True, "bilingual": True, "sl": "auto"},
    {"id": "politico", "name": "Politico Europe", "emoji": "🇪🇺", "anchor": "politico",
     "loader": fetch_politico, "expandable": True, "bilingual": True, "sl": "auto"},
    {"id": "eu_google", "name": "欧盟 · Google News", "emoji": "🇪🇺", "anchor": "eu_google",
     "loader": fetch_eu_google, "expandable": True, "bilingual": True, "sl": "auto"},
    {"id": "bbc", "name": "BBC News", "emoji": "🇬🇧", "anchor": "bbc",
     "loader": fetch_bbc, "expandable": True, "bilingual": True, "sl": "auto"},
    {"id": "nhk", "name": "NHK 日本", "emoji": "🇯🇵", "anchor": "nhk",
     "loader": fetch_nhk, "expandable": True, "bilingual": True, "sl": "en"},
]


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def build_html(results, now, brand, hero_title, extra_nav=""):
    total = 0
    nav_items = []
    sections = []
    for src, items, ok, err in results:
        items = items[: PER_PLATFORM.get(src["id"], 30)]
        total += len(items)
        nav_items.append((src["emoji"], src["name"], src["anchor"], len(items)))
        cards = []
        bilingual = bool(src.get("bilingual"))
        for i, it in enumerate(items, 1):
            if src.get("expandable"):
                pub = fmt_pubdate(it.get("published", ""))
                full = it.get("summary", "")
                short = (full[:52] + "…") if len(full) > 52 else full
                zh = it.get("zh")
                card = (
                    f'<div class="card expandable">'
                    f'<span class="rank">{i:02d}</span>'
                )
                if zh:
                    card += (f'<a class="card-title zh" href="{esc(it["url"])}" target="_blank" rel="noopener noreferrer">{esc(zh)}</a>'
                             f'<span class="card-sub">{esc(it["title"])}</span>')
                else:
                    card += f'<a class="card-title" href="{esc(it["url"])}" target="_blank" rel="noopener noreferrer">{esc(it["title"])}</a>'
                if pub:
                    card += f'<span class="card-sub2">{esc(pub)}</span>'
                if short:
                    card += f'<p class="card-summary">{esc(short)}</p>'
                card += (
                    f'<button class="toggle" onclick="toggleCard(this)">展开详情 ▾</button>'
                    f'<div class="card-detail" style="display:none">'
                    f'<p>{esc(full)}</p>'
                    f'<a class="readmore" href="{esc(it["url"])}" target="_blank" rel="noopener noreferrer">阅读全文 →</a>'
                    f'</div></div>'
                )
                cards.append(card)
                continue
            hot = f'<span class="hot">{it["hot"]}</span>' if it["hot"] else ""
            extra = f'<span class="extra">{it["extra"]}</span>' if it["extra"] else ""
            if bilingual and it.get("zh"):
                title_html = (f'<span class="card-title zh">{esc(it["zh"])}</span>'
                              f'<span class="card-sub">{esc(it["title"])}</span>')
            else:
                title_html = f'<span class="card-title">{esc(it["title"])}</span>'
            cards.append(
                f'<a class="card" href="{it["url"]}" target="_blank" rel="noopener noreferrer">'
                f'<span class="rank">{i:02d}</span>{title_html}'
                f'<span class="card-meta">{hot}{extra}</span></a>'
            )
        if ok:
            sec = (
                f'<section id="{src["anchor"]}">'
                f'<h2><span class="sec-emoji">{src["emoji"]}</span>{esc(src["name"])}'
                f'<span class="count">共 <strong>{len(items)}</strong> 条</span></h2>'
                f'<div class="grid">{"".join(cards)}</div></section>'
            )
        else:
            sec = (
                f'<section id="{src["anchor"]}">'
                f'<h2><span class="sec-emoji">{src["emoji"]}</span>{esc(src["name"])}'
                f'<span class="count">抓取失败</span></h2>'
                f'<div class="notice">⚠️ 本次抓取失败：{esc(err)}</div></section>'
            )
        sections.append(sec)

    nav_links = extra_nav + "".join(
        f'<a class="nav-link" href="#{a}"><span>{e}</span>{n}<span class="nav-count">{c}</span></a>'
        for e, n, a, c in nav_items
    )
    nav_html = build_nav(brand, nav_links)
    sec_html = "".join(sections)
    date_str = now.strftime("%Y-%m-%d %H:%M")
    total_ok = sum(1 for _, _, ok, _ in results if ok)

    return TEMPLATE.replace("__NAV_CSS__", NAV_CSS).replace("__NAV_HTML__", nav_html) \
        .replace("__DATE__", date_str).replace("__TOTAL__", str(total)) \
        .replace("__SECTIONS__", sec_html) \
        .replace("__SOURCES_OK__", f"{total_ok}/{len(results)}") \
        .replace("__HERO_TITLE__", hero_title) \
        .replace("__FOOTER_CSS__", FOOTER_CSS) \
        .replace("__FOOTER__", build_footer())


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>综合中文热点 · 实时聚合</title>
<script>
(function(){try{var t=localStorage.getItem('theme');if(t)document.documentElement.setAttribute('data-theme',t);}catch(e){}})();
</script>
<style>
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
.hero{padding:34px 22px 14px;text-align:center}
.hero h1{font-size:clamp(22px,6vw,30px);letter-spacing:.5px}
.hero p{color:var(--muted);margin-top:8px;font-size:14px}
.wrap{max-width:1080px;margin:0 auto;padding:10px 18px 60px}
section{padding:24px 0 6px;scroll-margin-top:70px}
h2{font-size:20px;display:flex;align-items:center;gap:10px;margin-bottom:14px;
  padding-bottom:8px;border-bottom:1px solid var(--line)}
.sec-emoji{font-size:22px}
.count{margin-left:auto;font-size:13px;color:var(--muted);font-weight:500}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:13px;align-items:start}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}}
/* nav 响应式规则（768px 汉堡等）已在 NAV_CSS 中统一定义 */
@media(max-width:560px){
  .grid{grid-template-columns:1fr}
  .hero h1{font-size:clamp(20px,7vw,26px)}
  .card{padding:14px 14px 14px 44px}
  .card-title{font-size:16px}
  .card-sub,.card-sub2,.card-meta{font-size:13px}
  .card-summary{font-size:14px}
  .sec-emoji{font-size:20px}
  h2{font-size:18px}
}
.card{position:relative;display:flex;flex-direction:column;gap:5px;text-decoration:none;
  background:var(--card);border:1px solid var(--line);border-radius:13px;padding:13px 14px 13px 46px;
  box-shadow:var(--shadow);transition:transform .12s,border-color .12s,background .25s;min-height:62px;min-width:0;overflow-wrap:anywhere}
.card:hover{transform:translateY(-2px);border-color:var(--accent2)}
.rank{position:absolute;left:12px;top:13px;width:26px;height:26px;border-radius:8px;
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:13px;
  background:var(--pill-bg);color:var(--accent)}
.card-title{font-size:15px;font-weight:600;color:var(--text);text-decoration:none;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.card-title.zh{color:var(--accent2)}
.card-title:hover{color:var(--accent2)}
.card-sub{font-size:12px;color:var(--muted);
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.card-sub2{font-size:12px;color:var(--muted)}
.card-summary{font-size:13px;color:var(--muted);margin-top:2px;line-height:1.5;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere}
.card.expandable{min-height:auto;padding-bottom:14px}
.card-detail{padding-top:9px;margin-top:5px;border-top:1px dashed var(--line);
  font-size:13.5px;line-height:1.7;color:var(--text)}
.card-detail p{margin:0 0 8px;overflow-wrap:anywhere}
.toggle{margin-top:8px;align-self:flex-start;cursor:pointer;border:1px solid var(--line);
  background:var(--pill-bg);color:var(--accent2);padding:8px 14px;border-radius:8px;font-size:13px;min-height:38px}
.toggle:hover{border-color:var(--accent2)}
.readmore{display:inline-block;color:var(--accent2);font-weight:600;text-decoration:none;font-size:13px}
.readmore:hover{text-decoration:underline}
.card-meta{display:flex;gap:10px;flex-wrap:wrap;font-size:12px;color:var(--muted)}
.hot{color:var(--accent);font-weight:600}
.extra{color:var(--accent2)}
.notice{padding:14px 16px;border-radius:10px;background:var(--pill-bg);color:var(--muted);font-size:14px}
.foot{text-align:center;color:var(--muted);font-size:12.5px;padding:26px 12px 10px}
</style>
</head>
<body>
__NAV_HTML__
<header class="hero">
  <h1>__HERO_TITLE__</h1>
  <p>聚合 <strong>__SOURCES_OK__</strong> 个平台 · 共 <strong>__TOTAL__</strong> 条 · 抓取于 __DATE__（北京时间）</p>
</header>
<div class="wrap">
__SECTIONS__
</div>
__FOOTER__
<footer class="foot">数据来自各平台公开热榜 / 新闻接口，国际热点与新闻标题已同步翻译为中文 · 点击卡片展开详情或跳转原文</footer>
<script>
window.toggleCard=function(btn){
  var d=btn.nextElementSibling;
  if(!d)return;
  if(d.style.display==='none'){d.style.display='block';btn.textContent='收起 ▴';}
  else{d.style.display='none';btn.textContent='展开详情 ▾';}
};
(function(){
  var h=document.getElementById('nav-hamburger');
  var nav=document.querySelector('.nav');
  if(!h||!nav)return;
  function set(open){nav.classList.toggle('menu-open',open);h.textContent=open?'✕':'☰';h.setAttribute('aria-expanded',open?'true':'false');}
  h.onclick=function(){set(!nav.classList.contains('menu-open'));};
  Array.prototype.forEach.call(nav.querySelectorAll('.nav-link'),function(a){a.addEventListener('click',function(){set(false);});});
  document.addEventListener('click',function(e){if(nav.classList.contains('menu-open')&&!nav.contains(e.target))set(false);});
})();
(function(){
  var b=document.getElementById('theme-toggle');
  function upd(){var t=document.documentElement.getAttribute('data-theme');
    b.textContent=t==='dark'?'☀️ 亮色':'🌙 暗色';}
  upd();
  b.onclick=function(){var t=document.documentElement.getAttribute('data-theme');
    var n=t==='dark'?'light':'dark';
    document.documentElement.setAttribute('data-theme',n);
    try{localStorage.setItem('theme',n);}catch(e){} upd();};
})();
</script>
</body>
</html>
"""


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "hot"
    if mode == "news":
        SOURCES = NEWS_SOURCES
        OUTPUT_DIR = NEWS_OUTPUT_DIR
        brand = "📰 全球新闻"
        hero_title = "全球新闻 · 多源聚合"
        extra_nav = '<a class="nav-link" href="../hotnews/index.html">🔥 综合热点</a><a class="nav-link" href="../rss/index.html">📡 RSS</a>'
        dated_prefix = "news_"
        archive_title = "全球新闻"
        archive_desc = "按生成日期排列的历史副本，每日自动追加。"
    else:
        SOURCES = HOT_SOURCES
        OUTPUT_DIR = HOT_OUTPUT_DIR
        brand = "🔥 综合中文热点"
        hero_title = "综合中文热点 · 实时聚合"
        extra_nav = '<a class="nav-link" href="../news/index.html">📰 新闻</a><a class="nav-link" href="../rss/index.html">📡 RSS</a>'
        dated_prefix = "hotnews_"
        archive_title = "综合中文热点"
        archive_desc = "按生成日期排列的历史副本，每日自动追加。"

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    now = datetime.now(CST)
    results = []
    for src in SOURCES:
        ok, err = True, ""
        items = []
        try:
            if "loader" in src:
                items, note = src["loader"]()
                if note:
                    err = note
                if not items:
                    ok, err = False, (err or "无数据")
            # 新闻时效性保护：仅新闻页按发布时间倒序 + 丢弃 >7 天旧闻
            if mode == "news" and items:
                items = freshen_news(items, max_age_days=7)
            else:
                data = fetch_json(src["url"])
                items = src["parser"](data)
                if not items:
                    ok, err = False, "接口返回空列表（可能结构变化或暂时无数据）"
            # 双语源：服务端同步翻译标题（sl 默认 en，新闻页用 auto / 中文源用 zh-CN）
            if src.get("bilingual") and items:
                zhs = translate_texts([it["title"] for it in items], sl=src.get("sl", "en"))
                for it, z in zip(items, zhs):
                    it["zh"] = z
        except Exception as e:
            ok, err = False, f"{type(e).__name__}: {e}"
        results.append((src, items, ok, err))
        zh_n = sum(1 for it in items if it.get("zh")) if src.get("bilingual") else None
        extra = f"  翻译 {zh_n}/{len(items)}" if zh_n is not None else ""
        print(f"  {src['name']}: {'OK' if ok else 'FAIL'}  {len(items)} 条{extra}" + ("" if ok else f"  ({err})"))

    html = build_html(results, now, brand, hero_title, extra_nav)
    out = os.path.join(OUTPUT_DIR, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    total = sum(len(it) for _, it, _, _ in results)
    ok_n = sum(1 for _, _, ok, _ in results if ok)
    print(f"[{mode}] feeds_ok={ok_n}/{len(results)} total_items={total}")
    print("wrote:", out)

    # 保留每日 dated 副本，供「历史归档」索引列出
    date_str = now.strftime("%Y-%m-%d")
    dated_out = os.path.join(OUTPUT_DIR, f"{dated_prefix}{date_str}.html")
    with open(dated_out, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote dated:", dated_out)

    n = write_archive(OUTPUT_DIR, dated_prefix, archive_title, archive_desc, "index.html")
    print(f"archive index: {os.path.join(OUTPUT_DIR, 'archive.html')} ({n} 份)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
