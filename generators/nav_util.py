#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nav_util.py —— 三个仪表盘（hotnews / aihot / arxiv-physics）共享的顶部导航栏。

目标：让三页的导航栏在「结构 / 样式 / 位置」三方面完全一致，避免过去各写各的、
每次只统一一半（胶囊样式统一了、结构/位置却仍不同）的问题。

用法（三种生成器通用）：
    from nav_util import NAV_CSS, build_nav

    # 1) CSS：把 NAV_CSS 注入到 <style> 里，替换掉各页原来散落的 .nav* 规则
    #    - hotnews（TEMPLATE + .replace）：用 __NAV_CSS__ 占位符
    #    - aihot / arxiv（f-string）：直接写 {NAV_CSS}
    #
    # 2) HTML：nav_html = build_nav(brand, nav_links_inner, extra_html="", home_href="../index.html")
    #    生成的 <nav> 结构三页完全相同。

跨主题变量别名说明：
    hotnews 用 --line / --muted；aihot / arxiv 用 --border / --text-mute。
    用 var(--line, var(--border)) 这类回退，保证同一套 CSS 在三种主题下都能解析。
    其余用到的 --nav-bg / --pill-bg / --accent / --text / --shadow 三页均已定义。
"""

# 让别名在 light / dark 两种 :root 下都能正确回退到「本页实际存在」的变量
NAV_CSS = """
:root{
  --nav-line:var(--line, var(--border));
  --nav-mute:var(--muted, var(--text-mute));
}
.nav{position:sticky;top:0;z-index:30;background:var(--nav-bg);
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  border-bottom:1px solid var(--nav-line)}
.nav-inner{max-width:1180px;margin:0 auto;display:flex;align-items:center;gap:10px 14px;flex-wrap:wrap;
  padding:11px max(20px,env(safe-area-inset-right)) 11px max(20px,env(safe-area-inset-left))}
.nav-brand{font-weight:800;font-size:17px;margin-right:auto;display:flex;align-items:center;gap:7px;white-space:nowrap}
.nav-links{display:flex;align-items:center;gap:8px;flex-wrap:wrap;flex:1 1 auto;min-width:0;scrollbar-width:none}
.nav-links::-webkit-scrollbar{display:none}
.nav-link{display:inline-flex;align-items:center;gap:6px;padding:5px 11px;border-radius:999px;
  background:var(--pill-bg);color:var(--text);text-decoration:none;font-size:13.5px;white-space:nowrap;transition:filter .15s}
.nav-link:hover{filter:brightness(.95)}
.nav-emoji{font-size:14px}
.nav-count{font-size:11px;color:var(--nav-mute)}
.home-link,.theme-toggle,.nav-toggle,.hamburger{display:inline-flex;align-items:center;gap:5px;
  padding:5px 12px;border-radius:999px;font-size:13.5px;color:var(--text);background:var(--pill-bg);
  border:1px solid var(--nav-line);text-decoration:none;white-space:nowrap;cursor:pointer;transition:border-color .15s,filter .15s}
