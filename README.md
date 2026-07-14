# auto-web

个人静态门户站点，纯前端、零构建、零外部依赖。

## 简介

`auto-web` 是一个自包含的静态网页门户，作为多个自动化信息面板的统一入口。当前包含两大板块：

| 板块 | 路径 | 内容 |
|------|------|------|
| **AIHot 每日科技日报** | `aihot/` | 每日自动抓取的科技/AI 资讯聚合仪表盘 |
| **arXiv 物理论文** | `arxiv-physics/` | arXiv 物理方向最新论文索引仪表盘 |

入口页 `index.html` 以卡片形式链接到上述子页，CSS/JS 全部内联，子页通过相对路径引用，部署后不会断链。

## 目录结构

```
auto-web/
├── index.html                      # 门户首页（卡片入口）
├── aihot/
│   ├── aihot_daily_latest.html     # AIHot 最新日报
│   └── aihot_daily_2026-07-14.html # AIHot 历史日报（按日期）
├── arxiv-physics/
│   ├── arxiv_physics_latest.html   # arXiv 物理最新论文
│   └── arxiv_physics_2026-07-14.html # arXiv 物理历史（按日期）
├── .gitignore
└── README.md
```

## 技术特性

- **纯静态**：所有样式与脚本内联，无需打包工具或运行时依赖
- **零构建**：源文件即部署文件，CI 构建命令留空
- **自包含**：相对路径引用，可整体拷贝到任意静态托管

## 部署

通过 Cloudflare Pages 的 **Git 集成** 自动部署：

1. Cloudflare Pages → Connect to Git → 选择 `jcxs2014/auto-web`
2. 构建设置：**Framework preset = None**、**Build command = 留空**、**Build output directory = `.`**
3. 部署后获得免费域名 `https://auto-web.pages.dev`

### 本地更新流程

```bash
cd /Users/jcxs2014/Sites/Workbuddy/auto-web
# 修改内容后
git add -A
git commit -m "update content"
git push   # 触发 Cloudflare Pages 自动重新部署
```

## 仓库

- GitHub: https://github.com/jcxs2014/auto-web
- 默认分支：`main`
