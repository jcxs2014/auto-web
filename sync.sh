#!/usr/bin/env bash
#
# auto-web 自动同步脚本（按模块提交版 · CI 独占写生成产物）
#
# 用法：
#   bash sync.sh <模块标签> <产物路径1> [<产物路径2> ...]
#     rss  : bash sync.sh rss rss/index.html rss/data rss/feed_health.json
#     hotnews: bash sync.sh hotnews hotnews news
#     aihot: bash sync.sh aihot aihot
#     arxiv: bash sync.sh arxiv arxiv-physics
#   bash sync.sh            # 不带参数时退化为 git add -A（本地 gen_all.sh 用）
#
# 设计要点（根治推送冲突）：
#   生成产物（html/json）只由 CI 提交，人工只提交代码。每个 workflow 在生成前已
#   `git pull --rebase` 拉到最新代码，这里再用 `git add -f` 仅提交本模块产物，
#   因此与人工的代码提交永不冲突；偶发的 rebase 冲突（如并行运行）一律采用
#   CI 本次新鲜生成的内容（rebase 中的 theirs）解决，保证页面与数据最新。

set -uo pipefail

LABEL="${1:-content}"
shift || true
OUTS=("$@")

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

git config user.email "github-actions[bot]@users.noreply.github.com" 2>/dev/null || true
git config user.name "github-actions[bot]" 2>/dev/null || true

echo "==> [$(date '+%Y-%m-%d %H:%M:%S')] auto-web 同步开始 ($LABEL)"

# 仅提交本模块生成的产物（强制 add，绕过 .gitignore）
if [ ${#OUTS[@]} -eq 0 ]; then
  git add -A
else
  git add -f -- "${OUTS[@]}"
fi

if git diff --cached --quiet; then
  echo "==> 无内容变更，跳过 ($LABEL)"
  exit 0
fi

git commit -q -m "auto: update $LABEL $(date '+%Y-%m-%d %H:%M')"

# 冲突自动解决：生成类文件一律采用 CI 本次新鲜生成的内容（rebase 中 theirs =
# 被 replay 的本地提交，即刚生成的产物），保证页面/数据最新；取不到时回退 ours。
resolve_conflicts() {
  local f
  git diff --name-only --diff-filter=U 2>/dev/null | while IFS= read -r f; do
    [ -z "$f" ] && continue
    echo "==> 冲突自动采用 CI 本次生成版本: $f"
    git checkout --theirs -- "$f" 2>/dev/null || git checkout --ours -- "$f" 2>/dev/null
    git add -- "$f"
  done
  GIT_EDITOR=true git rebase --continue 2>&1 | tail -2 || true
}

MAX_TRIES=6
for i in $(seq 1 "$MAX_TRIES"); do
  # 若上一次 rebase 遗留了冲突态，先解决再尝试推送
  if git diff --name-only --diff-filter=U 2>/dev/null | grep -q .; then
    resolve_conflicts
  fi
  if git push origin main 2>/dev/null; then
    echo "==> [$(date '+%Y-%m-%d %H:%M:%S')] 同步完成 ($LABEL)"
    exit 0
  fi
  # 拉取并 rebase（可能引入冲突）
  git pull --rebase --autostash origin main 2>&1 | tail -3 || true
  # 若 rebase 产生冲突，自动解决后直接尝试推送
  if git diff --name-only --diff-filter=U 2>/dev/null | grep -q .; then
    resolve_conflicts
    if git push origin main 2>/dev/null; then
      echo "==> [$(date '+%Y-%m-%d %H:%M:%S')] 同步完成 ($LABEL)（冲突已自动解决）"
      exit 0
    fi
  fi
  echo "==> 推送重试 ($i/$MAX_TRIES) ..."
  sleep 8
done

# 兜底：若仍卡在冲突态，放弃本次 rebase（保留本地提交，等待下次运行）
if git diff --name-only --diff-filter=U 2>/dev/null | grep -q .; then
  git rebase --abort 2>/dev/null || true
fi
echo "!! 推送失败 ($LABEL)" >&2
exit 1
