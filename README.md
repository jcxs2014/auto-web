# 观象 · Skyscope

个人静态门户站点，纯前端、零构建、零外部依赖。

## 简介

`观象 · Skyscope` 是一个自包含的静态网页门户，作为多个自动化信息面板的统一入口。当前包含五大板块：

| 板块 | 路径 | 内容 |
|------|------|------|
| **AIHot 每日科技日报** | `aihot/` | 每日自动抓取的科技/AI 资讯聚合仪表盘 |
| **arXiv 物理论文** | `arxiv-physics/` | arXiv 物理方向最新论文索引仪表盘 |
| **综合中文热点** | `hotnews/` | 实时聚合的中文热点资讯 |
| **科技新闻** | `news/` | 综合科技新闻聚合 |
| **RSS 订阅阅读器** | `rss/` | 自维护订阅源（72 源）的整页文章阅读器，支持翻译 |

入口页 `index.html` 以卡片形式链接到上述子页，CSS/JS 全部内联，子页通过相对路径引用，部署后不会断链。

## 部署背景（重要）

> Cloudflare 自 2025-04 起将 **Pages 列入维护模式（deprecated）**，新控制台默认只提供 "Create a Worker"。本仓库改用官方主推的 **Workers + Static Assets** 方案部署，功能等价、未来可持续维护。
>
> 部署配置见 `wrangler.jsonc`（仓库根），详细步骤见 `DEPLOY.md`。线上地址：**https://auto-web.jcxs2014.workers.dev**

## 目录结构

```
auto-web/
├── index.html                      # 门户首页（卡片入口，更新日期由 CI 每小时自动刷新）
├── aihot/                          # AIHot 每日科技日报
│   ├── aihot_daily_latest.html     # 最新日报
│   ├── aihot_daily_2026-07-16.html # 历史日报（按日期）
│   └── archive.html                # 归档索引
├── arxiv-physics/                  # arXiv 物理论文
│   ├── arxiv_physics_latest.html
│   ├── arxiv_physics_2026-07-16.html
│   └── archive.html
├── hotnews/                        # 综合中文热点
│   ├── index.html
│   ├── hotnews_2026-07-16.html
│   └── archive.html
├── news/                           # 科技新闻
│   ├── index.html
│   ├── news_2026-07-16.html
│   └── archive.html
├── rss/                            # RSS 订阅阅读器（订阅配置在 feeds.json）
│   ├── index.html                  # 阅读器页面
│   ├── data/                       # 各源最新内容快照（CI 生成）
│   ├── feeds.json                  # 订阅源列表
│   └── feed_health.json            # 源健康状态（CI 生成）
├── generators/                     # 各仪表盘生成器（Python）
├── .github/workflows/              # CI：aihot / arxiv / hot / news / rss / index（每小时或每日）
├── wrangler.jsonc                  # Cloudflare Workers + Static Assets 配置
├── sync.sh                         # 每日自动同步脚本（提交 + 推送 + 部署）
├── manifest.webmanifest           # PWA 清单
├── .gitignore
├── README.md
└── DEPLOY.md
```

## 技术特性

- **纯静态**：所有样式与脚本内联，无需打包工具或运行时依赖
- **零构建**：源文件即部署文件，CI 构建命令留空
- **自包含**：相对路径引用，可整体拷贝到任意静态托管

## 本地更新流程

```bash
cd /Users/jcxs2014/Sites/Workbuddy/auto-web
# 修改内容后
git add -A
git commit -m "update content"
git push                              # 方式 B（Git 集成）自动部署
# 方式 A（CLI）则改跑：wrangler deploy
```

每日内容由定时任务刷新后，可直接运行 `bash sync.sh` 完成"提交 → 推送 → 部署"全流程（详见 `DEPLOY.md` 的自动更新流水线一节）。

## 仓库

- GitHub: https://github.com/jcxs2014/auto-web
- 默认分支：`main`
- 线上站点：https://auto-web.jcxs2014.workers.dev
