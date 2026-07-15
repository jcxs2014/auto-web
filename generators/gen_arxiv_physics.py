#!/usr/bin/env python3
"""
arXiv 物理最新论文 HTML 仪表盘生成器（自包含、可被定时任务调用）。

领域（6 主题 / 8 arXiv 类别）：
  高能物理      : hep-th, hep-ph, hep-ex
  空间物理      : physics.space-ph
  广义相对论    : gr-qc
  高能天体物理  : astro-ph.HE
  探测器与仪器  : physics.ins-det

流程：
  1. 按主题分别调 https://export.arxiv.org/api/query （sortBy=submittedDate desc, max_results=PER_THEME）
     主题间 sleep 3 秒，遵守 arXiv 1 请求/3 秒限流。
  2. 解析 Atom XML，提取 标题/摘要/作者/主分类/published/abs链接/pdf链接。
  3. 按主题固定顺序分组；每主题内部从 1 单独编号（非全局连续）；时间转北京时间人话（相对 + 绝对）。
  4. 生成单文件 HTML（内联 CSS/JS + MathJax CDN 渲染 LaTeX，外链带 target=_blank rel=noopener noreferrer）。
  5. 输出到 /Users/jcxs2014/Sites/Workbuddy/arxiv-physics/：arxiv_physics_latest.html（稳定书签）+ arxiv_physics_YYYY-MM-DD.html（dated 归档）。
"""
import html
import json
import sys
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from archive_util import write_archive
from nav_util import NAV_CSS, build_nav, FOOTER_CSS, build_footer
from sources_util import fetch_rss, fetch_prl_crossref, dedup

BASE = "https://export.arxiv.org/api/query"

# 生成网页统一存放目录：按类型分子文件夹
# 基于脚本位置推导仓库根（generators/ 的上一级），CI 与本地通用
OUTPUT_BASE = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
BJ = timezone(timedelta(hours=8))

# 5 themes in fixed display order: (主题名, [arXiv categories], emoji, anchor)
THEMES = [
    ("高能物理",       ["hep-th", "hep-ph", "hep-ex"],          "⚛️", "hep"),
    ("空间物理",       ["physics.space-ph"],                    "🛰️", "space"),
    ("广义相对论",     ["gr-qc"],                               "🕳️", "grqc"),
    ("高能天体物理",   ["astro-ph.HE"],                         "🌌", "astrohe"),
    ("探测器与仪器",   ["physics.ins-det"],                     "🔬", "detector"),
]
PER_THEME = 12  # 每主题最多取多少篇（务必为 3 的倍数：列数固定为桌面3/平板2/手机1，保证各行都满，无孤卡）

# 中文翻译（Google 非官方 client=gtx 接口，无需 key）。失败优雅降级为只显示英文。
TRANSLATE_ZH = True
TRANSLATE_SLEEP = 0.5  # 翻译调用间隔（秒），降低被限流风险
TRANSLATE_ENDPOINT = "https://translate.googleapis.com/translate_a/single"

NS = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "os": "http://a9.com/-/spec/opensearch/1.1/",
}

# MathJax v3（SVG 输出，无字体加载问题）。非 f-string，花括号为字面量。
# 需联网加载；离线时数学公式以 LaTeX 原文显示（仍可读）。
MATHJAX_SCRIPTS = """<script>
  MathJax = { tex: { inlineMath: [['$','$'],['\\\\(','\\\\)']], displayMath: [['$$','$$'],['\\\\[','\\\\]']], ignoreHtmlClass: "no-math" }, svg: { fontCache: 'global' }, options: { skipHtmlTags: ['script','noscript','style','textarea','pre','code'] } };
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js" id="MathJax-script" async></script>
"""

