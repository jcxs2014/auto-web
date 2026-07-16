#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_dashboard_index.py — 自动生成根目录索引页，让各卡片"更新日期"随产物自动刷新。

设计要点：
  1. 不破坏现有 index.html 的可编辑结构：只改写 DASHBOARDS 数组中每个对象的
     `updated` 字段，其余 HTML/CSS/JS 原样保留。
  2. 日期来源尽量真实：
     - aihot / arxiv-physics / hotnews / news：从各自目录里的 dated 归档文件
       （如 aihot_daily_2026-07-16.html）取最新日期；
     - rss：无 dated 文件，从 rss/index.html 的 updated-badge 解析；
     - 兜底：解析不到时，用当前日期，避免空白。
  3. 无变更时不写文件，避免无意义的空提交。

用法：
  cd generators && python gen_dashboard_index.py
"""

import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "index.html"

# 各仪表盘日期提取策略
DASHBOARD_DATE_SOURCES = {
    "aihot":        ("aihot",        r"aihot_daily_(\d{4}-\d{2}-\d{2})\.html"),
    "arxiv-physics":("arxiv-physics",r"arxiv_physics_(\d{4}-\d{2}-\d{2})\.html"),
    "hotnews":      ("hotnews",      r"hotnews_(\d{4}-\d{2}-\d{2})\.html"),
    "news":         ("news",         r"news_(\d{4}-\d{2}-\d{2})\.html"),
}


def _extract_date_from_filenames(directory, pattern):
    """从目录中匹配命名模式的文件提取最新日期。"""
    if not directory.exists():
        return None
    dates = []
    for f in directory.iterdir():
        if not f.is_file():
            continue
        m = re.match(pattern, f.name)
        if m:
            dates.append(m.group(1))
    return max(dates) if dates else None


def _extract_date_from_badge(html_text):
    """尝试从 HTML 的 updated-badge 元素文本中抠出 YYYY-MM-DD。"""
    # 必须匹配 HTML 标签（<... class="updated-badge">...），避免误匹配 CSS 规则 .updated-badge
    m = re.search(r'<[^>]*updated-badge[^>]*>([^<]+)', html_text)
    if not m:
        return None
    text = m.group(1).strip()
    # 尝试 ISO 日期
    iso = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if iso:
        return iso.group(1)
    # 中文格式：7月16日 → 2026-07-16（假设当年）
    cn = re.search(r'(\d{1,2})月(\d{1,2})日', text)
    if cn:
        year = datetime.now().year
        return f"{year}-{int(cn.group(1)):02d}-{int(cn.group(2)):02d}"
    return None


def _get_latest_index_html(directory):
    """返回目录内可能是入口页的 HTML 文件。"""
    candidates = [directory / "index.html", directory / f"{directory.name}_latest.html"]
    # 针对各仪表盘命名习惯补充
    name = directory.name
    if name == "aihot":
        candidates.append(directory / "aihot_daily_latest.html")
    elif name == "arxiv-physics":
        candidates.append(directory / "arxiv_physics_latest.html")
    for c in candidates:
        if c.exists():
            return c
    return None


def get_dashboard_date(key):
    """获取某个仪表盘在索引上应显示的更新日期。"""
    if key in DASHBOARD_DATE_SOURCES:
        dir_name, pattern = DASHBOARD_DATE_SOURCES[key]
        directory = ROOT / dir_name
        d = _extract_date_from_filenames(directory, pattern)
        if d:
            return d

    # RSS 或上述兜底：从入口页 badge 解析
    if key == "rss":
        directory = ROOT / "rss"
    elif key in DASHBOARD_DATE_SOURCES:
        directory = ROOT / DASHBOARD_DATE_SOURCES[key][0]
    else:
        directory = ROOT / key

    idx = _get_latest_index_html(directory)
    if idx:
        text = idx.read_text(encoding="utf-8")
        d = _extract_date_from_badge(text)
        if d:
            return d

    # 最终兜底：今天
    return datetime.now().strftime("%Y-%m-%d")


def _replace_updated_field(m):
    """re.sub 回调：只替换 matched 对象的 updated 值。"""
    key = m.group(1)
    before = m.group(2)
    # group(3) 是 updated 之后到对象结束（不含 closing }）的内容
    after = m.group(3)
    new_date = get_dashboard_date(key)
    # 注意：原正则的 \} 本身不是捕获组，所以这里要补回 closing }
    return f'{{\n    key: "{key}",{before}updated: "{new_date}"{after}}}'


def main():
    if not INDEX_HTML.exists():
        print(f"[gen_dashboard_index] WARN: {INDEX_HTML} 不存在，跳过")
        return 0

    html = INDEX_HTML.read_text(encoding="utf-8")

    # 匹配 DASHBOARDS 数组里的每个对象：key + updated 字段
    # 注意：对象内部字段顺序可能有变，这里只要求 key 在前面、updated 在对象内出现
    obj_re = re.compile(
        r'\{\s*key:\s*"([^"]+)",'
        r'(.*?)'                            # key 与 updated 之间的字段
        r'updated:\s*"\d{4}-\d{2}-\d{2}"'  # 当前硬编码日期
        r'(.*?)\}',                         # updated 之后到对象结束
        re.S,
    )

    if not obj_re.search(html):
        print("[gen_dashboard_index] WARN: 未在 index.html 中找到 DASHBOARDS 对象，跳过")
        return 0

    new_html = obj_re.sub(_replace_updated_field, html)

    if new_html == html:
        print("[gen_dashboard_index] 各仪表盘日期无变化，无需写回")
        return 0

    INDEX_HTML.write_text(new_html, encoding="utf-8")
    print("[gen_dashboard_index] 根目录索引日期已更新 ->", INDEX_HTML)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
