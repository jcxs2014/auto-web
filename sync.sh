#!/usr/bin/env bash
#
# auto-web 自动同步脚本
# 在每日定时任务生成 HTML 后运行：提交到 Git 并部署到 Cloudflare
#
# 前置条件（本机一次性配置）：
#   - SSH key 已加入 GitHub，且 push 时无密码短语交互
#     （若 key 有密码短语，需用 ssh-agent 或改用无密码 key，否则 cron/launchd 会卡住）
#   - 已执行 `wrangler login` 缓存部署令牌
#
# 用法：
#   bash sync.sh
# 或在每日生成任务末尾追加：
#   bash /Users/jcxs2014/Sites/Workbuddy/auto-web/sync.sh

set -euo pipefail

# 切换到脚本所在目录（auto-web 仓库根）
cd "$(dirname "$0")"

echo "==> [$(date '+%Y-%m-%d %H:%M:%S')] auto-web 同步开始"

# 1. 仅在有内容变更时提交
if git diff --quiet && git diff --cached --quiet; then
  echo "==> 无内容变更，跳过"
  exit 0
fi

git add -A
git commit -m "auto: update content $(date '+%Y-%m-%d')"

# 2. 推送到 GitHub（保留完整版本历史）
git push origin main

# 3. 部署到 Cloudflare：优先用本地 wrangler 部署（含 worker.js 动态代理），
#    若未安装/未登录则退回依赖 Git 集成（git push 已自动上线）。两步均幂等无害。
if command -v wrangler >/dev/null 2>&1; then
  wrangler deploy
elif command -v npx >/dev/null 2>&1; then
  echo "==> 尝试 npx wrangler deploy（未登录会自动失败，可忽略）"
  npx --yes wrangler deploy || echo "==> wrangler deploy 未执行（需先 wrangler login）；worker 依赖 Cloudflare Git 集成自动上线"
else
  echo "==> 未检测到 wrangler/npx，跳过 worker 部署（纯静态由 git push 上线）"
fi

echo "==> [$(date '+%Y-%m-%d %H:%M:%S')] auto-web 同步完成"