# 中文显示开关：记忆用户偏好（localStorage），点击切换 .zh-hidden。
TOGGLE_SCRIPT = """<script>
  (function(){
    try {
      var b = document.getElementById('zh-toggle');
      if (localStorage.getItem('arxiv-zh') === 'off') { document.body.classList.add('zh-hidden'); if(b) b.classList.add('off'); }
      if (b) b.addEventListener('click', function(){
        document.body.classList.toggle('zh-hidden');
        localStorage.setItem('arxiv-zh', document.body.classList.contains('zh-hidden') ? 'off' : 'on');
        b.classList.toggle('off');
      });
    } catch(e) {}
  })();

  // 摘要展开/收起：点短摘要或按钮均可切换，更新按钮文案与 aria
  function toggleCard(el) {{
    try {{
      var card = el.closest('.card');
      if (!card) return;
      var on = card.classList.toggle('expanded');
      var btn = card.querySelector('.expand-btn');
      if (btn) {{
        btn.textContent = on ? '收起 ▴' : '展开全文 ▾';
        btn.setAttribute('aria-expanded', on ? 'true' : 'false');
      }}
      var hint = card.querySelector('.more-hint');
      if (hint) hint.style.display = on ? 'none' : '';
    }} catch(e) {{}}
  }}
</script>
"""


def clean(s: str) -> str:
    return " ".join((s or "").split())


def translate_to_zh(text: str) -> str:
    """Google 非官方翻译接口（client=gtx，无需 key）。失败返回空串。"""
    if not text:
        return ""
    try:
        url = TRANSLATE_ENDPOINT + "?client=gtx&sl=en&tl=zh-CN&dt=t&q=" + urllib.parse.quote(text)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return "".join(seg[0] for seg in data[0] if seg and seg[0])
    except Exception as e:
        print(f"[arxiv] WARN 翻译失败（{len(text)} 字符）: {e}", file=sys.stderr)
        return ""


def translate_paper(title: str, summary_short: str) -> tuple[str, str]:
    """一次调用翻译 标题+摘要（用 \\n\\n 拼接，译文保留分隔），返回 (zh_title, zh_summary)。
    失败或分隔符被吞时对应字段返回空串。"""
    combined = f"{title}\n\n{summary_short}"
    zh = translate_to_zh(combined)
    if not zh:
        return "", ""
    if "\n\n" in zh:
        a, b = zh.split("\n\n", 1)
        return a.strip(), b.strip()
    return zh.strip(), ""  # 分隔符被吞，整段当标题译文


