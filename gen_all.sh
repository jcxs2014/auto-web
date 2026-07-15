#!/usr/bin/env bash
#
# gen_all.sh — 一键生成全部页面并同步上线
# 依次生成：综合热点(hot) → 全球新闻(news) → AI HOT 日报 → arXiv 物理 → RSS 阅读，最后 git 提交推送
#
# 用法：
#   bash /Users/jcxs2014/Sites/Workbuddy/auto-web/gen_all.sh
#
# 单个生成失败不会影响其余页面，但只要有任一 HTML 变更，最后仍会提交并推送。

set -uo pipefail

# 优先使用已安装依赖的托管解释器，缺失时回退到 PATH 中的 python3
PY="/Users/jcxs2014/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
if [ ! -x "$PY" ]; then PY="$(command -v python3 || true)"; fi
if [ -z "$PY" ]; then
  echo "!! 找不到 python3，无法生成页面" >&2
  exit 1
fi

# 相对脚本自身定位（gen_all.sh 位于仓库根，generators/ 在其下），CI 与本地通用
AUTO_WEB="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$(cd "$AUTO_WEB/generators" && pwd)"

# 生成脚本依赖同目录模块，必须切到 scripts 目录运行
cd "$SCRIPTS_DIR" || { echo "!! 无法进入 $SCRIPTS_DIR" >&2; exit 1; }

run_gen() {
  local label="$1"; shift
  echo "==> [$(date '+%Y-%m-%d %H:%M:%S')] 生成 $label"
  if "$PY" "$@"; then
    echo "    ✓ $label 完成"
  else
    echo "    ✗ $label 失败（继续其余页面）" >&2
  fi
}

echo "===== auto-web 全量生成开始 $(date '+%Y-%m-%d %H:%M:%S') ====="
echo "Python: $PY"

run_gen "综合中文热点(hot)"  gen_hotnews.py hot
run_gen "全球新闻(news)"      gen_hotnews.py news
run_gen "AI HOT 每日科技日报"  gen_aihot_daily.py
run_gen "arXiv 物理最新论文"   gen_arxiv_physics.py
run_gen "RSS 阅读订阅"         gen_rss_reader.py

echo "===== 页面生成结束，开始同步 ====="

# 提交并推送到 GitHub（Cloudflare Git 集成自动上线）
bash "$AUTO_WEB/sync.sh"

echo "===== auto-web 全量生成完成 $(date '+%Y-%m-%d %H:%M:%S') ====="
