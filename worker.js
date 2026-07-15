/**
 * auto-web Cloudflare Worker
 * --------------------------------------------------------------------------
 * 作用：为「综合中文热点」页提供在线刷新能力。
 *   - 普通请求（静态资源）转发给 Static Assets 绑定（env.ASSETS）。
 *   - GET /api/hotnews  ← 浏览器端的「立即刷新」按钮调用。
 *       服务端并行抓取 6 个热榜源（无 CORS 限制），归一化为统一 JSON 返回。
 *       国际热点标题在服务端用 Google 翻译（client=gtx，无需 key）翻译成中文。
 * 部署：wrangler.jsonc 已设 main: worker.js；git push 或 wrangler deploy 上线。
 */

const UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36";
const TIMEOUT = 12000;

// 与 gen_hotnews.py 的 SOURCES 保持一致
const SOURCES = [
  {
    id: "baidu", name: "百度热搜", emoji: "🔍", anchor: "baidu",
    url: "https://top.baidu.com/api/board?platform=wise&tab=realtime",
    headers: { Referer: "https://top.baidu.com/" },
    parse: parseBaidu,
  },
  {
    id: "weibo", name: "微博热搜", emoji: "🔥", anchor: "weibo",
    url: "https://60s-api.viki.moe/v2/weibo",
    headers: {},
    parse: parseWeibo,
  },
  {
    id: "toutiao", name: "今日头条热榜", emoji: "📰", anchor: "toutiao",
    url: "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
    headers: { Referer: "https://www.toutiao.com/" },
    parse: parseToutiao,
  },
  {
    id: "bili", name: "B站热门", emoji: "📺", anchor: "bili",
    url: "https://api.bilibili.com/x/web-interface/popular?ps=20",
    headers: { Referer: "https://www.bilibili.com/" },
    parse: parseBili,
  },
  { id: "intl", name: "国际热点", emoji: "🌍", anchor: "intl", bilingual: true },
  { id: "news", name: "官方新闻", emoji: "📰", anchor: "news", expandable: true },
];

const GOOGLE_NEWS_URL = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en";
const HN_URL = "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=40";
const CHINANEWS_URL = "https://www.chinanews.com.cn/rss/scroll-news.xml";

// ---------------- 网络 ----------------
async function fetchText(url, extraHeaders) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const r = await fetch(url, {
      headers: { "User-Agent": UA, ...(extraHeaders || {}) },
      signal: ctrl.signal,
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.text();
  } finally {
    clearTimeout(timer);
  }
}

async function fetchJson(url, extraHeaders) {
  const txt = await fetchText(url, extraHeaders);
  return JSON.parse(txt);
}

// ---------------- 解析器（移植自 gen_hotnews.py） ----------------
function parseBaidu(d) {
  const out = [];
  try {
    const entries = d.data.cards[0].content[0].content;
    for (const e of entries) {
      const word = (e.word || "").trim();
      if (!word) continue;
      out.push({ title: word, url: e.url || "", hot: "", extra: e.isTop ? "🔝" : "" });
    }
  } catch (_) {}
  return out;
}

function parseWeibo(d) {
  const out = [];
  for (const e of d.data || []) {
    const title = (e.title || "").trim();
    if (!title) continue;
    out.push({ title, url: e.link || "", hot: fmtHot(e.hot_value), extra: "" });
  }
  return out;
}

function parseToutiao(d) {
  const out = [];
  for (const e of d.data || []) {
    const title = (e.Title || "").trim();
    if (!title) continue;
    out.push({
      title,
      url: e.Url || "",
      hot: fmtHot(e.HotValue),
      extra: e.Label || "",
    });
  }
  return out;
}

function parseBili(d) {
  const out = [];
  for (const e of (d.data || {}).list || []) {
    const title = (e.title || "").trim();
    if (!title) continue;
    const bvid = e.bvid || "";
    const url = bvid
      ? "https://www.bilibili.com/video/" + bvid
      : e.short_link_v2 || "";
    const owner = ((e.owner || {}).name) || "";
    const views = fmtHot((e.stat || {}).view);
    out.push({
      title,
      url,
      hot: views ? views + "播放" : "",
      extra: owner ? "@" + owner : "",
    });
  }
  return out;
}

function parseHn(d) {
  const out = [];
  for (const h of d.hits || []) {
    const title = (h.title || "").trim();
    if (!title) continue;
    out.push({
      title,
      url: h.url || h.story_url || "",
      hot: fmtHot(h.points),
      extra: "",
    });
  }
  return out;
}