def fetch_theme(theme_cats: list[str]) -> list[dict]:
    """Query arXiv for one theme's categories, return list of paper dicts."""
    ors = " OR ".join(f"cat:{c}" for c in theme_cats)
    params = {
        "search_query": ors,
        "start": "0",
        "max_results": str(PER_THEME),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/atom+xml"})
    papers = []
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_bytes = resp.read()
        root = ET.fromstring(xml_bytes)
        for entry in root.findall("a:entry", NS):
            arxiv_id_raw = clean(entry.findtext("a:id", "", NS))  # http://arxiv.org/abs/XXXXvN
            arxiv_id = arxiv_id_raw.split("/abs/")[-1]
            title = clean(entry.findtext("a:title", "", NS))
            summary = clean(entry.findtext("a:summary", "", NS))
            published = clean(entry.findtext("a:published", "", NS))
            updated = clean(entry.findtext("a:updated", "", NS))
            authors = [clean(a.text or "") for a in entry.findall("a:author/a:name", NS)]
            pc = entry.find("arxiv:primary_category", NS)
            primary_cat = pc.get("term", "") if pc is not None else ""
            # links
            abs_url = arxiv_id_raw
            pdf_url = ""
            for ln in entry.findall("a:link", NS):
                rel = ln.get("rel", "")
                if rel == "alternate":
                    abs_url = ln.get("href", abs_url)
                elif rel == "related" and ln.get("type", "") == "application/pdf":
                    pdf_url = ln.get("href", "")
            if not pdf_url:
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
            papers.append({
                "arxiv_id": arxiv_id,
                "title": title,
                "summary": summary,
                "published": published,
                "updated": updated,
                "authors": authors,
                "primary_cat": primary_cat,
                "abs_url": abs_url,
                "pdf_url": pdf_url,
            })
    except Exception as e:
        print(f"[arxiv] WARN 主题 {theme_cats} 拉取失败: {e}", file=sys.stderr)
    return papers


def cn_truncate(s: str, n: int = 400) -> str:
    """截断到 n 字符；若切断导致 $ 配对失衡（奇数个），丢弃尾部未闭合的数学片段，
    避免 MathJax 看到未闭合定界符。"""
    if len(s) <= n:
        return s
    cut = s[:n]
    if cut.count("$") % 2 == 1:
        last_dollar = cut.rfind("$")
        if last_dollar > 0:
            cut = cut[:last_dollar]
    return cut.rstrip() + "…"


def relative_time(iso: str, now: datetime) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    delta = now - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return "刚刚"
    if secs < 3600:
        return f"{secs // 60} 分钟前"
    if secs < 86400:
        return f"{secs // 3600} 小时前"
    if secs < 2 * 86400:
        return "昨天"
    return f"{secs // 86400} 天前"


def bj_human(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(BJ)
    return f"{dt.month}月{dt.day}日 {dt.strftime('%H:%M')}（北京时间）"


def weekday_cn(d: datetime) -> str:
    return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][d.weekday()]


def render_journal_card(it: dict, seq: int) -> str:
    """渲染前沿期刊条目（Nature Physics / Science / PRL）为卡片，风格与 arXiv 论文卡一致（含中文译文）。"""
    title = html.escape(it.get("title", ""))
    source = html.escape(it.get("source", "期刊"))
    url = html.escape(it.get("url", "#"))
    extra = it.get("extra")
    extra_html = f'<span class="journal-tag">{html.escape(extra)}</span>' if extra else ""
    summary_full = html.escape(it.get("summary", ""))
    summary_short = html.escape(cn_truncate(it.get("summary", ""), 300))
    zh_title = it.get("zh_title", "")
    zh_summary = it.get("zh_summary", "")
    zh_title_html = f'<p class="card-title-zh no-math">🌐 {html.escape(zh_title)}</p>' if zh_title else ""
    zh_summary_html = f'<p class="card-summary-zh no-math">{html.escape(zh_summary)}</p>' if zh_summary else ""
    date = it.get("date")
    date_html = f'<span class="journal-date">{html.escape(date)}</span>' if date else ""
    has_summary = bool(summary_full)
    summary_html = ""
    if has_summary:
        summary_html = f"""
          <p class="card-summary-short no-math" onclick="toggleCard(this)">{summary_short} <span class="more-hint">…展开全文 ▾</span></p>
          <div class="card-summary-full">
            <p class="card-summary-en">{summary_full}</p>
            {zh_summary_html}
          </div>
          <button class="expand-btn" type="button" aria-expanded="false" onclick="toggleCard(this)">展开全文 ▾</button>"""
    return f"""
        <article class="card">
          <div class="card-head">
            <span class="seq">{seq:02d}</span>
            <span class="source-chip" title="{source}">{source}</span>
            {date_html}
          </div>
          <h3 class="card-title">{title}</h3>
          {zh_title_html}
          {summary_html}
          <div class="card-foot">
            <a class="btn-primary" href="{url}" target="_blank" rel="noopener noreferrer">阅读原文 ↗</a>
            {extra_html}
          </div>
        </article>"""


def build_html(theme_papers: list[tuple[str, list[dict]]], jitems: list[dict], now: datetime) -> tuple[str, int, dict]:
    """Return (html_string, total, theme_counts). theme_papers: [(theme_name, [papers]), ...]"""
    # 每个主题内部从 1 单独编号（非全局连续）
    total = 0
    theme_counts = {}
    grouped = []  # (theme_name, emoji, anchor, [(tseq, paper), ...])
    for theme_name, papers, emoji, anchor in theme_papers:
        theme_counts[theme_name] = len(papers)
        total += len(papers)
        indexed = []
        for tseq, p in enumerate(papers, start=1):
            indexed.append((tseq, p))
        grouped.append((theme_name, emoji, anchor, indexed))

    # Hero stat chips
    stat_chips = "".join(
        f'<span class="chip-stat"><span class="chip-emoji">{emoji}</span><span class="chip-label">{name}</span><span class="chip-num">{theme_counts[name]}</span></span>'
        for name, _, emoji, _ in [(t[0], None, t[2], None) for t in THEMES]
    )
    # Nav
    nav_items = "".join(
        f'<a href="#{anchor}" class="nav-link"><span class="nav-emoji">{emoji}</span>{name}<span class="nav-count">{theme_counts[name]}</span></a>'
        for name, _, emoji, anchor in THEMES
    )
    nav_items += '<a class="nav-link" href="../rss/index.html">📡 RSS</a>'

    # Sections
    sections_html = []
    for name, emoji, anchor, indexed in grouped:
        cards = []
        for tseq, p in indexed:
            title = html.escape(p["title"])
            summary_full = html.escape(p["summary"])
            summary_short = html.escape(cn_truncate(p["summary"], 400))
            zh_title = p.get("zh_title", "")
            zh_summary = p.get("zh_summary", "")
            zh_title_html = f'<p class="card-title-zh no-math">🌐 {html.escape(zh_title)}</p>' if zh_title else ""
            zh_summary_html = f'<p class="card-summary-zh no-math">{html.escape(zh_summary)}</p>' if zh_summary else ""
            cat = html.escape(p["primary_cat"])
            arxiv_id = html.escape(p["arxiv_id"])
            authors = p["authors"]
            if not authors:
                author_str = "未知作者"
            elif len(authors) == 1:
                author_str = html.escape(authors[0])
            else:
                author_str = html.escape(authors[0]) + " 等"
            rel = relative_time(p["published"], now)
            bj = bj_human(p["published"])
            abs_url = html.escape(p["abs_url"])
            pdf_url = html.escape(p["pdf_url"])
            cards.append(f"""
        <article class="card">
          <div class="card-head">
            <span class="seq">{tseq:02d}</span>
            <span class="source-chip" title="arXiv 主分类">{cat}</span>
            <span class="arxiv-id">{arxiv_id}</span>
          </div>
          <h3 class="card-title">{title}</h3>
          {zh_title_html}
          <p class="card-authors">{author_str}</p>
          <p class="card-summary-short no-math" onclick="toggleCard(this)">{summary_short} <span class="more-hint">…展开全文 ▾</span></p>
          <div class="card-summary-full">
            <p class="card-summary-en">{summary_full}</p>
            {zh_summary_html}
          </div>
          <button class="expand-btn" type="button" aria-expanded="false" onclick="toggleCard(this)">展开全文 ▾</button>
          <div class="card-meta">
            <span class="meta-rel">{rel}</span>
            <span class="meta-sep">·</span>
            <span class="meta-bj">{bj}</span>
          </div>
          <div class="card-foot">
            <a class="btn-primary" href="{abs_url}" target="_blank" rel="noopener noreferrer">摘要页 ↗</a>
            <a class="btn-ghost" href="{pdf_url}" target="_blank" rel="noopener noreferrer">PDF ↗</a>
          </div>
        </article>""")
        cards_html = "\n".join(cards)
        sections_html.append(f"""
      <section id="{anchor}" class="section">
        <header class="section-head">
          <h2><span class="sec-emoji">{emoji}</span>{name}</h2>
          <span class="sec-count">{theme_counts[name]} 篇</span>
          <a href="#top" class="back-top" title="返回顶部">↑</a>
        </header>
        <div class="grid">
{cards_html}
        </div>
      </section>""")
    sections_html_str = "\n".join(sections_html)

    # ---- 前沿期刊（已正式发表）：Nature Physics / Science / PRL —— jitems 已在 main() 抓取并翻译 ----
    journal_count = len(jitems)
    if jitems:
        jcards = [render_journal_card(it, i) for i, it in enumerate(jitems, 1)]
        sections_html_str += f"""
      <section id="journals" class="section">
        <header class="section-head">
          <h2><span class="sec-emoji">🏛️</span>前沿期刊</h2>
          <span class="sec-count">{journal_count} 条 · 已正式发表</span>
          <a href="#top" class="back-top" title="返回顶部">↑</a>
        </header>
        <div class="grid">
{chr(10).join(jcards)}
        </div>
      </section>"""
        nav_items += (f'<a href="#journals" class="nav-link"><span class="nav-emoji">🏛️</span>前沿期刊'
                      f'<span class="nav-count">{journal_count}</span></a>')
        stat_chips += (f'<span class="chip-stat"><span class="chip-emoji">🏛️</span>'
                       f'<span class="chip-label">前沿期刊</span><span class="chip-num">{journal_count}</span></span>')

    now_bj = now.astimezone(BJ)
    today_str = f"{now_bj.year}年{now_bj.month}月{now_bj.day}日 {weekday_cn(now_bj)}"
    fetch_time = f"{now_bj.month}月{now_bj.day}日 {now_bj.strftime('%H:%M')}（北京时间）"
    cats_str = " · ".join(
        "/".join(cats) for _, cats, _, _ in [(t[0], t[1], None, None) for t in THEMES]
    )

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>arXiv 物理最新论文 · {now_bj.strftime('%Y-%m-%d')}</title>
<style>
  :root {{
    --bg: #f5f7fb; --bg-soft: #eaeef5; --bg-card: #ffffff; --bg-card-hover: #eef2f9;
    --border: #e1e6ef; --text: #1a2030; --text-dim: #59647a; --text-mute: #8b95a8;
    --accent: #7c5cf0; --accent-2: #3b7df0; --accent-3: #0c9e8b;
    --shadow: 0 6px 24px rgba(20,30,60,0.10);
    --nav-bg: rgba(255,255,255,0.85);
    --pill-bg: rgba(15,23,42,0.05);
    --accent-soft: rgba(124,92,240,0.12);
    --accent-soft-border: rgba(124,92,240,0.30);
    --accent3-soft: rgba(12,158,139,0.10);
    --accent3-soft-border: rgba(12,158,139,0.28);
  }}
  :root[data-theme="dark"] {{
    --bg: #0d0f14; --bg-soft: #141824; --bg-card: #181d2a; --bg-card-hover: #1d2333;
    --border: #283042; --text: #e6e9ef; --text-dim: #9aa3b2; --text-mute: #6b7384;
    --accent: #a78bfa; --accent-2: #6ea8ff; --accent-3: #4fd1c5;
    --shadow: 0 6px 24px rgba(0,0,0,0.4);
    --nav-bg: rgba(13,15,20,0.92);
    --pill-bg: rgba(255,255,255,0.06);
    --accent-soft: rgba(167,139,250,0.1);
    --accent-soft-border: rgba(167,139,250,0.28);
    --accent3-soft: rgba(79,209,197,0.08);
    --accent3-soft-border: rgba(79,209,197,0.2);
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.6;
    -webkit-font-smoothing: antialiased; min-height: 100vh;
  }}
  a {{ color: inherit; text-decoration: none; }}
  .hero {{
    background: linear-gradient(135deg, rgba(167,139,250,0.10), rgba(110,168,255,0.07) 50%, rgba(79,209,197,0.05)),
                radial-gradient(circle at 85% 0%, rgba(167,139,250,0.10), transparent 55%), var(--bg-soft);
    border-bottom: 1px solid var(--border); padding: 48px 24px 40px;
  }}
  .hero-inner {{ max-width: 1240px; margin: 0 auto; }}
  .hero-eyebrow {{
    display: inline-flex; align-items: center; gap: 8px; font-size: 12px; letter-spacing: 2px;
    color: var(--accent); text-transform: uppercase;
    background: var(--accent-soft); border: 1px solid var(--accent-soft-border);
    padding: 4px 10px; border-radius: 999px; margin-bottom: 16px;
  }}
  .hero-title {{
    font-size: clamp(26px, 4vw, 40px); font-weight: 700; letter-spacing: -0.5px; margin-bottom: 8px;
    background: linear-gradient(90deg, var(--text), var(--accent));
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
  }}
  .hero-sub {{ color: var(--text-dim); font-size: 15px; margin-bottom: 6px; }}
  .hero-window {{ color: var(--text-mute); font-size: 13px; margin-bottom: 10px; }}
  .hero-cats {{ color: var(--text-mute); font-size: 12px; margin-bottom: 26px; line-height: 1.7; word-break: break-all; }}
  .hero-cats code {{ font-family: "SF Mono", "JetBrains Mono", Menlo, monospace; color: var(--accent-3); background: var(--accent3-soft); padding: 1px 6px; border-radius: 4px; }}
  .hero-stats {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }}
  .total-pill {{
    display: inline-flex; align-items: baseline; gap: 6px;
    background: linear-gradient(135deg, var(--accent), var(--accent-2));
    color: #0b0d12; padding: 10px 18px; border-radius: 12px; font-weight: 700; box-shadow: var(--shadow);
  }}
  .total-pill .num {{ font-size: 24px; }} .total-pill .lbl {{ font-size: 12px; opacity: 0.85; }}
  .chip-stat {{
    display: inline-flex; align-items: center; gap: 8px;
    background: var(--bg-card); border: 1px solid var(--border);
    padding: 8px 14px; border-radius: 10px; font-size: 13px;
    transition: transform 0.15s, border-color 0.15s;
  }}
  .chip-stat:hover {{ transform: translateY(-2px); border-color: var(--accent); }}
  .chip-emoji {{ font-size: 16px; }} .chip-label {{ color: var(--text-dim); }} .chip-num {{ color: var(--text); font-weight: 700; }}
  {NAV_CSS}
  {FOOTER_CSS}
  /* 以下为页面专属非-nav 样式 */
  main {{ max-width: 1240px; margin: 0 auto; padding: 32px 24px 64px; }}
  .section {{ margin-bottom: 48px; }}
  .section-head {{ display: flex; align-items: center; gap: 12px; padding-bottom: 12px; margin-bottom: 20px; border-bottom: 1px solid var(--border); }}
  .section-head h2 {{ font-size: 20px; font-weight: 600; display: inline-flex; align-items: center; gap: 8px; }}
  .sec-emoji {{ font-size: 22px; }}
  .sec-count {{ color: var(--text-mute); font-size: 13px; background: var(--bg-card); padding: 2px 10px; border-radius: 999px; border: 1px solid var(--border); }}
  .back-top {{ margin-left: auto; color: var(--text-mute); font-size: 14px; padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border); transition: all 0.15s; }}
  .back-top:hover {{ color: var(--accent); border-color: var(--accent); }}
  .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }}
  .card {{
    background: var(--bg-card); border: 1px solid var(--border); border-radius: 14px;
    padding: 18px 20px; display: flex; flex-direction: column; gap: 10px;
    transition: transform 0.18s, border-color 0.18s, background 0.18s; position: relative; overflow: hidden;
  }}
  .card::before {{ content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: linear-gradient(180deg, var(--accent), transparent); opacity: 0.6; transition: opacity 0.18s; }}
  .card:hover {{ transform: translateY(-3px); border-color: rgba(167,139,250,0.4); background: var(--bg-card-hover); box-shadow: var(--shadow); }}
  .card:hover::before {{ opacity: 1; }}
  .card-head {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .seq {{
    font-family: "SF Mono", "JetBrains Mono", Menlo, monospace; font-size: 13px; font-weight: 700;
    color: var(--accent); background: var(--accent-soft); border: 1px solid var(--accent-soft-border);
    padding: 2px 8px; border-radius: 6px; letter-spacing: 0.5px; flex-shrink: 0;
  }}
  .source-chip {{ font-size: 11px; color: var(--accent-3); background: var(--accent3-soft); border: 1px solid var(--accent3-soft-border); padding: 3px 8px; border-radius: 999px; font-family: "SF Mono","JetBrains Mono",Menlo,monospace; }}
  .arxiv-id {{ font-size: 11px; color: var(--text-mute); font-family: "SF Mono","JetBrains Mono",Menlo,monospace; margin-left: auto; }}
  .journal-date {{ font-size: 11px; color: var(--text-mute); }}
  .journal-tag {{ font-size: 11px; color: var(--accent-3); background: var(--accent3-soft); border: 1px solid var(--accent3-soft-border); padding: 2px 8px; border-radius: 999px; white-space: nowrap; }}
  .card-title {{ font-size: 15px; font-weight: 600; line-height: 1.45; color: var(--text); }}
  .card-authors {{ font-size: 12px; color: var(--text-mute); font-style: italic; }}
  .card-summary-short {{ font-size: 13px; color: var(--text-dim); line-height: 1.6; cursor: pointer; }}
  .card-summary-full {{ display: none; font-size: 13px; color: var(--text-dim); line-height: 1.65; margin-top: 8px; }}
  .card-summary-full .card-summary-en {{ margin: 0 0 8px; }}
  .card.expanded .card-summary-short {{ display: none; }}
  .card.expanded .card-summary-full {{ display: block; }}
  .expand-btn {{ align-self: flex-start; margin-top: 8px; background: transparent; border: 1px solid var(--border); color: var(--accent); font-size: 12px; padding: 4px 11px; border-radius: 7px; cursor: pointer; transition: all 0.15s; }}
  .expand-btn:hover {{ border-color: var(--accent); background: rgba(110,168,255,0.08); }}
  .more-hint {{ color: var(--accent); font-size: 12px; white-space: nowrap; }}
  .card-meta {{ display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text-mute); padding-top: 2px; }}
  .meta-rel {{ color: var(--accent-2); font-weight: 500; }}
  .meta-sep {{ opacity: 0.5; }}
  .card-foot {{ display: flex; gap: 8px; margin-top: auto; padding-top: 4px; }}
  .btn-primary, .btn-ghost {{ display: inline-flex; align-items: center; gap: 4px; padding: 6px 12px; border-radius: 8px; font-size: 12px; font-weight: 500; transition: all 0.15s; border: 1px solid transparent; }}
  .btn-primary {{ background: var(--accent); color: #0b0d12; }}
  .btn-primary:hover {{ background: #b89dff; transform: translateY(-1px); }}
  .btn-ghost {{ background: transparent; color: var(--text-mute); border-color: var(--border); }}
  .btn-ghost:hover {{ color: var(--text); border-color: var(--text-mute); }}
  footer {{ max-width: 1240px; margin: 0 auto; padding: 32px 24px 48px; border-top: 1px solid var(--border); text-align: center; color: var(--text-mute); font-size: 12px; line-height: 1.8; }}
  footer a {{ color: var(--accent); }} footer a:hover {{ text-decoration: underline; }}
  .card-title-zh {{ font-size: 13px; color: var(--text-dim); font-weight: 500; margin-top: -4px; line-height: 1.5; }}
  .card-summary-zh {{ font-size: 12.5px; color: var(--text-mute); line-height: 1.6; padding-left: 8px; border-left: 2px solid var(--border); }}
  body.zh-hidden .card-title-zh, body.zh-hidden .card-summary-zh {{ display: none; }}
  /* nav 规则（.nav-toggle/.theme-toggle/.nav-links/.hamburger/.home-link）已统一到 NAV_CSS */
  @media (max-width: 1024px) {{
    .grid {{ grid-template-columns: repeat(2, 1fr); }}
  }}
  /* 768px nav 响应式规则已统一到 NAV_CSS */
  @media (max-width: 640px) {{
    .hero {{ padding: 32px 16px 28px; }}
    .hero-stats {{ gap: 8px; }}
    .chip-stat {{ padding: 6px 10px; font-size: 12px; }}
    main {{ padding: 24px 16px 48px; }}
    .grid {{ grid-template-columns: 1fr; gap: 12px; }}
    .card {{ padding: 16px; }}
    .arxiv-id {{ margin-left: 0; }}
  }}
  @media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; transition: none !important; scroll-behavior: auto !important; }} }}
