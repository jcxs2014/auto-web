"""HTML 消毒工具：用于把外部抓取的文章正文安全地内联进静态页面。

文章正文（来自 trafilatura 等抽取器，或 RSS 全文）可能包含字面
<script>/<style>/<iframe> 等标签（尤其技术类文章会讨论这些标签本身）。
直接内联进页面会让浏览器把正文里的 <script> 当成真脚本标签，吞掉后续真正的
JS（如阅读器 openReader 定义），导致整页「阅读全文」按钮全部失效。

sanitize_html() 用于：
  1) 整段移除成对的 <script>/<style>/<iframe>/<object>/<embed>/<link>/<meta>/<noscript> 块
  2) 把残留的未配对标签字面转义为文本（如文章讨论 «<script> tag»）
  3) 剥离 on* 事件属性与 javascript: 协议链接（防 XSS）
保留 p/a/img/code/pre/blockquote/h1-4/ul/ol/table 等安全排版标签。
"""

import re

_DANGER_BLOCK = re.compile(
    r"<(script|style|iframe|object|embed|link|meta|noscript)\b[^>]*>.*?</\1>",
    re.I | re.S)
_DANGER_OPEN = re.compile(r"<(script|style|iframe|object|embed|link|meta|noscript)\b[^>]*/?>", re.I)
_DANGER_CLOSE = re.compile(r"</(script|style|iframe|object|embed|link|meta|noscript)\s*>", re.I)
_ON_ATTR = re.compile(r"\s+on\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_JS_URI = re.compile(r"(href|src)\s*=\s*(\"javascript:[^\"]*\"|'javascript:[^']*')", re.I)


def sanitize_html(html):
    """移除 HTML 中的危险标签/属性，返回可安全内联的 HTML 字符串。

    若输入为空（None/''/' '）则原样返回，避免把空串变成其它东西。
    """
    if not html:
        return html
    html = _DANGER_BLOCK.sub("", html)             # 配对危险块整段移除
    html = _DANGER_OPEN.sub("&lt;\\1", html)        # 残留未配对开始标签 → 文本
    html = _DANGER_CLOSE.sub("&lt;/\\1&gt;", html)  # 残留未配对结束标签 → 文本
    html = _ON_ATTR.sub("", html)                   # 移除 on* 事件属性
    html = _JS_URI.sub("", html)                    # 移除 javascript: 协议链接
    return html
