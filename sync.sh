#!/usr/bin/env bash
#
# auto-web 自动同步脚本（按模块提交版）
# 在某一模块的生成脚本跑完后调用：只提交该模块产生的变更并推送，
# 由 Cloudflare Git 集成自动上线。
#
# 用法：
#   bash sync.sh <模块标签>      # 例如 bash sync.sh rss / hotnews / aihot / arxiv
#   bash sync.sh                 # 不带标签时默认标签为 content（本地全量 gen_all.sh 用）
#
# 多模块并行 push 同一分支可能触发 non-fast-forward，因此推送前先
# git pull --rebase，并在失败时重试，保证并发更新互不丢失。

set -uo pipefail

LABEL="${1:-content}"
cd "$(dirname "$0")"

echo "==> [$(date '+%Y-%m-%d %H:%M:%S')] auto-web 同步开始 ($LABEL)"

# 1. 仅在有内容变更时提交
if git diff --quiet && git diff --cached --quiet; then
  echo "==> 无内容变更，跳过 ($LABEL)"
  exit 0
fi

git add -A
git commit -m "auto: update $LABEL $(date '+%Y-%m-%d %H:%M')"

# 2. 推送（带 rebase + 重试，兼容其它模块并发推送）
MAX_TRIES=5
for i in $(seq 1 "$MAX_TRIES"); do
  git pull --rebase --autostash origin main || true
  if git push origin main; then
    echo "==> [$(date '+%Y-%m-%d %H:%M:%S')] 同步完成 ($LABEL)"
    exit 0
  fi
  echo "==> push 冲突，重试 ($i/$MAX_TRIES) ..."
  sleep 10
done

echo "!! 推送失败 ($LABEL)" >&2
exit 1
