#!/bin/bash
# check_repo.sh：把這個 repo 的檢查跑一輪（編譯 → 合成測試 → 重算衍生檔 → 洩密掃描 → email 掃描）。
# 用法：PY=/path/to/python /bin/bash bin/check_repo.sh [--refresh]
#   --refresh：跳過「衍生檔是否已提交最新」這一步（改完程式碼第一次重生檔案時用）；圖也保留為重新產生的版本。
# 要在 git 倉庫（用 git clone 下來的資料夾）裡跑。不是的話，要用 git 的三步（衍生檔是否已提交、機密掃描、email 掃描）
# 會略過並印出原因，最後一行印 CHECK_REPO_PARTIAL。
# 重算出來的文字檔（RESULTS.md、data/cells.csv、data/jumps.csv、data/fit/）要和提交的版本逐位元相同；
# 只比對 git 已追蹤的檔案，多出來的未追蹤輸出（例如自己用別的設定跑 fit 產生的檔）不會讓檢查失敗。
# 圖（charts/*.png）不逐位元比對：matplotlib 版本或字型不同，圖檔的位元組就不同；重生的圖會還原成跑之前的檔案。
# 要逐位元重現文字檔，請用 requirements-lock.txt 的版本（Python 3.12 以上）。本工具目前只在 macOS 驗證過。
# 第一個失敗就結束，結束碼非 0。全部通過時最後一行是 CHECK_REPO_OK。
# 有檢查被略過時，最後一行是 CHECK_REPO_PARTIAL：沒裝 node 或 gitleaks 時結束碼 4；資料夾不是 git 倉庫時結束碼 0。
# 只有最後一行是 CHECK_REPO_OK，才代表全部檢查都跑過、都通過。
# 只用到 bash 3.2 有的東西（macOS 內建），沒有關聯陣列；變數後面緊接中文或全形符號時，一律寫成大括號形式的變數。
set -u

cd "$(dirname "$0")/.." || exit 1
REPO="$(pwd -P)"
PY="${PY:-.venv/bin/python}"
REFRESH=0
for arg in "$@"; do
    case "${arg}" in
        --refresh) REFRESH=1 ;;
        *)
            echo "用法：PY=/path/to/python /bin/bash bin/check_repo.sh [--refresh]" >&2
            exit 2
            ;;
    esac
done

# 一律對 repo 自己的 data/ 重算：清掉所有 QUOTA_LAB_* 環境變數（QUOTA_LAB_DATA 指到別處就不能用來比對衍生檔）
for v in $(env | sed -n 's/^\(QUOTA_LAB_[A-Za-z0-9_]*\)=.*/\1/p'); do
    unset "${v}"
done

PARTIAL=0   # 沒裝 node 或 gitleaks 而略過的檢查
NOGIT=0     # 資料夾不是 git 倉庫而略過的檢查
GIT_OK=0
if command -v git > /dev/null 2>&1 && [ "$(git rev-parse --show-toplevel 2>/dev/null)" = "${REPO}" ]; then
    GIT_OK=1
else
    NOGIT=1
    echo "WARN: 這個資料夾不是 git 倉庫（或沒有 git）：衍生檔是否已提交、機密掃描、email 掃描這三步會略過。先 git clone，或 git init 並提交之後再跑。"
fi

CHART_SNAP=""

# 重算會把 charts/ 的圖整批重畫；圖不逐位元比對，所以重畫完要把圖還原成跑之前的檔案（--refresh 例外，保留新圖）。
restore_charts() {
    if [ -n "${CHART_SNAP}" ] && [ -d "${CHART_SNAP}" ]; then
        if [ "${REFRESH}" -eq 0 ]; then
            for f in charts/*.png; do
                [ -e "${f}" ] || continue
                name="${f##*/}"
                if [ -f "${CHART_SNAP}/${name}" ]; then
                    cp -p "${CHART_SNAP}/${name}" "${f}"
                else
                    rm -f -- "${f:?}"
                fi
            done
        fi
        rm -rf -- "${CHART_SNAP:?}"
        CHART_SNAP=""
    fi
}
trap restore_charts EXIT
trap 'exit 1' INT TERM

fail() {
    echo "FAIL: $1" >&2
    exit 1
}

echo "== 1/6 編譯 bin/*.py tests/*.py =="
echo "interpreter: ${PY}"
if ! "${PY}" -c 'import sys, numpy, scipy, matplotlib; sys.exit(0 if sys.version_info >= (3, 10) else 1)' > /dev/null 2>&1; then
    fail "PY=${PY} 不能用：需要 Python 3.10 以上，而且要裝好 numpy、scipy、matplotlib（pip install -r requirements.txt）"
