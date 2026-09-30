#!/usr/bin/env bash
# PDF 工具箱 —— 一键回归测试。
#
# 三套测试各管一段，缺一不可：
#   1) --selftest   核心链路（界面/字体/拼版/图层/水印/打印），无界面即可跑
#   2) test_e2e.py  四条主链路的端到端断言 + 异常边界（真实文件，不打桩）
#   3) test_layers.py  图层列表 / 双击切换预览 / 打印目标优先级的交互行为
#
# 用 offscreen 平台跑，无需真实显示器，CI 与本地一致。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(dirname "$HERE")"
PY="${PY:-$PROJ/.venv-sys/bin/python}"

if [[ ! -x "$PY" ]]; then
    echo "找不到 Python 解释器：$PY"
    echo "可用 PY=/path/to/python 覆盖。"
    exit 1
fi

export QT_QPA_PLATFORM=offscreen
cd "$PROJ"

status=0
run() {
    local title="$1"; shift
    echo
    echo "=============================================================="
    echo "  $title"
    echo "=============================================================="
    if "$@"; then
        echo "  → 通过"
    else
        echo "  → 失败"
        status=1
    fi
}

run "1/3 核心链路自检" "$PY" main.py --selftest
run "2/3 端到端功能测试" "$PY" "$HERE/test_e2e.py"
run "3/3 图层与预览交互测试" "$PY" "$HERE/test_layers.py"

echo
if [[ "$status" -eq 0 ]]; then
    echo "ALL TESTS PASSED"
else
    echo "SOME TESTS FAILED"
fi
exit "$status"
