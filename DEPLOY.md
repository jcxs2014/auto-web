# 部署说明（Cloudflare Workers + Static Assets）

auto-web 是纯静态站点（零构建、零外部依赖），通过 Cloudflare 托管上线。

> **重要背景**：Cloudflare 自 2025-04 起将 **Pages 列入维护模式（deprecated）**，新控制台默认只提供 "Create a Worker"。本仓库改用官方主推的 **Workers + Static Assets** 方案部署，功能等价、未来可持续维护。

## 配置（已在仓库内）

仓库根目录的 `wrangler.jsonc` 声明本站点为纯静态资源 Worker：

```jsonc
{
  "name": "auto-web",
  "compatibility_date": "2026-07-14",
  "assets": {
    "directory": ".",                // 静态资源目录 = 仓库根（无构建，直接托管）
    "not_found_handling": "404-page" // 多页站用 404-page；勿用 single-page-application
  }
  // 未设置 main 字段 → 纯静态资源 Worker，无服务端 JS
}
```

## 方式 A：CLI 一行部署（最快看到效果）

在本机终端执行：

```bash
npm install -g wrangler
wrangler login          # 浏览器授权一次
cd /Users/jcxs2014/Sites/Workbuddy/auto-web
wrangler deploy
```

部署完成自动分配域名：**`https://auto-web.workers.dev`**

## 方式 B：控制台 Git 集成（保留 git push 自动部署）

1. 登录 [dash.cloudflare.com](https://dash.cloudflare.com) → **Workers & Pages** → **Create application**
2. 选 **Worker**（新版入口，无需找 Pages 标签）→ **Connect to Git**
3. 授权 GitHub，选中仓库 **`jcxs2014/auto-web`**
4. 构建设置：因 `wrangler.jsonc` 已在仓库内定义了 `assets.directory`，**构建命令留空**即可，CF 读取配置上传静态资源
5. 点击 **Deploy** → 之后每次 `git push` 自动重新部署

> 若控制台看不到 Pages 入口，可在地址栏把 `.../workers-and-pages` 改为 `.../pages/new/provider/github` 直达旧 Pages Git 流程（不推荐，Pages 已弃用）。

## 自定义域名

Worker 设置 → **Triggers** → **Custom Domains** 添加：

- 免费二级域（如 `your.eu.org`，需先申请）
- 或购买的顶级域（如 `.top` / `.xyz`，首年几元人民币）

添加后按需配置 DNS（Cloudflare 会给出记录值），站点代码无需改动。

## 本地更新流程

```bash
cd /Users/jcxs2014/Sites/Workbuddy/auto-web
# 修改内容后
git add -A
git commit -m "update content"
git push          # 触发 Cloudflare 自动重新部署（方式 B）
# 方式 A 则改用：wrangler deploy
```

## 自动更新流水线

内容由每日定时任务刷新生成 HTML，之后通过本仓库的 `sync.sh` 自动上线：

```bash
# 在每日生成任务生成 HTML 之后运行
bash /Users/jcxs2014/Sites/Workbuddy/auto-web/sync.sh
```

`sync.sh` 会依次执行：检测变更 → `git commit` → `git push`（保留版本历史）→ `wrangler deploy`（当前方式 A 上线）。无变更时直接退出，避免空部署。

**前置条件（本机一次性配置）**：
- SSH key 已加入 GitHub，且 push 时无密码短语交互（若 key 有密码短语，需用 `ssh-agent` 或改用无密码 key，否则 cron / launchd 会卡住）
- 已执行 `wrangler login` 缓存部署令牌
- 若改用控制台 Git 集成（方式 B），`git push` 即自动部署，`sync.sh` 中的 `wrangler deploy` 可省略（保留亦幂等无害）

**定时调度（可选）**：推荐把 `sync.sh` 串到每日生成任务末尾。若需独立定时，可用 launchd（以下示例每日 03:30，须晚于生成任务完成时间）：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.auto-web.sync</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/jcxs2014/Sites/Workbuddy/auto-web/sync.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>3</integer>
    <key>Minute</key><integer>30</integer>
  </dict>
  <key>WorkingDirectory</key>
  <string>/Users/jcxs2014/Sites/Workbuddy/auto-web</string>
</dict>
</plist>
```

保存为 `~/Library/LaunchAgents/com.auto-web.sync.plist`，加载：`launchctl load ~/Library/LaunchAgents/com.auto-web.sync.plist`。

## Pages vs Workers + Assets 对照

| | Pages（旧 / 维护中） | Workers + Static Assets（新 / 推荐） |
|---|---|---|
| 默认入口 | 原 Pages 标签（已隐藏） | Create application → Worker |
| 域名 | `auto-web.pages.dev` | `auto-web.workers.dev` |
| 配置文件 | 无（控制台填写） | `wrangler.jsonc`（仓库内） |
| Git 自动部署 | ✅ | ✅ |
| 未来维护 | 停止更新 | 官方主推 |

## 仓库

- GitHub: https://github.com/jcxs2014/auto-web
- 默认分支：`main`