</style>
<script>
  (function(){{ try {{ var t = localStorage.getItem('theme'); if (t === 'dark' || t === 'light') {{ document.documentElement.setAttribute('data-theme', t); }} }} catch(e) {{}} }})();
</script>
{MATHJAX_SCRIPTS}
</head>
<body>
<div id="top"></div>
<header class="hero">
  <div class="hero-inner">
    <span class="hero-eyebrow">⚛️ arXiv · Physics Latest</span>
    <h1 class="hero-title">物理最新论文 · {today_str}</h1>
    <p class="hero-sub">按提交时间倒序，每主题取最近 {PER_THEME} 篇，共 <strong>{total}</strong> 篇</p>
    <p class="hero-window">抓取时间：{fetch_time} · 数据源：arXiv API · Nature Physics · Science · PRL（含已正式发表期刊）</p>
    <p class="hero-cats">覆盖类别：<code>{html.escape(cats_str)}</code></p>
    <div class="hero-stats">
      <span class="total-pill"><span class="num">{total}</span><span class="lbl">篇论文</span></span>
      {stat_chips}
    </div>
  </div>
</header>
{build_nav("🔭 arXiv 物理最新论文", nav_items, extra_html='<button id="zh-toggle" class="nav-toggle" type="button">🌐 中文</button>')}
<main>
{sections_html_str}
</main>
{build_footer()}
<footer>
  共 <strong>{total}</strong> 篇 · 数据来源 arXiv.org + 前沿期刊（Nature Physics / Science / PRL）· 时间已转换为北京时间 · 摘要悬停可看全文 · 数学公式由 <a href="https://www.mathjax.org" target="_blank" rel="noopener noreferrer">MathJax</a> 渲染（需联网）· 中文译文由 Google 翻译（仅供参考，以英文原文为准）
