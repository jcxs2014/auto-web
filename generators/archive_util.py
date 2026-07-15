"""共享：为各仪表盘目录生成 archive.html 历史归档索引页。

每个生成器在写完 latest / dated 副本后调用 write_archive()，
即可让门户首页的「历史归档」按钮指向 <dir>/archive.html 并正确列出所有 dated 副本。
"""
from __future__ import annotations

import re
from pathlib import Path

ARCHIVE_TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0f1220">
<title>__TITLE__ · 历史归档</title>
<style>
  :root{--bg:#0f1220;--card:#1a1f33;--text:#e8eaf2;--muted:#9aa3c0;--accent:#8b7bff;--line:#2a3050}
  @media (prefers-color-scheme: light){
    :root{--bg:#f5f6fb;--card:#ffffff;--text:#1a1f33;--muted:#5b6480;--accent:#6a5cff;--line:#e2e5f0}
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh;padding:max(24px,env(safe-area-inset-top)) 20px 60px}
  main{max-width:760px;margin:0 auto}
  h1{font-size:clamp(22px,5vw,30px);margin:0 0 6px}
  .sub{color:var(--muted);margin:0 0 24px}
  .archive-list{list-style:none;padding:0;margin:0;display:grid;gap:10px}
  .archive-list a{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 18px;background:var(--card);border:1px solid var(--line);border-radius:12px;text-decoration:none;color:var(--text);transition:.15s}
  .archive-list a:hover{border-color:var(--accent);transform:translateY(-1px)}
  .archive-list .date{font-weight:600}
  .archive-list .go{color:var(--accent);font-size:13px;white-space:nowrap}
  .empty{color:var(--muted);padding:14px 2px}
  .back{margin-top:28px;display:flex;gap:18px;flex-wrap:wrap}
  .back a{color:var(--accent);text-decoration:none}
</style></head>
<body><main>
<h1>📚 __TITLE__ · 历史归档</h1>
<p class="sub">__SUBTITLE__</p>
<ul class="archive-list">
__ITEMS__
</ul>
<p class="back"><a href="__LATEST__">← 返回最新版</a> · <a href="../index.html">← 返回首页</a></p>
</main></body></html>"""


def write_archive(out_dir, prefix, title, subtitle, latest_rel):
    """在 out_dir 生成 archive.html，列出所有 prefix*.html 的 dated 副本（排除 *_latest.html）。

    返回找到的 dated 副本数量。
    """
    out_dir = Path(out_dir)
    files = []
    for p in out_dir.glob(prefix + "*.html"):
        name = p.name
        if name.endswith("_latest.html"):
            continue
        m = re.search(r"(\d{4}-\d{2}-\d{2})", name)
        if not m:
            continue
        files.append((m.group(1), name))
    # 按日期倒序（新→旧）
    files.sort(key=lambda x: x[0], reverse=True)

    if not files:
        items = '<li class="empty">暂无历史归档（每日首次生成后会出现在此处）</li>'
    else:
        items = "\n".join(
            '      <li><a href="%s"><span class="date">📄 %s</span><span class="go">查看 →</span></a></li>'
            % (name, date)
            for date, name in files
        )

    doc = (
        ARCHIVE_TEMPLATE.replace("__TITLE__", title)
        .replace("__SUBTITLE__", subtitle)
        .replace("__ITEMS__", items)
        .replace("__LATEST__", latest_rel)
    )
    (out_dir / "archive.html").write_text(doc, encoding="utf-8")
    return len(files)