fi
"${PY}" -m py_compile bin/*.py tests/*.py || fail "py_compile"

echo "== 2/6 合成資料回歸測試（含跨模型比較測試、快取權重範圍測試） =="
"${PY}" tests/test_synthetic.py || fail "tests/test_synthetic.py"
"${PY}" tests/test_quota_lab.py || fail "tests/test_quota_lab.py"
"${PY}" tests/test_compare_models.py || fail "tests/test_compare_models.py"
"${PY}" tests/test_cache_weight_bounds.py || fail "tests/test_cache_weight_bounds.py"
if command -v node > /dev/null 2>&1; then
    node tests/test_workflow_script.mjs || fail "tests/test_workflow_script.mjs"
else
    echo "WARN: 沒有 node，略過流程腳本的離線測試"
    PARTIAL=1
fi

echo "== 3/6 從 repo 自己的資料重算衍生檔 =="
CHART_SNAP="$(mktemp -d "${TMPDIR:-/tmp}/quota_lab_charts.XXXXXX")" || fail "mktemp"
if [ -d charts ]; then
    cp -Rp charts/. "${CHART_SNAP}/"
fi
for cmd in jumps cells checks sweep summary; do
    "${PY}" bin/analyze.py "${cmd}" > /dev/null || fail "analyze.py ${cmd}"
done
"${PY}" bin/analyze.py fit > /dev/null || fail "analyze.py fit"
"${PY}" bin/analyze.py fit --lead 60 --slack 0.1 > /dev/null || fail "analyze.py fit --lead 60 --slack 0.1"
"${PY}" bin/analyze.py fit --lead 60 --slack 0.1 --unlogged 0.1 > /dev/null || fail "analyze.py fit --lead 60 --slack 0.1 --unlogged 0.1"
"${PY}" bin/analyze.py fit --lead 60 --slack 0.1 --rounding round > /dev/null || fail "analyze.py fit --lead 60 --slack 0.1 --rounding round"
"${PY}" bin/analyze.py fit --lead 60 --slack 0.1 --rounding ceil > /dev/null || fail "analyze.py fit --lead 60 --slack 0.1 --rounding ceil"
"${PY}" bin/analyze.py charts > /dev/null || fail "analyze.py charts"
restore_charts

echo "== 4/6 衍生檔是否已是最新（提交過） =="
if [ "${REFRESH}" -eq 0 ]; then
    echo "圖（charts/*.png）不逐位元比對：matplotlib 版本或字型不同，圖檔的位元組就不同；重生的圖已還原成跑之前的檔案。"
else
    echo "圖（charts/*.png）不逐位元比對；--refresh 保留重新產生的圖。"
fi
if [ "${REFRESH}" -ne 0 ]; then
    echo "（--refresh：跳過這一步）"
elif [ "${GIT_OK}" -eq 0 ]; then
    echo "略過：資料夾不是 git 倉庫，沒有提交過的版本可以比。"
else
    # 只比對 git 已追蹤的檔案（--untracked-files=no）：讀者用別的設定多跑出來的檔（例如 data/fit/fit_lag600_*.json）不算不一致。
    DIRTY="$(git status --porcelain --untracked-files=no -- data/jumps.csv data/cells.csv data/fit RESULTS.md)"
    if [ -n "${DIRTY}" ]; then
        echo "${DIRTY}"
        fail "重算出來的文字檔與已提交的版本不同。如果你沒有改程式或資料，先照 requirements-lock.txt 安裝套件版本（pip install -r requirements-lock.txt，Python 3.12 以上；numpy、scipy 版本不同時，data/fit 的最後幾位小數會不同）再重跑一次。若裝了鎖定版本仍不同，先看過內容再提交，或用 --refresh 只重生不檢查"
    fi
    echo "重算出來的文字檔與已提交版本逐位元一致"
fi

echo "== 5/6 機密掃描（gitleaks，掃歷史） =="
if [ "${GIT_OK}" -eq 0 ]; then
    echo "略過：資料夾不是 git 倉庫，沒有歷史可掃。"
elif command -v gitleaks > /dev/null 2>&1; then
    GL_OUT="$(mktemp "${TMPDIR:-/tmp}/quota_lab_gitleaks.XXXXXX")" || fail "mktemp"
    gitleaks detect --source . --redact > "${GL_OUT}" 2>&1
    GITLEAKS_RC=$?
    tail -6 "${GL_OUT}"
    if [ "${GITLEAKS_RC}" -ne 0 ]; then
        fail "gitleaks 掃到疑似機密（完整輸出：${GL_OUT}）"
    fi
    rm -f -- "${GL_OUT:?}"
    echo "gitleaks 沒有發現機密"
else
    echo "WARN: 沒有安裝 gitleaks，跳過機密掃描"
    PARTIAL=1
fi

echo "== 6/6 追蹤檔案裡的 email（只允許 noreply@anthropic.com） =="
if [ "${GIT_OK}" -eq 0 ]; then
    echo "略過：資料夾不是 git 倉庫，沒有追蹤檔案可掃。"
else
    EMAILS="$(git grep -nIE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}' | grep -v 'noreply@anthropic.com' || true)"
    if [ -n "${EMAILS}" ]; then
        echo "${EMAILS}"
        fail "追蹤檔案裡有非預期的 email"
    fi
    echo "沒有非預期的 email"
fi


if [ "${PARTIAL}" -ne 0 ]; then
    echo "有檢查被略過（見上面的 WARN），不能算全部通過。"
    echo "CHECK_REPO_PARTIAL"
    exit 4
fi

if [ "${NOGIT}" -ne 0 ]; then
    echo "資料夾不是 git 倉庫，衍生檔是否已提交、機密掃描、email 掃描這三步沒有跑，不能算全部通過。"
    echo "CHECK_REPO_PARTIAL"
    exit 0
fi

echo "CHECK_REPO_OK"
exit 0
