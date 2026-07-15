#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sources_util.py —— 多数据源抓取工具，供 AI 新闻页 / 物理论文页共用。

设计原则（对应「来源单一」痛点）：
  1. 每个 fetcher 独立 try/except，失败时返回 []，绝不因单源故障把整页打空。
  2. 返回结构统一为 list[dict]，字段：
       title   : 标题
       url     : 跳转链接
       summary : 摘要（可选，已截断）
       source  : 来源名（用于 source-chip 展示）
       extra   : 次要信息（票数 / 分类 / 期刊名等，可选）
       date    : 展示用日期字符串（可选）
  3. 单一真源：AI 新闻页与物理页都从这里取，新增源只改本文件。

已验证可用的源（2026-07-14 探针）：
  - Hugging Face 每日论文  : https://huggingface.co/api/daily_papers
  - Hacker News (Algolia)  : https://hn.algolia.com/api/v1/search
  - arXiv cs.AI/LG/CL      : http://export.arxiv.org/api/query
  - Nature Physics RSS     : https://www.nature.com/nphys/rss/getrss.html
  - Science 新闻 RSS       : https://www.science.org/rss/news_current.xml
  - PRL (Crossref by ISSN) : https://api.crossref.org/journals/0031-9007/works
"""

import calendar
import html
import json
import re
import ssl
import sys
import time
import concurrent.futures
from datetime import datetime, timedelta
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET

import feedparser

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 跨源抓取时统一限流，避免触发反爬 / 被限流
_REQUEST_GAP = 1.0
_last_req = 0.0


# SSL 上下文：优先用 certifi 提供的 CA 包（解决托管 Python 缺少系统证书导致的
# "unable to get local issuer certificate"）；certifi 不可用时回退系统默认上下文。
_SSL_CTX = None


def _ssl_context():
    global _SSL_CTX
    if _SSL_CTX is not None:
        return _SSL_CTX
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        ctx = ssl.create_default_context()
    _SSL_CTX = ctx
    return ctx


def http_get(url, timeout=25):
    """返回 (status, data)。任何异常都吞掉，返回 (-1, 错误信息)。

    健壮性增强：
      - https 请求使用 certifi CA 包做证书校验，避免托管环境缺少系统证书而误报失败；
      - 若 https 因 SSL/连接问题失败，自动用 http:// 重试一次（部分站点证书过期或无 https）。
    回退仅在失败时触发，不影响本就正常的 https 源。
    """
    global _last_req
    now = time.time()
    if now - _last_req < _REQUEST_GAP:
        time.sleep(_REQUEST_GAP - (now - _last_req))
    _last_req = time.time()

    def _open(u):
        req = urllib.request.Request(u, headers={"User-Agent": UA})
        if u.startswith("https://"):
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
                return r.status, r.read().decode("utf-8", "replace")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")

    try:
        return _open(url)
    except urllib.error.HTTPError as e:
        return e.code, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        if url.startswith("https://"):
            try:
                return _open("http://" + url[8:])
            except urllib.error.HTTPError as e2:
                return e2.code, f"HTTP {e2.code}"
            except Exception as e2:  # noqa: BLE001
                return -1, f"{type(e2).__name__}: {e2}"
        return -1, f"{type(e).__name__}: {e}"


def _clean(t):
    if not t:
        return ""
    return html.unescape(str(t)).strip()


def clean_html(t):
    """去除 HTML 标签并反转义，得到纯文本（用于 RSS/Atom 摘要里混入的 <p>/<a> 等标签）。"""
    if not t:
        return ""
    t = html.unescape(str(t))
    t = re.sub(r"<[^>]+>", "", t)
    return " ".join(t.split())


# ---------------------------------------------------------------------------
# 中文翻译（Google 非官方 client=gtx 接口，无需 key）。
# 供含英文内容的卡片（Hugging Face / Hacker News / arXiv 等一手源）服务端翻译后烤进 HTML。
# 失败优雅降级（返回空串），绝不因翻译故障拖垮整页。
# ---------------------------------------------------------------------------

TRANSLATE_ENDPOINT = "https://translate.googleapis.com/translate_a/single"


def translate_to_zh(text: str, sl: str = "en", tl: str = "zh-CN", timeout: int = 20) -> str:
    """Google 非官方翻译接口（client=gtx，无需 key）。失败返回空串。"""
    if not text or not text.strip():
        return ""
    try:
        url = (TRANSLATE_ENDPOINT + "?client=gtx&sl=" + sl + "&tl=" + tl
               + "&dt=t&q=" + urllib.parse.quote(text))
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return "".join(seg[0] for seg in data[0] if seg and seg[0])
    except Exception as e:  # noqa: BLE001
        print(f"[sources] WARN 翻译失败（{len(text)} 字符）: {e}", file=sys.stderr)
        return ""


def translate_batch(texts: list, max_workers: int = 10, timeout: int = 20) -> list:
    """并发翻译一批文本（默认 10 线程），返回与输入等长的译文列表。"""
    out = [""] * len(texts)

    def work(idx, t):
        return idx, translate_to_zh(t, timeout=timeout)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(work, i, t) for i, t in enumerate(texts)]
        for fu in concurrent.futures.as_completed(futs):
            try:
                idx, z = fu.result()
                out[idx] = z
            except Exception:  # noqa: BLE001
                pass
    return out


def attach_cn(items: list, title_key: str = "title", summary_key: str = "summary") -> int:
    """就地给 items 加 zh_title / zh_summary 字段（一次性批量翻译，标题+摘要用 \\n\\n 拼接一次调用）。

    返回成功翻译出中文标题的条目数。单个条目翻译失败不影响其他。
    """
    if not items:
        return 0
    texts = []
    for it in items:
        t = it.get(title_key, "") or ""
        s = it.get(summary_key, "") or ""
        texts.append(f"{t}\n\n{s}" if s else t)
    zhs = translate_batch(texts)
    ok = 0
    for it, z in zip(items, zhs):
        if "\n\n" in z:
            it["zh_title"], it["zh_summary"] = z.split("\n\n", 1)
        else:
            it["zh_title"], it["zh_summary"] = z, ""
        if it.get("zh_title"):
            ok += 1
    return ok


# ---------------------------------------------------------------------------
# AI 新闻一手源
# ---------------------------------------------------------------------------

def fetch_hf_papers(date_str=None, max_n=12):
    """Hugging Face 每日热门论文。date_str='YYYY-MM-DD'，空则取最新一期。"""
    url = "https://huggingface.co/api/daily_papers"
    if date_str:
        url += f"?date={date_str}"
    st, data = http_get(url)
    if st != 200:
        return []
    try:
        j = json.loads(data)
    except Exception:  # noqa: BLE001
        return []
    # 指定日期无数据 → 回退到最新一期
    if not j and date_str:
        st, data = http_get("https://huggingface.co/api/daily_papers")
        if st == 200:
            try:
                j = json.loads(data)
            except Exception:  # noqa: BLE001
                j = []
    out = []
    for it in j[:max_n]:
        p = it.get("paper", {}) or {}
        pid = p.get("id") or it.get("id")
        if not pid:
            continue
        title = _clean(p.get("title"))
        if not title:
            continue
        up = it.get("upvotes")
        out.append({
            "title": title,
            "url": f"https://huggingface.co/papers/{pid}",
            "summary": _clean(p.get("summary"))[:400],
            "source": "Hugging Face",
            "extra": f"▲ {up}" if up else "",
            "date": _clean(it.get("submittedOnDailyAt") or p.get("publishedAt"))[:10],
        })
    return out


def fetch_hn_ai(queries=("AI", "LLM", "deep learning", "machine learning"),
                per=20, max_n=14, max_age_days=2):
    """Hacker News 上与 AI 相关的近期讨论（多关键词合并去重）。
    使用 search_by_date 端点按时间倒序抓取，天然偏新；再叠加 max_age_days 过滤做保险，
    避免任何超过 N 天的陈旧老帖混入（普通 search 按热度排序会把几年前的旧热帖顶到前面）。
    """
    seen = set()
    out = []
    cutoff = datetime.now() - timedelta(days=max_age_days)
    for q in queries:
        url = ("https://hn.algolia.com/api/v1/search_by_date?tags=story&query="
               + urllib.parse.quote(q) + f"&hitsPerPage={per}")
        st, data = http_get(url)
        if st != 200:
            continue
        try:
            j = json.loads(data)
        except Exception:  # noqa: BLE001
            continue
        for h in j.get("hits", []):
            u = h.get("url") or ""
            k = u or h.get("title", "")
            if not k or k in seen:
                continue
            seen.add(k)
            # 时间过滤：只保留最近 max_age_days 天内的讨论
            created = h.get("created_at", "")
            if created:
                try:
                    dt = datetime.strptime(created[:19], "%Y-%m-%dT%H:%M:%S")
                except Exception:  # noqa: BLE001
                    dt = None
                if dt is not None and dt < cutoff:
                    continue
            title = _clean(h.get("title"))
            if not title:
                continue
            out.append({
                "title": title,
                "url": u or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                "summary": "",
                "source": "Hacker News",
                "extra": f"▲ {h.get('points', 0)} · 💬 {h.get('num_comments', 0)}",
                "date": _clean(created)[:10],
            })
            if len(out) >= max_n:
                break
        if len(out) >= max_n:
            break
    return out


def fetch_arxiv_cs(cats=("cs.AI", "cs.LG", "cs.CL"), per=4, max_n=14):
    """arXiv 上最新的 AI/ML 论文（cs.AI / cs.LG / cs.CL）。"""
    out = []
    for cat in cats:
        url = ("http://export.arxiv.org/api/query?search_query=cat:" + cat +
               "&sortBy=submittedDate&sortOrder=descending&max_results=" + str(per))
        st, data = http_get(url, timeout=30)
        if st != 200:
            continue
        try:
            ns = {"a": "http://www.w3.org/2005/Atom",
                  "arxiv": "http://arxiv.org/schemas/atom"}
            root = ET.fromstring(data)
            for e in root.findall("a:entry", ns):
                title = _clean(e.findtext("a:title", "", ns))
                if not title:
                    continue
                aid_raw = e.findtext("a:id", "", ns)
                aid = aid_raw.split("/abs/")[-1]
                out.append({
                    "title": title,
                    "url": f"https://arxiv.org/abs/{aid}",
                    "summary": _clean(e.findtext("a:summary", "", ns))[:400],
                    "source": "arXiv",
                    "extra": f"[{cat}]",
                    "date": _clean(e.findtext("a:published", "", ns))[:10],
                })
                if len(out) >= max_n:
                    break
        except Exception:  # noqa: BLE001
            continue
    return out


# ---------------------------------------------------------------------------
# 物理论文：前沿期刊源
# ---------------------------------------------------------------------------

def fetch_rss(url, source_name, max_n=10):
    """通用 RSS/Atom 解析（feedparser 通吃 RSS 1.0/2.0/Atom）。"""
    st, data = http_get(url)
    if st != 200:
        return []
    try:
        d = feedparser.parse(data)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for e in d.entries[:max_n]:
        title = clean_html(e.get("title"))
        if not title:
            continue
        link = e.get("link") or ""
        summ = ""
        if e.get("summary"):
            summ = clean_html(e.get("summary"))[:400]
        elif e.get("content"):
            try:
                summ = clean_html(e["content"][0].get("value", ""))[:400]
            except Exception:  # noqa: BLE001
                pass
        # 优先用 feedparser 已解析好的时间结构（可靠、可排序），
        # 回退到原始字符串截断（少数源无 *_parsed 时）。
        date = ""
        ts = 0
        parsed = (e.get("published_parsed") or e.get("updated_parsed")
                  or e.get("created_parsed"))
        if parsed:
            try:
                ts = calendar.timegm(parsed)  # struct_time 为 UTC
                date = time.strftime("%Y-%m-%d %H:%M", parsed)
            except Exception:  # noqa: BLE001
                ts, date = 0, ""
        if not date:
            for f in ("published", "updated", "created"):
                if e.get(f):
                    date = _clean(e[f])[:16]
                    break
        out.append({
            "title": title, "url": link, "summary": summ,
            "source": source_name, "extra": "", "date": date, "ts": ts,
        })
    return out


def fetch_prl_crossref(max_n=10):
    """PRL 最新正式发表（Crossref，按 PRL 的 ISSN 0031-9007 取最近发表）。"""
    url = ("https://api.crossref.org/journals/0031-9007/works?sort=published"
           "&order=desc&rows=" + str(max_n) +
           "&select=title,published,DOI,URL,author")
    st, data = http_get(url)
    if st != 200:
        return []
    try:
        j = json.loads(data)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for it in j.get("message", {}).get("items", []):
        ts = it.get("title", [""])
        t = ts[0] if ts else ""
        if not t:
            continue
        doi = it.get("DOI", "")
        out.append({
            "title": _clean(t),
            "url": f"https://doi.org/{doi}" if doi else it.get("URL", ""),
            "summary": "",
            "source": "PRL",
            "extra": "Physical Review Letters",
            "date": str(it.get("published", {}).get("date-parts", [[""]])[0][0]),
        })
    return out


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def dedup(items, keyfn=None):
    """按 url（或 title）去重，保持原有顺序。"""
    seen = set()
    out = []
    for it in items:
        k = keyfn(it) if keyfn else (it.get("url") or it.get("title"))
        if k and k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out
