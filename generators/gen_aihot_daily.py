#!/usr/bin/env python3
"""
AI HOT 日报 HTML 仪表盘生成器（自包含、可被定时任务调用）。

流程：
  1. 拉 https://aihot.virxact.com/api/public/daily （必须带浏览器 UA，否则 nginx UA 黑名单 403）
  2. 若当日无数据（空 sections / 404），回退到 /api/public/dailies?take=1 取最近一期日期，再拉 /daily/{date}
  3. 按五版块固定顺序分组 + 全局连续编号，生成单文件 HTML（纯内联 CSS/JS，无外部资源）
  4. 输出到 /Users/jcxs2014/Sites/Workbuddy/aihot/：aihot_daily_latest.html（稳定书签）+ aihot_daily_YYYY-MM-DD.html（dated 归档）
  5. stdout 打印简报：日期 / 总条数 / 五版块条数 / 输出路径

被定时任务调用时无需任何参数；也可手动 `python3 gen_aihot_daily.py` 运行。
"""
import json
import html
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from archive_util import write_archive
from nav_util import NAV_CSS, build_nav, FOOTER_CSS, build_footer
from sources_util import fetch_hf_papers, fetch_hn_ai, fetch_arxiv_cs, dedup, attach_cn

BASE = "https://aihot.virxact.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
BJ = timezone(timedelta(hours=8))

# 生成网页统一存放目录：按类型分子文件夹（aihot/、arxiv-physics/ 等）
# 基于脚本位置推导仓库根（generators/ 的上一级），CI 与本地通用
OUTPUT_BASE = Path(__file__).resolve().parent.parent

SECTION_ORDER = ["模型发布/更新", "产品发布/更新", "行业动态", "论文研究", "技巧与观点"]
SECTION_ANCHORS = {
    "模型发布/更新": "models",
    "产品发布/更新": "products",
    "行业动态": "industry",
    "论文研究": "papers",
    "技巧与观点": "tips",
}
SECTION_EMOJI = {
    "模型发布/更新": "🧠",
    "产品发布/更新": "🚀",
    "行业动态": "📰",
    "论文研究": "📄",
    "技巧与观点": "💡",
}


def fetch(path: str) -> dict:
    url = path if path.startswith("http") else BASE + path
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_path": path}
    except Exception as e:
        return {"_error": str(e), "_path": path}


def fetch_daily() -> dict:
    """Fetch today's daily; fall back to most recent if today empty/missing."""
    d = fetch("/api/public/daily")
    # Validate: must have sections with at least one item total
    if isinstance(d, dict) and d.get("sections"):
        total = sum(len(s.get("items", [])) for s in d["sections"])
        if total > 0:
            return d
    # Fallback: list dailies, pick most recent
    listing = fetch("/api/public/dailies?take=5")
    recent_date = None
    if isinstance(listing, dict) and listing.get("items"):
        # items may be list of {date: "..."} or similar
        for it in listing["items"][:5]:
            cand = it.get("date") if isinstance(it, dict) else None
            if cand:
                recent_date = cand
                break
    elif isinstance(listing, list) and listing:
        cand = listing[0].get("date") if isinstance(listing[0], dict) else None
        if cand:
            recent_date = cand
    if recent_date:
        d2 = fetch(f"/api/public/daily/{recent_date}")
        if isinstance(d2, dict) and d2.get("sections"):
            total2 = sum(len(s.get("items", [])) for s in d2["sections"])
            if total2 > 0:
                return d2
    # If we got here with a non-empty d (had sections but zero items), return it anyway
    if isinstance(d, dict) and d.get("sections"):
        return d
    raise RuntimeError(f"无法从 AI HOT 拉到任何日报数据。daily={d!r}, listing={listing!r}")


def cn_truncate(s: str, n: int = 60) -> str:
    if len(s) <= n:
        return s
    return s[:n].rstrip() + "…"