// 轻量 RSS 解析（Worker 无 DOMParser）：按 <item> 切块，提取字段
function parseRssItems(xml) {
  const items = [];
  const blockRe = /<item>([\s\S]*?)<\/item>/gi;
  let m;
  while ((m = blockRe.exec(xml))) {
    const block = m[1];
    const get = (tag) => {
      const r = new RegExp("<" + tag + "[^>]*>([\\s\\S]*?)</" + tag + ">", "i").exec(
        block
      );
      return r ? decodeXml(r[1]) : "";
    };
    const title = get("title").trim();
    if (!title) continue;
    items.push({
      title,
      link: get("link"),
      description: get("description"),
      pubDate: get("pubDate"),
    });
  }
  return items;
}

function parseGoogleNews(xml) {
  return parseRssItems(xml).map((it) => ({
    title: it.title,
    url: it.link,
    hot: "",
    extra: "",
  }));
}

function parseChinanews(xml) {
  return parseRssItems(xml).map((it) => ({
    title: it.title,
    url: it.link,
    summary: cleanHtml(it.description).slice(0, 300),
    published: it.pubDate,
  }));
}

// ---------------- 工具 ----------------
function fmtHot(n) {
  n = parseInt(n, 10);
  if (!n || isNaN(n)) return "";
  if (n >= 1e8) return (n / 1e8).toFixed(1) + "亿";
  if (n >= 1e4) return (n / 1e4).toFixed(1) + "万";
  return String(n);
}

const TAG_RE = /<[^>]+>/g;
function cleanHtml(html) {
  if (!html) return "";
  let txt = String(html).replace(TAG_RE, " ");
  txt = txt
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, " ")
    .replace(/&#8230;/g, "…");
  return txt.replace(/\s+/g, " ").trim();
}

function decodeXml(s) {
  if (!s) return "";
  // 处理 CDATA
  s = s.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1");
  return cleanHtml(s);
}

function fmtPubdate(s) {
  if (!s) return "";
  const dt = new Date(s);
  if (isNaN(dt.getTime())) return "";
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(dt);
  } catch (_) {
    return "";
  }
}

async function translate(text, sl = "en", tl = "zh-CN") {
  if (!text) return "";
  try {
    const url =
      "https://translate.googleapis.com/translate_a/single?client=gtx&sl=" +
      sl +
      "&tl=" +
      tl +
      "&dt=t&q=" +
      encodeURIComponent(text);
    const r = await fetch(url, { headers: { "User-Agent": UA } });
    const d = await r.json();
    return (d[0] || []).map((seg) => (seg && seg[0] ? seg[0] : "")).join("");
  } catch (_) {
    return "";
  }
}

// ---------------- 单源加载 ----------------
async function loadSource(src) {
  try {
    if (src.id === "intl") {
      let note = "";
      try {
        const xml = await fetchText(GOOGLE_NEWS_URL);
        const items = parseGoogleNews(xml);
        if (items.length) return { items, note: "" };
        note = "GoogleNews 返回空";
      } catch (e) {
        note = "GoogleNews 失败(" + e.message + ")";
      }
      try {
        const d = await fetchJson(HN_URL);
        const items = parseHn(d);
        if (items.length) return { items, note: note + "，已回退 Hacker News" };
      } catch (e) {
        note += "；HN 也失败(" + e.message + ")";
      }
      return { items: [], note };
    }
    if (src.id === "news") {
      const xml = await fetchText(CHINANEWS_URL);
      const items = parseChinanews(xml);
      return { items, note: items.length ? "" : "中新网返回空" };
    }
    const d = await fetchJson(src.url, src.headers);
    const items = src.parse(d);
    return { items, note: items.length ? "" : "接口返回空列表" };
  } catch (e) {
    return { items: [], note: e.name + ": " + e.message };
  }
}

// ---------------- /api/hotnews ----------------
let _cache = null;
let _cacheAt = 0;
const CACHE_TTL = 60000; // 60s 内复用，避免每次点击都打上游

async function buildResponse() {
  const results = await Promise.all(
    SOURCES.map(async (src) => {
      const { items, note } = await loadSource(src);
      const ok = items.length > 0;
      if (src.bilingual && items.length) {
        await Promise.all(
          items.map(async (it) => {
            it.zh = await translate(it.title);
          })
        );
      }
      return {
        id: src.id,
        name: src.name,
        emoji: src.emoji,
        anchor: src.anchor,
        bilingual: !!src.bilingual,
        expandable: !!src.expandable,
        ok,
        error: ok ? "" : note || "无数据",
        items,
      };
    })
  );

  const updated = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());

  const body = JSON.stringify({ updated, sources: results });
  return new Response(body, {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "access-control-allow-origin": "*",
    },
  });
}

async function handleHotnews() {
  const now = Date.now();
  if (_cache && now - _cacheAt < CACHE_TTL) return _cache;
  _cache = await buildResponse();
  _cacheAt = now;
  return _cache;
}

// ---------------- 入口 ----------------
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/api/hotnews") {
      if (request.method !== "GET") {
        return new Response("Method Not Allowed", { status: 405 });
      }
      return handleHotnews();
    }
    // 其余路径：托管静态资源
    return env.ASSETS.fetch(request);
  },
};