.home-link:hover,.theme-toggle:hover,.nav-toggle:hover{border-color:var(--accent)}
.hamburger{border-radius:10px;font-size:18px;line-height:1;display:none;justify-content:center}
.nav-toggle.off{opacity:.5}
@media(max-width:768px){
  .nav-inner{flex-wrap:nowrap;gap:10px}
  .nav-brand{font-size:16px;margin-right:auto}
  .hamburger{display:inline-flex}
  .nav-links{position:absolute;top:100%;left:0;right:0;display:none;flex-direction:column;align-items:stretch;gap:6px;
    padding:10px max(16px,env(safe-area-inset-left)) 14px max(16px,env(safe-area-inset-right));
    background:var(--nav-bg);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
    border-bottom:1px solid var(--nav-line);box-shadow:var(--shadow);z-index:40}
  .nav.menu-open .nav-links{display:flex}
  .nav-link{width:100%;justify-content:space-between;padding:11px 14px;font-size:15px;border-radius:10px}
  .nav-count{font-size:12px}
}
"""


def build_nav(brand, nav_links_html, extra_html="", home_href="../index.html"):
    """返回统一结构的 <nav>。

    brand          : 站点标题（含 emoji），如 '🔥 综合中文热点'
    nav_links_html : 已拼好的 .nav-link 元素字符串（不含外层 <span class="nav-links">）
    extra_html     : 额外控件（如 arxiv 的中文开关），插在「主题切换」之前
    home_href      : 返回首页链接（默认 ../index.html）
    """
    return (
        '<nav class="nav"><div class="nav-inner">'
        f'<a class="home-link" href="{home_href}" title="返回首页">← 首页</a>'
        f'<span class="nav-brand">{brand}</span>'
        f'<span class="nav-links">{nav_links_html}</span>'
        f'{extra_html}'
        '<button id="theme-toggle" class="theme-toggle" type="button">🌙 暗色</button>'
        '<button id="nav-hamburger" class="hamburger" type="button" aria-label="导航菜单" aria-expanded="false">☰</button>'
        '</div></nav>'
    )


# ---------- 延伸资源 footer（三页共享单一真值） ----------
FOOTER_CSS = """
.ext-res{max-width:1080px;margin:36px auto 0;padding:22px 18px 6px;border-top:1px solid var(--line)}
.ext-res h3{font-size:16px;margin-bottom:16px;color:var(--text);display:flex;align-items:center;gap:8px}
.ext-cols{display:grid;grid-template-columns:repeat(4,1fr);gap:18px 22px}
@media(max-width:900px){.ext-cols{grid-template-columns:repeat(2,1fr)}}
@media(max-width:560px){.ext-cols{grid-template-columns:1fr}}
.ext-col h4{font-size:13.5px;color:var(--accent2);margin:0 0 9px;font-weight:700}
.ext-col a{display:block;color:var(--text);text-decoration:none;font-size:13px;padding:5px 0;
  border-bottom:1px solid var(--line);transition:color .15s, padding-left .15s}
.ext-col a:hover{color:var(--accent2);padding-left:5px}
.ext-note{margin-top:18px;font-size:12px;color:var(--muted);text-align:center;line-height:1.7}
"""

# 延伸资源清单（域名均为稳定大站；外链统一新窗口打开）
_EXT_LINKS = [
    ("综合热榜", [
        ("今日热榜", "https://tophub.today"),
        ("百度热搜", "https://top.baidu.com/board"),
        ("微博热搜", "https://s.weibo.com/top/summary"),
        ("B站热门", "https://www.bilibili.com/v/popular/all"),
    ]),
    ("科技 / AI", [
        ("Hacker News", "https://news.ycombinator.com"),
        ("Reddit · technology", "https://www.reddit.com/r/technology/"),
        ("Hugging Face Papers", "https://huggingface.co/papers"),
        ("Papers with Code", "https://paperswithcode.com"),
    ]),
    ("学术 / 物理", [
        ("arXiv", "https://arxiv.org"),
        ("Google Scholar", "https://scholar.google.com"),
        ("Semantic Scholar", "https://www.semanticscholar.org"),
        ("Nature", "https://www.nature.com"),
    ]),
    ("新闻聚合", [
        ("Google News", "https://news.google.com"),
        ("Ground News", "https://ground.news"),
        ("BBC News", "https://www.bbc.com/news"),
        ("新华社", "https://www.news.cn"),
    ]),
]


def build_footer():
    """返回「延伸资源」板块 HTML（纯静态链接，无后端）。"""
    cols = []
    for title, links in _EXT_LINKS:
        items = "".join(
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">{name}</a>'
            for name, url in links
        )
        cols.append(f'<div class="ext-col"><h4>{title}</h4>{items}</div>')
    return (
        '<section class="ext-res"><h3>🔗 延伸资源</h3>'
        f'<div class="ext-cols">{"".join(cols)}</div>'
        '<p class="ext-note">以上为同类信息聚合 / 新闻站点，供延伸阅读 · 本站仅聚合公开热榜与新闻接口，与所列站点无隶属关系</p>'
        '</section>'
    )