def render_extra_card(it: dict, seq: int) -> str:
    """渲染「一手源」归一化条目（sources_util 返回结构）为卡片。英文标题/摘要 + 中文译文。"""
    title = html.escape(it.get("title", ""))
    summary = html.escape(cn_truncate(it.get("summary", ""), 60))
    source = html.escape(it.get("source", "未知来源"))
    url = html.escape(it.get("url", "#"))
    extra = it.get("extra")
    extra_html = f'<span class="card-extra">{html.escape(extra)}</span>' if extra else ""
    date = it.get("date")
    date_html = f'<span class="card-date">{html.escape(date)}</span>' if date else ""
    zh_title = html.escape(it.get("zh_title", ""))
    zh_summary = html.escape(it.get("zh_summary", ""))
    zh_title_html = f'<p class="card-title-zh">🌐 {zh_title}</p>' if zh_title else ""
    summary_html = f'<p class="card-summary">{summary}</p>' if summary else ""
    zh_summary_html = f'<p class="card-summary-zh">{zh_summary}</p>' if zh_summary else ""
    return f"""
        <article class="card">
          <div class="card-head">
            <span class="seq">{seq:02d}</span>
            <span class="source-chip" title="{source}">{source}</span>
            {extra_html}
          </div>
          <h3 class="card-title">{title}</h3>
          {zh_title_html}
          {summary_html}
          {zh_summary_html}
          <div class="card-foot">
            <a class="btn-primary" href="{url}" target="_blank" rel="noopener noreferrer">阅读原文 ↗</a>
            {date_html}
          </div>
        </article>"""


def bj_human(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(BJ)
    return f"{dt.month}月{dt.day}日 {dt.strftime('%H:%M')}（北京时间）"


def bj_window(start_iso: str, end_iso: str) -> str:
    s = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone(BJ)
    e = datetime.fromisoformat(end_iso.replace("Z", "+00:00")).astimezone(BJ)
    return f"{s.month}月{s.day}日 {s.strftime('%H:%M')} — {e.month}月{e.day}日 {e.strftime('%H:%M')}（北京时间）"


def weekday_cn(d: datetime) -> str:
    return ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][d.weekday()]


