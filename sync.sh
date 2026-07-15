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
# git pull --rebase。若 rebase 在「生成类文件」上产生冲突，自动采用
# upstream（rebase 中的 ours）版本解决——这些内容由代码确定性生成，
# upstream 始终是较新基准，取它即可保证页面正确；即便偶发保留了稍旧
# 的版本，下一次定时重跑也会重新生成自愈。

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
git commit -q -m "auto: update $LABEL $(date '+%Y-%m-%d %H:%M')"

# 冲突自动解决：生成类文件一律采用 upstream（rebase 中的 ours）版本。
# 因内容由代码确定性生成，丢弃同名的本地/上游生成文件不会丢信息，
# 且上游始终是较新基准，可保证页面与最新代码一致。
resolve_conflicts() {
  local f
  git diff --name-only --diff-filter=U 2>/dev/null | while IFS= read -r f; do
    [ -z "$f" ] && continue
    echo "==> 冲突自动采用 upstream 版本: $f"
    # rebase 中 ours=upstream(较新基准)，theirs=本次提交的旧生成；取 ours
    git checkout --ours -- "$f" 2>/dev/null || git checkout --theirs -- "$f" 2>/dev/null
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
