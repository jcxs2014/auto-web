#!/usr/bin/env bash
#
# auto-web 自动同步脚本
# 在每日定时任务生成 HTML 后运行：提交到 Git 并推送，由 Cloudflare Git 集成自动上线
#
# 前置条件（本机一次性配置）：
#   - SSH key 已加入 GitHub，且 push 时无密码短语交互
#     （若 key 有密码短语，需用 ssh-agent 或改用无密码 key，否则 cron/launchd 会卡住）
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

# 2. 推送到 GitHub（Cloudflare Git 集成会自动把纯静态内容上线）
git push origin main

echo "==> [$(date '+%Y-%m-%d %H:%M:%S')] auto-web 同步完成"