def build_html(d: dict) -> tuple[str, str, int, dict]:
    """Return (html_string, date_str, total, section_counts)."""
    date_str = d["date"]
    date_dt = datetime.fromisoformat(date_str).astimezone(BJ)
    window = bj_window(d["windowStart"], d["windowEnd"])
    generated = bj_human(d["generatedAt"])
    # 本页真正被重生成的时间（随每次自动更新变化），区别于 AI HOT 数据源期次
    _now = datetime.now(BJ)
    page_updated = f"{_now.month}月{_now.day}日 {_now.strftime('%H:%M')}（北京时间）"
    canonical = d.get("attribution", {}).get("canonical", f"{BASE}/daily/{date_str}")

    sections_by_label = {s["label"]: s["items"] for s in d["sections"]}
    grouped = []
    seq = 0
    total = 0
    section_counts = {}
    for label in SECTION_ORDER:
        items = sections_by_label.get(label, [])
        section_counts[label] = len(items)
        total += len(items)
        indexed = []
        for it in items:
            seq += 1
            indexed.append((seq, it))
        grouped.append((label, indexed))

    stat_chips = "".join(
        f'<span class="chip-stat"><span class="chip-emoji">{SECTION_EMOJI[l]}</span><span class="chip-label">{l}</span><span class="chip-num">{section_counts[l]}</span></span>'
        for l in SECTION_ORDER
    )
    nav_items = "".join(
        f'<a href="#{SECTION_ANCHORS[l]}" class="nav-link"><span class="nav-emoji">{SECTION_EMOJI[l]}</span>{l}<span class="nav-count">{section_counts[l]}</span></a>'
        for l in SECTION_ORDER
    )

    sections_html = []
    for label, indexed in grouped:
        anchor = SECTION_ANCHORS[label]
        emoji = SECTION_EMOJI[label]
        cards = []
        for seq, it in indexed:
            title = html.escape(it["title"])
            summary = html.escape(cn_truncate(it.get("summary", ""), 60))
            source = html.escape(it.get("sourceName", "未知来源"))
            url = html.escape(it.get("sourceUrl", it.get("permalink", "#")))
            permalink = html.escape(it.get("permalink", ""))
            cards.append(f"""
        <article class="card">
          <div class="card-head">
            <span class="seq">{seq:02d}</span>
            <span class="source-chip" title="{source}">{source}</span>
          </div>
          <h3 class="card-title">{title}</h3>
          <p class="card-summary">{summary}</p>
          <div class="card-foot">
            <a class="btn-primary" href="{url}" target="_blank" rel="noopener noreferrer">阅读原文 ↗</a>
            {f'<a class="btn-ghost" href="{permalink}" target="_blank" rel="noopener noreferrer">AI HOT</a>' if permalink else ''}
          </div>
        </article>""")
        cards_html = "\n".join(cards)
        sections_html.append(f"""
      <section id="{anchor}" class="section">
        <header class="section-head">
          <h2><span class="sec-emoji">{emoji}</span>{label}</h2>
          <span class="sec-count">{section_counts[label]} 条</span>
          <a href="#top" class="back-top" title="返回顶部">↑</a>
        </header>
        <div class="grid">
{cards_html}
        </div>
      </section>""")
    sections_html_str = "\n".join(sections_html)

    # ---- 额外一手源：多源聚合，避免单一聚合器视角 / 单点故障 ----
    extra_defs = [
        ("🔬 Hugging Face 论文", "hf", fetch_hf_papers(date_str)),
        ("💬 Hacker News 热议", "hn", fetch_hn_ai()),
        ("📄 arXiv 最新 AI", "arxiv", fetch_arxiv_cs()),
    ]
    extra_blocks = []
    for label, anchor, raw in extra_defs:
        items = dedup(raw)[:14]
        if not items:
            continue
        # 服务端翻译英文标题/摘要为中文（失败单条降级，不阻塞整页）
        ok = attach_cn(items)
        print(f"[aihot] 翻译「{label[1:]}」: {ok}/{len(items)} 条成功", flush=True)
        section_counts[label] = len(items)
        total += len(items)
        cards = [render_extra_card(it, i) for i, it in enumerate(items, 1)]
        extra_blocks.append(f"""
      <section id="{anchor}" class="section">
        <header class="section-head">
          <h2><span class="sec-emoji">{label[0]}</span>{label[1:]}</h2>
          <span class="sec-count">{len(items)} 条</span>
          <a href="#top" class="back-top" title="返回顶部">↑</a>
        </header>
        <div class="grid">
{chr(10).join(cards)}
        </div>
      </section>""")
        nav_items += (f'<a href="#{anchor}" class="nav-link">'
                      f'<span class="nav-emoji">{label[0]}</span>{label[1:]}'
                      f'<span class="nav-count">{section_counts[label]}</span></a>')
        stat_chips += (f'<span class="chip-stat"><span class="chip-emoji">{label[0]}</span>'
                       f'<span class="chip-label">{label[1:]}</span>'
                       f'<span class="chip-num">{section_counts[label]}</span></span>')
    if extra_blocks:
        sections_html_str += "\n".join(extra_blocks)

    today_str = f"{date_dt.year}年{date_dt.month}月{date_dt.day}日 {weekday_cn(date_dt)}"

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI HOT 日报 · {date_str}</title>
<style>
  :root {{
    --bg: #f5f7fb; --bg-soft: #eaeef5; --bg-card: #ffffff; --bg-card-hover: #eef2f9;
    --border: #e1e6ef; --text: #1a2030; --text-dim: #59647a; --text-mute: #8b95a8;
    --accent: #3b7df0; --accent-2: #6a4cf0; --accent-3: #0c9e8b;
    --shadow: 0 6px 24px rgba(20,30,60,0.10);
    --nav-bg: rgba(255,255,255,0.85);
    --pill-bg: rgba(15,23,42,0.05);
    --accent-soft: rgba(59,125,240,0.12);
    --accent-soft-border: rgba(59,125,240,0.30);
    --accent3-soft: rgba(12,158,139,0.10);
    --accent3-soft-border: rgba(12,158,139,0.28);
  }}
  :root[data-theme="dark"] {{
    --bg: #0f1115; --bg-soft: #161a22; --bg-card: #1a1f29; --bg-card-hover: #1f2531;
    --border: #2a3140; --text: #e6e9ef; --text-dim: #9aa3b2; --text-mute: #6b7384;
    --accent: #6ea8ff; --accent-2: #8b7bff; --accent-3: #4fd1c5;
    --shadow: 0 6px 24px rgba(0,0,0,0.35);
    --nav-bg: rgba(15,17,21,0.92);
    --pill-bg: rgba(255,255,255,0.06);
    --accent-soft: rgba(110,168,255,0.1);
    --accent-soft-border: rgba(110,168,255,0.25);
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
    background: linear-gradient(135deg, rgba(110,168,255,0.08), rgba(139,123,255,0.06) 50%, rgba(79,209,197,0.05)),
                radial-gradient(circle at 80% 0%, rgba(246,135,179,0.08), transparent 50%), var(--bg-soft);
    border-bottom: 1px solid var(--border); padding: 48px 24px 40px;
  }}
  .hero-inner {{ max-width: 1200px; margin: 0 auto; }}
  .hero-eyebrow {{
    display: inline-flex; align-items: center; gap: 8px; font-size: 12px; letter-spacing: 2px;
    color: var(--accent); text-transform: uppercase;
    background: var(--accent-soft); border: 1px solid var(--accent-soft-border);
    padding: 4px 10px; border-radius: 999px; margin-bottom: 16px;
  }}
  .hero-title {{
    font-size: clamp(28px, 4vw, 42px); font-weight: 700; letter-spacing: -0.5px; margin-bottom: 8px;
    background: linear-gradient(90deg, var(--text), var(--accent));
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
  }}
  .hero-date {{ color: var(--text-dim); font-size: 15px; margin-bottom: 6px; }}
  .hero-window {{ color: var(--text-mute); font-size: 13px; margin-bottom: 28px; }}
  .updated-badge {{ color: var(--accent); font-weight: 700; }}
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

  main {{ max-width: 1200px; margin: 0 auto; padding: 32px 24px 64px; }}
  .section {{ margin-bottom: 48px; }}
  .section-head {{
    display: flex; align-items: center; gap: 12px; padding-bottom: 12px; margin-bottom: 20px; border-bottom: 1px solid var(--border);
  }}
  .section-head h2 {{ font-size: 20px; font-weight: 600; display: inline-flex; align-items: center; gap: 8px; }}
  .sec-emoji {{ font-size: 22px; }}
  .sec-count {{ color: var(--text-mute); font-size: 13px; background: var(--bg-card); padding: 2px 10px; border-radius: 999px; border: 1px solid var(--border); }}
  .back-top {{ margin-left: auto; color: var(--text-mute); font-size: 14px; padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border); transition: all 0.15s; }}
  .back-top:hover {{ color: var(--accent); border-color: var(--accent); }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }}
  .card {{
    background: var(--bg-card); border: 1px solid var(--border); border-radius: 14px;
    padding: 18px 20px; display: flex; flex-direction: column; gap: 12px;
    transition: transform 0.18s, border-color 0.18s, background 0.18s; position: relative; overflow: hidden;
  }}
  .card::before {{
    content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
    background: linear-gradient(180deg, var(--accent), transparent); opacity: 0.6; transition: opacity 0.18s;
  }}
  .card:hover {{ transform: translateY(-3px); border-color: rgba(110,168,255,0.4); background: var(--bg-card-hover); box-shadow: var(--shadow); }}
  .card:hover::before {{ opacity: 1; }}
  .card-head {{ display: flex; align-items: center; gap: 10px; }}
  .seq {{
    font-family: "SF Mono", "JetBrains Mono", Menlo, monospace; font-size: 13px; font-weight: 700;
    color: var(--accent); background: var(--accent-soft); border: 1px solid var(--accent-soft-border);
    padding: 2px 8px; border-radius: 6px; letter-spacing: 0.5px; flex-shrink: 0;
  }}
  .source-chip {{
    font-size: 11px; color: var(--text-dim); background: var(--pill-bg);
    border: 1px solid var(--border); padding: 3px 10px; border-radius: 999px;
    max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }}
  .card-extra {{ font-size: 11px; color: var(--accent-3); background: var(--accent3-soft); border: 1px solid var(--accent3-soft-border); padding: 2px 8px; border-radius: 999px; white-space: nowrap; }}
  .card-date {{ font-size: 11px; color: var(--text-mute); }}
  .card-title {{ font-size: 15px; font-weight: 600; line-height: 1.45; color: var(--text); }}
  .card-summary {{ font-size: 13px; color: var(--text-dim); line-height: 1.6; }}
  .card-title-zh {{ font-size: 13px; font-weight: 500; line-height: 1.5; color: var(--accent); }}
  .card-summary-zh {{ font-size: 13px; color: var(--text-dim); line-height: 1.7; margin-top: 2px; border-left: 2px solid var(--accent-soft-border); padding-left: 8px; }}
  .card-foot {{ display: flex; gap: 8px; margin-top: auto; padding-top: 4px; }}
  .btn-primary, .btn-ghost {{
    display: inline-flex; align-items: center; gap: 4px; padding: 6px 12px; border-radius: 8px;
    font-size: 12px; font-weight: 500; transition: all 0.15s; border: 1px solid transparent;
  }}
  .btn-primary {{ background: var(--accent); color: #0b0d12; }}
  .btn-primary:hover {{ background: #82b6ff; transform: translateY(-1px); }}
  .btn-ghost {{ background: transparent; color: var(--text-mute); border-color: var(--border); }}
  .btn-ghost:hover {{ color: var(--text); border-color: var(--text-mute); }}
  footer {{
    max-width: 1200px; margin: 0 auto; padding: 32px 24px 48px;
    border-top: 1px solid var(--border); text-align: center; color: var(--text-mute); font-size: 12px; line-height: 1.8;
  }}
  footer a {{ color: var(--accent); }} footer a:hover {{ text-decoration: underline; }}
  @media (max-width: 640px) {{
    .hero {{ padding: 32px 16px 28px; }}
    .hero-stats {{ gap: 8px; }}
    .chip-stat {{ padding: 6px 10px; font-size: 12px; }}
    main {{ padding: 24px 16px 48px; }}
    .grid {{ grid-template-columns: 1fr; gap: 12px; }}
    .card {{ padding: 16px; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    * {{ animation: none !important; transition: none !important; scroll-behavior: auto !important; }}
  }}
</style>
<script>
  (function(){{ try {{ var t = localStorage.getItem('theme'); if (t === 'dark' || t === 'light') {{ document.documentElement.setAttribute('data-theme', t); }} }} catch(e) {{}} }})();
</script>
</head>
<body>
<div id="top"></div>
<header class="hero">
  <div class="hero-inner">
    <span class="hero-eyebrow">⚡ AI HOT · Daily Briefing</span>
    <h1 class="hero-title">AI 日报 · {today_str}</h1>
    <p class="hero-date">数据窗口：{window} · 数据生成：{generated}（AI HOT 期次）</p>
    <p class="hero-window">本页更新：<span class="updated-badge">{page_updated}</span> · 多源聚合：AI HOT · Hugging Face · Hacker News · arXiv</p>
    <div class="hero-stats">
      <span class="total-pill"><span class="num">{total}</span><span class="lbl">条精选</span></span>
      {stat_chips}
    </div>
  </div>
</header>
{build_nav("📰 AI HOT 科技日报", nav_items)}
<main>
{sections_html_str}
</main>
{build_footer()}
<footer>
  共 <strong>{total}</strong> 条 · 多源聚合：AI HOT · Hugging Face Papers · Hacker News · arXiv cs.AI/LG/CL · 时间已转换为北京时间
</footer>
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
    function set(open) {{ nav.classList.toggle('menu-open', open); h.textContent = open ? '✕' : '☰'; h.setAttribute('aria-expanded', open ? 'true' : 'false'); }}
    h.addEventListener('click', function() {{ set(!nav.classList.contains('menu-open')); }});
    Array.prototype.forEach.call(nav.querySelectorAll('.nav-link'), function(a) {{ a.addEventListener('click', function() {{ set(false); }}); }});
    document.addEventListener('click', function(e) {{ if (nav.classList.contains('menu-open') && !nav.contains(e.target)) set(false); }});
  }})();
</script>
</body>
</html>
"""
    return html_doc, date_str, total, section_counts


def main():
    out_dir = OUTPUT_BASE / "aihot"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[aihot] 输出目录: {out_dir}", flush=True)
    d = fetch_daily()
    html_doc, date_str, total, section_counts = build_html(d)

    latest_path = out_dir / "aihot_daily_latest.html"
    dated_path = out_dir / f"aihot_daily_{date_str}.html"
    latest_path.write_text(html_doc, encoding="utf-8")
    dated_path.write_text(html_doc, encoding="utf-8")

    n = write_archive(out_dir, "aihot_daily_", "AI HOT 每日科技日报",
                      "按生成日期排列的历史副本，每日自动追加。", "aihot_daily_latest.html")
    print(f"[aihot] 日期: {date_str}", flush=True)
    print(f"[aihot] 总条数: {total}", flush=True)
    print(f"[aihot] 版块: {section_counts}", flush=True)
    print(f"[aihot] 输出: {latest_path}", flush=True)
    print(f"[aihot] 归档: {dated_path}", flush=True)
    print(f"[aihot] 归档索引: {out_dir / 'archive.html'} ({n} 份)", flush=True)


if __name__ == "__main__":
    main()