</footer>
{TOGGLE_SCRIPT}
<script>
  (function() {{
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    function sync() {{
      var dark = document.documentElement.getAttribute('data-theme') === 'dark';
      btn.innerHTML = dark ? '☀️ 亮色' : '🌙 暗色';
    }}
    sync();
    btn.addEventListener('click', function() {{
      var dark = document.documentElement.getAttribute('data-theme') === 'dark';
      var next = dark ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      try {{ localStorage.setItem('theme', next); }} catch(e) {{}}
      sync();
    }});
  }})();
  (function() {{
    var h = document.getElementById('nav-hamburger');
    var nav = document.querySelector('.nav');
    if (!h || !nav) return;
    function set(open) {{ nav.classList.toggle('menu-open', open); h.textContent = open ? '\u2715' : '\u2630'; h.setAttribute('aria-expanded', open ? 'true' : 'false'); }}
    h.addEventListener('click', function() {{ set(!nav.classList.contains('menu-open')); }});
    Array.prototype.forEach.call(nav.querySelectorAll('.nav-link'), function(a) {{ a.addEventListener('click', function() {{ set(false); }}); }});
    document.addEventListener('click', function(e) {{ if (nav.classList.contains('menu-open') && !nav.contains(e.target)) set(false); }});
  }})();
