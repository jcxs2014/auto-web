# 长期项目记忆（auto-web 静态资讯站 · 观象 · Skyscope）

> 本文件随仓库走，任何设备 clone 后 WorkBuddy 均可读。约定与具体机器无关。

## 品牌名
- 站点品牌名：**观象 · Skyscope**（2026-07-19 由 auto-web 改名）。
- 用户可见品牌字样在 `index.html`（title/kicker `⚡ SKYSCOPE`/h1 `观象`/footer/apple-mobile-web-app-title）+ `manifest.webmanifest`（name/short_name `观象`/description）+ `README.md` 标题首段。
- 基础设施名保留 auto-web 不改：域名 `auto-web.jcxs2014.workers.dev`、GitHub repo `jcxs2014/auto-web`、wrangler 部署名。
- 各仪表盘页（CI 产物）不含品牌字样（footer 是各自文案），无需改生成器。

## 仓库与部署
- 站点托管：auto-web.jcxs2014.workers.dev（Cloudflare Workers），产物由 GitHub Actions 定时重建。
- 本地仓库路径随设备而变（佩鲁贾机为 `/Users/jcxs2014/Sites/Workbuddy/auto-web`），以实际 clone 位置为准。
- **多设备策略**：Mac mini 与 MacBook 各自独立 clone，互不 rsync 目录；必要时经 Tailscale 远程桌面操作另一台。git 是唯一的合并层。

## garss 模型（铁律）
- 生成产物（rss/index.html、各仪表盘 html、arxiv-physics/*、news/*、hotnews/*、aihot/* 的 dated/latest/archive）= CI 独占提交。
- 人工只提交：generators/ 生成器、rss/feeds.json、index.html 手写部分、文档。改完本地跑生成器验证后务必 `git checkout` 回退产物再 commit。
- ⚠️ 生成器每新增一个「写出的文件」，必须同步把该路径加入对应 `.github/workflows/<x>.yml` 的 `sync.sh` 参数列表（如 rss.yml 的 `bash sync.sh rss rss/index.html rss/translate.html rss/data rss/feed_health.json`）。否则 CI 生成了却从不 commit，线上 404。translate.html 已踩此坑（d082109 修复）。

## 五大仪表盘 + 生成器
- arxiv-physics：gen_arxiv_physics.py（arXiv 物理 + 前沿期刊区）。
- hotnews：gen_hotnews.py（中文热榜，mode=hot）。
- news：gen_hotnews.py（国际新闻，mode=news，含 freshen_news 7天时效过滤 + 标题翻译）。
- rss：gen_rss_reader.py（读 rss/feeds.json，全文 trafilatura 抽取）。
- aihot：gen_aihot_daily.py。
- 索引：gen_dashboard_index.py（每小时 CI 刷新各卡片 updated 日期）。

## 首页天气卡片
- 位置：手写 `index.html`（**非生成器产物**），`gen_dashboard_index.py` 只替换 DASHBOARDS 的 `updated` 字段、不会冲刷它。
- 首页结构：顶部 `.top-nav`（左=品牌 `⚡ SKYSCOPE`+`观象`+副标题，右=主题切换）；其下依次为天气卡片、仪表盘网格。
- 天气数据源 Open-Meteo（免费、免 key、CORS 友好），前端 fetch `current`+`daily`；6 城市（佩鲁贾/北京/合肥/广州/榆林/南昌）硬编码经纬度，点击切换 + localStorage 记忆（key=`weather-city-idx`，默认佩鲁贾）；每 10 分钟刷新；WMO weather_code 映射中文+emoji；当地时钟按城市时区（`Intl.DateTimeFormat(timeZone)`，佩鲁贾 Europe/Rome、其余 Asia/Shanghai）每秒走字。
- 国内访问 Open-Meteo（欧洲服务器）可能慢，有降级提示；`file://` 直开时 fetch 被拦，须用本地服务器/线上站点/桌面 app。

## 关键坑（已解决，勿回退）
- 斜体/加粗泄漏：trafilatura include_formatting 会乱加 <i>/<strong> 且 <i> 常不闭合，污染整站；已从 sources_util 白名单剥 i/em/cite/var/dfn 且关闭 include_formatting。
- 翻译：整页 lang=zh-CN 压制浏览器原生翻译（元素级 lang 无效，须文档级）。英文源靠 feeds.json 的 lang 字段落到正文 DOM；点「🌐 翻译」打开站内 `rss/translate.html?src=<源slug>&id=<文章url>`（`<html lang="en">`、真实 https URL），该页 fetch `rss/data/<slug>.json` 按 url 匹配正文渲染。**关键坑：Chrome 在 PWA `display:standalone`（整站装成桌面 app）下会抑制原生「翻译此页」弹窗**。故 translate.html 内嵌 Google 翻译条（正文 fetch 完成后 `loadGoogle()` 初始化；`pageLanguage:'en'`）+ Bing 文本兜底（`cn.bing.com/translator?text=<正文纯文本>`）；Google 3.5s 未注入则高亮 Bing 按钮作国内兜底。导航栏 Google 元素仍保留（`pageLanguage:"zh-CN"` + Bing URL 兜底），但首页中英混排下对英文文章段无效——正文翻译以 translate.html 为准。
- ⚠️ **translate.html 是「生成器产物」非手写静态文件**：`gen_rss_reader.py` 的 `TRANSLATE_HTML.write_text(TRANSLATE_TEMPLATE, ...)` 每次运行都会**覆盖** `rss/translate.html`。改它必须改 `gen_rss_reader.py` 里的 `TRANSLATE_TEMPLATE` 常量（手写 translate.html 会被下次生成冲掉）。
- ⚠️ 已回退的误判方案（勿复用）：曾误判「保存到桌面不翻译」是 `file://` 另存为导致本地 fetch 拦截，加了「📥 保存离线版」按钮；后澄清场景是**整站装成桌面 app（PWA `standalone`）**而非单页 `file://` 另存，已 revert。真正根因是 standalone 抑制原生翻译。`file://` 另存不是主场景，不要再为此改代码。
- gen_hotnews 主循环必须用 `if/elif/else` 区分「有 loader 的源」与「有 url+parser 的源」：国际热点(intl)用 loader、没有 `url` 键，若误写成两个独立 `if` 的 `else` 去 `fetch_json(src['url'])` 会触发 `KeyError: 'url'`（2026-07-21 修复）。
- 死源：corriere(冻2024)/xinhua_en(死2018) 已换 France24/NHK；加任何源前务必探针验证可达。

## 加订阅源标准流程
1. 用 feedparser+certifi 探针（看 entries 数、bozo），排除 404/410/被墙。
2. RSS 阅读器：直接往 rss/feeds.json 加条目（带 lang=en 触发翻译按钮）。
3. 新闻页：gen_hotnews.py 加 fetch_*（复用 fetch_rss_news）+ NEWS_SOURCES + PER_PLATFORM。
4. 前沿期刊：gen_arxiv_physics.py 的 journal_feeds 加 (url, name)。