</script>
</body>
</html>
"""
    return html_doc, total, theme_counts


def main():
    do_translate = TRANSLATE_ZH and "--no-translate" not in sys.argv
    out_dir = OUTPUT_BASE / "arxiv-physics"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[arxiv] 输出目录: {out_dir}", flush=True)
    print(f"[arxiv] 中文翻译: {'开启' if do_translate else '关闭'}", flush=True)

    now = datetime.now(timezone.utc)
    theme_papers = []
    for i, (name, cats, emoji, anchor) in enumerate(THEMES):
        if i > 0:
            time.sleep(3)  # arXiv 限流：1 请求/3 秒
        print(f"[arxiv] 拉取主题 {name} ({cats}) ...", flush=True)
        papers = fetch_theme(cats)
        print(f"[arxiv]   得到 {len(papers)} 篇", flush=True)
        theme_papers.append((name, papers, emoji, anchor))

    if do_translate:
        print("[arxiv] 开始翻译为中文（每篇 1 次调用，约 30 秒）...", flush=True)
        ok = 0
        for name, papers, _, _ in theme_papers:
            for p in papers:
                time.sleep(TRANSLATE_SLEEP)
                zt, zs = translate_paper(p["title"], p["summary"])
                p["zh_title"] = zt
                p["zh_summary"] = zs
                if zt:
                    ok += 1
        print(f"[arxiv] 翻译完成：{ok} 篇成功", flush=True)

    # 前沿期刊：同样抓取并翻译中文，风格与 arXiv 论文卡一致（在 build_html 前完成，传入 jitems）
    journal_feeds = [
        ("https://www.nature.com/nphys/rss/getrss.html", "Nature Physics"),
        ("https://www.science.org/rss/news_current.xml", "Science"),
    ]
    jitems = []
    for _url, _name in journal_feeds:
        jitems.extend(fetch_rss(_url, _name, max_n=6))
    jitems.extend(fetch_prl_crossref(max_n=6))
    jitems = dedup(jitems)[:18]
    if do_translate and jitems:
        print(f"[arxiv] 翻译前沿期刊中文（{len(jitems)} 条）...", flush=True)
        for it in jitems:
            time.sleep(TRANSLATE_SLEEP)
            zt, zs = translate_paper(it["title"], it.get("summary", ""))
            it["zh_title"] = zt
            it["zh_summary"] = zs

    html_doc, total, theme_counts = build_html(theme_papers, jitems, now)
    date_str = now.astimezone(BJ).strftime("%Y-%m-%d")
    latest_path = out_dir / "arxiv_physics_latest.html"
    dated_path = out_dir / f"arxiv_physics_{date_str}.html"
    latest_path.write_text(html_doc, encoding="utf-8")
    dated_path.write_text(html_doc, encoding="utf-8")

    n = write_archive(out_dir, "arxiv_physics_", "arXiv 物理最新论文",
                      "按生成日期排列的历史副本，每日自动追加。", "arxiv_physics_latest.html")
    print(f"[arxiv] 总篇数: {total}", flush=True)
    print(f"[arxiv] 主题分布: {theme_counts}", flush=True)
    print(f"[arxiv] 输出: {latest_path}", flush=True)
    print(f"[arxiv] 归档: {dated_path}", flush=True)
    print(f"[arxiv] 归档索引: {out_dir / 'archive.html'} ({n} 份)", flush=True)


if __name__ == "__main__":
    main()
