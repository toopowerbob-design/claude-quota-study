#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bin/cache_weight_bounds.py 的測試：不需要 pytest，直接 `python tests/test_cache_weight_bounds.py`，
全部通過時最後一行印 CACHE_WEIGHT_TEST_OK、結束碼 0。

每個測試各自在臨時資料夾放一份 cells.csv，用 QUOTA_LAB_DATA 指給子行程，不會動到 repo 的 data/。
測到的行為：
  1. 合成資料（已知真值）：每格的範圍包住真值，報出的 q_cr/q_cw 範圍要包住真比值、夠窄，
     並且和「獨立寫的精確解（Charnes-Cooper 線性規劃，測試裡自己算）」一致；表格欄位順序無歧義；
  2. 只有下限的格：upper_unreliable=True 的格不用上限（即使上限值是錯的），但下限仍然有用；
     hi_adj 有值時優先於 hi；
  3. 缺格：結束碼 3；
  4. 真實資料：已發表的數字（Opus 0.158～0.730、Sonnet 0.188～0.892、Fable 0.435～1.978，容許 0.01）；
  5. 函式層級：很窄的可行區（舊版格點搜尋會漏掉、誤報不相容）、q_cw 被逼成 0 的退化情形、
     互相矛盾的範圍、沒夾到上限的情形。
"""
from __future__ import annotations

import csv
import importlib.util
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from scipy.optimize import linprog

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = Path(os.environ.get("CWB_SCRIPT") or ROOT / "bin" / "cache_weight_bounds.py")   # CWB_SCRIPT：拿舊版或故意改壞的版本驗證測試抓得到
PY = sys.executable
# 真實資料測試用的資料夾：預設是 repo 自己的 data/（bin 旁邊的 data）；設 QUOTA_LAB_REAL_DATA 可改指到別的資料夾
REAL_DATA = Path(os.environ.get("QUOTA_LAB_REAL_DATA") or ROOT / "data")
FAILS: list[str] = []
DONE: list[str] = []


def load_script():
    spec = importlib.util.spec_from_file_location("cache_weight_bounds_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CWB = load_script()

# 每格的美元占比（in, cw5m, cw1h, cr, out）：照真實 cells.csv 的混合比例
SHARES = {
    "o1b-opus-digest": (0.0, 0.880, 0.0, 0.093, 0.027),
    "o2-opus-write": (0.0, 0.432, 0.0, 0.022, 0.546),
    "o3-opus-reread": (0.0, 0.087, 0.0, 0.886, 0.026),
    "stage1-sonnet": (0.0, 0.881, 0.0, 0.094, 0.026),
    "s1b-sonnet-digest": (0.0, 0.881, 0.0, 0.093, 0.026),
    "s2-sonnet-write": (0.0, 0.250, 0.0, 0.066, 0.684),
    "s2b-sonnet-write": (0.0, 0.396, 0.0, 0.026, 0.578),
    "s3-sonnet-reread": (0.0, 0.087, 0.0, 0.885, 0.028),
    "s3b-sonnet-reread": (0.0, 0.087, 0.0, 0.885, 0.028),
    "f1-fable-digest": (0.0, 0.853, 0.011, 0.081, 0.055),
    "f2-fable-write": (0.0, 0.558, 0.0, 0.001, 0.441),
    "f3-fable-reread": (0.0, 0.188, 0.0, 0.762, 0.051),
    "h1-haiku-digest": (0.0, 0.782, 0.0, 0.185, 0.033),
    "h3-haiku-reread": (0.0, 0.041, 0.0, 0.942, 0.017),
}
# 真值：每 1 美元牌價扣幾個百分點（cw＝寫入快取，cr＝讀取快取）。真比值＝cr / cw
TRUTH = {
    "Opus 5.5": {"cw": 0.05, "cr": 0.02, "out": 0.05, "in": 0.0},      # 比值 0.4
    "Sonnet 5.5": {"cw": 0.06, "cr": 0.015, "out": 0.06, "in": 0.0},   # 0.25
    "Fable 5.2": {"cw": 0.08, "cr": 0.10, "out": 0.08, "in": 0.0},     # 1.25
    "Haiku 5.5": {"cw": 0.06, "cr": 0.03, "out": 0.05, "in": 0.0},     # 0.5
}
TRUE_RATIO = {m: q["cr"] / q["cw"] for m, q in TRUTH.items()}
MODEL_OF = {n: m for m, (_, names) in CWB.CELLS.items() for n in names}
HEADER = ["cell", "model", "usd_main", "share_in", "share_cw5m", "share_cw1h", "share_cr", "share_out",
          "usd_per_pct_lo", "usd_per_pct_hi", "usd_per_pct_hi_adj", "upper_unreliable"]


# ───────────────────────── 小工具 ─────────────────────────

def true_usd_per_pct(cell: str) -> float:
    sh, q = SHARES[cell], TRUTH[MODEL_OF[cell]]
    return 1.0 / (sh[0] * q["in"] + (sh[1] + sh[2]) * q["cw"] + sh[3] * q["cr"] + sh[4] * q["out"])


def build_rows(margin: float = 0.03, overrides: dict | None = None, drop: tuple = ()) -> list[dict]:
    """每格範圍＝真值 ±margin，往外取到 0.01（和真實 cells.csv 一樣只有兩位小數）。"""
    rows = []
    for model, (_, names) in CWB.CELLS.items():
        for n in names:
            if n in drop:
                continue
            sh, t = SHARES[n], true_usd_per_pct(n)
            lo = math.floor(t * (1 - margin) * 100) / 100
            hi = math.ceil(t * (1 + margin) * 100) / 100
            row = {"cell": n, "model": model, "usd_main": "10.00", "share_in": sh[0], "share_cw5m": sh[1],
                   "share_cw1h": sh[2], "share_cr": sh[3], "share_out": sh[4],
                   "usd_per_pct_lo": "%.2f" % lo, "usd_per_pct_hi": "%.2f" % hi,
                   "usd_per_pct_hi_adj": "%.2f" % hi, "upper_unreliable": "False"}
            row.update((overrides or {}).get(n, {}))
            rows.append(row)
    return rows


def write_cells(folder: Path, rows: list[dict]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "cells.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)
    return folder


def run_script(folder: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, QUOTA_LAB_DATA=str(folder), PYTHONIOENCODING="utf-8")
    return subprocess.run([PY, str(SCRIPT), *args], env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=300)


def parse_csv_out(text: str) -> dict:
    """--csv 輸出 → {模型: (ratio_lo, ratio_hi 或 None，沒夾到上限; 或整列 None＝解不出來)}"""
    res = {}
    for r in csv.DictReader(text.splitlines()):
        if r["ratio_lo"] == "":
            res[r["model"]] = None
        else:
            res[r["model"]] = (float(r["ratio_lo"]), None if r["ratio_hi"] == "" else float(r["ratio_hi"]))
    return res


def oracle(cells_csv: Path) -> dict:
    """獨立寫的精確解：Charnes-Cooper 把 min／max q_cr/q_cw 變成線性規劃（令 t＝1/q_cw、y＝t·q，y_cw＝1）。
    回傳 {模型: (最小, 最大或 None＝無上限)}，無解的模型值為 None。不用被測腳本的任何函式。"""
    with open(cells_csv, encoding="utf-8", newline="") as fh:
        cells = {r["cell"]: r for r in csv.DictReader(fh)}
    out = {}
    for model, (_, names) in CWB.CELLS.items():
        a, b = [], []
        for n in names:
            r = cells[n]
            sh = [float(r["share_cw5m"]) + float(r["share_cw1h"]), float(r["share_cr"]),
                  float(r["share_out"]), float(r["share_in"])]
            a.append(sh + [-1.0 / float(r["usd_per_pct_lo"])])           # Σ share·y − t/lo <= 0
            b.append(0.0)
            if r["upper_unreliable"] != "True":
                hi = float(r["usd_per_pct_hi_adj"] or r["usd_per_pct_hi"])
                a.append([-x for x in sh] + [1.0 / hi])                  # −Σ share·y + t/hi <= 0
                b.append(0.0)
        ends = []
        for sense in (1.0, -1.0):
            res = linprog([0, sense, 0, 0, 0], A_ub=a, b_ub=b, A_eq=[[1, 0, 0, 0, 0]], b_eq=[1],
                          bounds=[(0, None)] * 5, method="highs")
            ends.append(sense * res.fun if res.status == 0 else (None if res.status == 3 else "infeasible"))
        out[model] = None if "infeasible" in ends else (ends[0], ends[1])
    return out


def close(x: float, y: float, tol: float) -> bool:
    return abs(x - y) <= tol


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


def run_all() -> None:
    for fn in TESTS:
        t0 = time.time()
        try:
            fn()
            DONE.append(fn.__name__)
            print("通過：%s（%.1f 秒）" % (fn.__name__, time.time() - t0))
        except Exception:
            FAILS.append(fn.__name__)
            print("失敗：" + fn.__name__)
            traceback.print_exc()


# ───────────────────────── 1. 合成資料 ─────────────────────────

@test
def synthetic_interval_contains_truth_and_matches_exact_lp():
    with tempfile.TemporaryDirectory() as td:
        folder = write_cells(Path(td), build_rows(margin=0.03))
        p = run_script(folder, "--csv")
        check(p.returncode == 0, "結束碼 %s：%s" % (p.returncode, p.stderr))
        check(p.stderr.strip() == "", "不該有警告：" + p.stderr)
        got, exact = parse_csv_out(p.stdout), oracle(folder / "cells.csv")
        for model, truth in TRUE_RATIO.items():
            lo, hi = got[model]
            e_lo, e_hi = exact[model]
            if model == "Haiku 5.5":
                # 只有兩格、q_cw 可以任意小：比值沒有上限（真實資料也是），只檢查下限與真值
                check(hi is None and e_hi is None, "Haiku 的上限應是沒夾到：%s 對 %s" % (hi, e_hi))
                check(lo <= truth + 1e-3, "Haiku：真比值 %.3f 應 >= 下限 %.3f" % (truth, lo))
                check(close(lo, e_lo, 2e-3), "Haiku 下限：腳本 %.4f，獨立精確解 %.4f" % (lo, e_lo))
                continue
            check(hi is not None, model + " 應該夾得出上限")
            check(lo - 1e-3 <= truth <= hi + 1e-3, "%s：真比值 %.3f 不在 %.3f～%.3f" % (model, truth, lo, hi))
            check(hi - lo < 0.5, "%s：範圍 %.3f～%.3f 太寬（±3%% 的資料應該夾得很窄）" % (model, lo, hi))
            check(close(lo, e_lo, 2e-3) and close(hi, e_hi, 2e-3),
                  "%s：腳本 %.4f～%.4f，獨立精確解 %.4f～%.4f" % (model, lo, hi, e_lo, e_hi))
        lo, hi = got["Opus 5.5"]
        check(hi - lo < 0.15, "Opus 範圍 %.3f～%.3f 應該小於 0.15" % (lo, hi))


@test
def synthetic_table_column_order_is_unambiguous():
    with tempfile.TemporaryDirectory() as td:
        folder = write_cells(Path(td), build_rows(margin=0.03))
        p = run_script(folder)
        check(p.returncode == 0, p.stderr)
        header = [l for l in p.stdout.splitlines() if l.startswith("| 模型")][0]
        check("最少 ~ 最多" in header.split("|")[2] and "最少 ~ 最多" in header.split("|")[4], "表頭要標明最少 ~ 最多：" + header)
        for model, (lr_txt, lr) in {"Opus 5.5": ("1/25", 0.04), "Sonnet 5.5": ("1/25", 0.04),
                                    "Fable 5.2": ("1/50", 0.02), "Haiku 5.5": ("1/12.5", 0.08)}.items():
            row = [l for l in p.stdout.splitlines() if l.startswith("| " + model)][0].split("|")
            check(row[3].strip() == lr_txt, "%s 牌價欄應為 %s，實際 %s" % (model, lr_txt, row[3].strip()))
            if model == "Haiku 5.5":                   # 上限沒夾到
                m_ratio = re.fullmatch(r"\s*最少 ([\d.]+) ~ 最多 沒夾到上限（>10）\s*", row[2])
                m_tok = re.fullmatch(r"\s*最少 1/([\d.]+) ~ 最多 沒夾到上限\s*", row[4])
                check(m_ratio and m_tok, "Haiku 的列格式不對：%s" % (row,))
                continue
            m_ratio = re.fullmatch(r"\s*最少 ([\d.]+) ~ 最多 ([\d.]+)\s*", row[2])
            m_tok = re.fullmatch(r"\s*最少 1/([\d.]+) ~ 最多 1/([\d.]+)\s*", row[4])
            check(m_ratio and m_tok, "%s 的列格式不對：%s" % (model, row))
            r_lo, r_hi = float(m_ratio.group(1)), float(m_ratio.group(2))
            d_lo, d_hi = float(m_tok.group(1)), float(m_tok.group(2))
            check(r_lo <= r_hi, "比值最少要 <= 最多")
            check(d_lo >= d_hi, "每 token：最少的分母要 >= 最多的分母（%s、%s）" % (d_lo, d_hi))
            true_tok = TRUE_RATIO[model] * lr
            check(1 / d_lo - 1e-3 <= true_tok <= 1 / d_hi + 1e-3,
                  "%s：真值 1/%.1f 不在 1/%s ～ 1/%s" % (model, 1 / true_tok, d_lo, d_hi))
            # 每 token 的範圍必須是「比值範圍 × 牌價比」，分母才不會被讀反
            check(close(1 / d_lo, r_lo * lr, 0.0015) and close(1 / d_hi, r_hi * lr, 0.0015),
                  "%s：每 token 範圍和比值範圍對不上" % model)


# ───────────────────────── 2. 只有下限的格 ─────────────────────────

@test
def lower_bound_only_cell_drops_upper_but_keeps_lower():
    t3 = true_usd_per_pct("o3-opus-reread")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        base = parse_csv_out(run_script(write_cells(td / "base", build_rows()), "--csv").stdout)["Opus 5.5"]
        # (a) o3 標成只有下限；上限值故意亂填（比下限還小），應完全被忽略
        flagged = {"o3-opus-reread": {"upper_unreliable": "True", "usd_per_pct_hi": "%.2f" % (0.2 * t3),
                                      "usd_per_pct_hi_adj": ""}}
        p = run_script(write_cells(td / "flag", build_rows(overrides=flagged)), "--csv")
        check(p.returncode == 0, p.stderr)
        f = parse_csv_out(p.stdout)["Opus 5.5"]
        check(f is not None, "標成只有下限後不該無解")
        check(f[0] <= TRUE_RATIO["Opus 5.5"] <= f[1], "範圍應包住真值 0.4：%s" % (f,))
        check(f[0] <= base[0] + 1e-6 and f[1] >= base[1] - 1e-6, "少一條上限，範圍只能變寬：%s 對 %s" % (f, base))
        # 不管上限值亂填什麼，結果都一樣
        flagged2 = {"o3-opus-reread": {"upper_unreliable": "True", "usd_per_pct_hi": "999.00", "usd_per_pct_hi_adj": ""}}
        f2 = parse_csv_out(run_script(write_cells(td / "flag2", build_rows(overrides=flagged2)), "--csv").stdout)["Opus 5.5"]
        check(close(f[0], f2[0], 1e-6) and close(f[1], f2[1], 1e-6), "只有下限的格，上限值不該影響結果")
        # 同一個亂填的上限，沒標旗就會和下限打架 → 無解
        unflagged = {"o3-opus-reread": {"usd_per_pct_hi": "%.2f" % (0.2 * t3), "usd_per_pct_hi_adj": "%.2f" % (0.2 * t3)}}
        u = parse_csv_out(run_script(write_cells(td / "unflag", build_rows(overrides=unflagged)), "--csv").stdout)
        check(u["Opus 5.5"] is None, "沒標旗的亂填上限應該讓 Opus 無解：%s" % (u["Opus 5.5"],))
        check(u["Sonnet 5.5"] is not None, "別的模型不該被牽連")
        # (b) 下限仍然有用：把 o3 的下限換成幾乎沒限制的小值，範圍要變寬（讀取快取占 88%，下限直接壓住 q_cr）
        loose = {"o3-opus-reread": {"upper_unreliable": "True", "usd_per_pct_hi": "", "usd_per_pct_hi_adj": "",
                                    "usd_per_pct_lo": "0.01"}}
        l = parse_csv_out(run_script(write_cells(td / "loose", build_rows(overrides=loose)), "--csv").stdout)["Opus 5.5"]
        check(l is not None and (l[1] is None or l[1] > f[1] + 0.05),
              "有下限的最大比值 %.3f 應明顯小於幾乎沒下限的 %s" % (f[1], l[1] if l else None))
        # (c) 下限的方向：下限＝每 1%% 至少幾美元＝每美元最多扣 1/lo 個百分點；
        #     若把 o3 的下限調到比真值還大 10%%，真值（Σ＝1/t3）就違反下限，q_cr 必須比真值小 → 範圍整體往下移
        high_lo = {"o3-opus-reread": {"upper_unreliable": "True", "usd_per_pct_hi": "", "usd_per_pct_hi_adj": "",
                                      "usd_per_pct_lo": "%.2f" % (1.1 * t3)}}
        h = parse_csv_out(run_script(write_cells(td / "highlo", build_rows(overrides=high_lo)), "--csv").stdout)["Opus 5.5"]
        check(h is not None and h[1] < f[1] - 0.01, "下限調高後最大比值應下降：%s 對 %s" % (h, f))


@test
def hi_adj_takes_precedence_over_hi():
    t3 = true_usd_per_pct("o3-opus-reread")
    lo_ok = "%.2f" % (0.8 * t3)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        wide = "%.2f" % (1.03 * t3)
        a = {"o3-opus-reread": {"usd_per_pct_lo": lo_ok, "usd_per_pct_hi": "%.2f" % (0.9 * t3), "usd_per_pct_hi_adj": wide}}
        b = {"o3-opus-reread": {"usd_per_pct_lo": lo_ok, "usd_per_pct_hi": wide, "usd_per_pct_hi_adj": ""}}
        c = {"o3-opus-reread": {"usd_per_pct_lo": lo_ok, "usd_per_pct_hi": "%.2f" % (0.9 * t3), "usd_per_pct_hi_adj": ""}}
        ra, rb, rc = (parse_csv_out(run_script(write_cells(td / k, build_rows(overrides=o)), "--csv").stdout)["Opus 5.5"]
                      for k, o in (("a", a), ("b", b), ("c", c)))
        check(ra is not None and rb is not None and rc is not None, "三種設定都該有解：%s %s %s" % (ra, rb, rc))
        check(close(ra[0], rb[0], 1e-6) and close(ra[1], rb[1], 1e-6),
              "有 hi_adj 時要用 hi_adj（等同 hi 直接填寬值）：%s 對 %s" % (ra, rb))
        check(abs(ra[0] - rc[0]) > 0.005 or abs(ra[1] - rc[1]) > 0.005,
              "測試要有鑑別力：只用 hi（較窄）的結果應該和用 hi_adj 的不同：%s 對 %s" % (ra, rc))


# ───────────────────────── 3. 缺格 ─────────────────────────

@test
def missing_cell_exits_3():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        p = run_script(write_cells(td / "drop", build_rows(drop=("o3-opus-reread",))))
        check(p.returncode == 3, "缺格應結束碼 3，實際 %s：%s" % (p.returncode, p.stderr))
        check("o3-opus-reread" in p.stderr and "缺少" in p.stderr, "stderr 要點名缺的格：" + p.stderr)
        check(p.stdout.strip() == "", "缺格時不該印出表格")
        # 格在、但下限是空的（真實資料裡「混合工作不給範圍」的格就是這樣）也算缺
        p = run_script(write_cells(td / "blank", build_rows(overrides={"f2-fable-write": {"usd_per_pct_lo": ""}})), "--csv")
        check(p.returncode == 3 and "f2-fable-write" in p.stderr, "空下限應結束碼 3：%s %s" % (p.returncode, p.stderr))
        check(p.stdout.strip() == "", "缺格時不該印出任何結果")


@test
def incompatible_ranges_report_unsolvable_not_crash():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        rows = build_rows(overrides={"o2-opus-write": {"usd_per_pct_lo": "1000.00", "usd_per_pct_hi": "2000.00",
                                                       "usd_per_pct_hi_adj": "2000.00"}})
        folder = write_cells(td, rows)
        p = run_script(folder, "--csv")
        check(p.returncode == 0, p.stderr)
        got = parse_csv_out(p.stdout)
        check(got["Opus 5.5"] is None and got["Sonnet 5.5"] is not None, "只有 Opus 該無解：%s" % got)
        t = run_script(folder).stdout
        check([l for l in t.splitlines() if l.startswith("| Opus")][0].count("解不出來") == 2, "表格該標明 Opus 解不出來")


# ───────────────────────── 4. 真實資料 ─────────────────────────

@test
def real_data_headline_numbers():
    data = REAL_DATA
    check((data / "cells.csv").exists(), "找不到 %s/cells.csv" % data)
    p = run_script(data, "--csv")
    check(p.returncode == 0, "結束碼 %s：%s" % (p.returncode, p.stderr))
    check(p.stderr.strip() == "", "真實資料不該有警告：" + p.stderr)
    got = parse_csv_out(p.stdout)
    for model, (lo, hi) in {"Opus 5.5": (0.158, 0.730), "Sonnet 5.5": (0.188, 0.892), "Fable 5.2": (0.435, 1.978)}.items():
        check(got[model] is not None, model + " 不該無解")
        check(close(got[model][0], lo, 0.01) and close(got[model][1], hi, 0.01),
              "%s：預期 %.3f～%.3f，實際 %s" % (model, lo, hi, got[model]))
    check(got["Haiku 5.5"] is not None and close(got["Haiku 5.5"][0], 0.0, 1e-6) and got["Haiku 5.5"][1] is None,
          "Haiku 應是 0 到沒夾到上限：%s" % (got["Haiku 5.5"],))
    # 和獨立的精確解一致（舊版格點搜尋最多差 0.0025）
    exact = oracle(data / "cells.csv")
    for model in ("Opus 5.5", "Sonnet 5.5", "Fable 5.2"):
        check(close(got[model][0], exact[model][0], 2e-3) and close(got[model][1], exact[model][1], 2e-3),
              "%s：腳本 %s，精確解 %s" % (model, got[model], exact[model]))
    # 表格：每 token 的範圍（最少 ~ 最多）
    t = run_script(data).stdout
    want = {"Opus 5.5": (159, 160, 34), "Sonnet 5.5": (133, 136, 28), "Fable 5.2": (115, 116, 25)}
    for model, (d_lo_min, d_lo_max, d_hi) in want.items():
        row = [l for l in t.splitlines() if l.startswith("| " + model)][0]
        m = re.search(r"最少 1/(\d+) ~ 最多 1/(\d+) \|$", row)
        check(m is not None, "列格式：" + row)
        check(d_lo_min <= int(m.group(1)) <= d_lo_max and int(m.group(2)) == d_hi,
              "%s 每 token 範圍預期 1/%s～1/%s ~ 1/%s，實際 %s" % (model, d_lo_min, d_lo_max, d_hi, row))
    check("1/12.5" in [l for l in t.splitlines() if l.startswith("| Haiku")][0], "Haiku 牌價比是 1/12.5，不能寫成 1/12")


# ───────────────────────── 5. 函式層級的邊界情形 ─────────────────────────

def ab(rows: list[dict], names: list[str]):
    return CWB.constraints({r["cell"]: r for r in rows}, names)


def exact_rows(q: dict, margin: float, cells=("o1b-opus-digest", "o2-opus-write", "o3-opus-reread")) -> list[dict]:
    """不取整的範圍（取到 10 位小數），給函式層級的測試用。"""
    rows = []
    for n in cells:
        sh = SHARES[n]
        pct = sh[0] * q["in"] + (sh[1] + sh[2]) * q["cw"] + sh[3] * q["cr"] + sh[4] * q["out"]
        rows.append({"cell": n, "share_in": sh[0], "share_cw5m": sh[1], "share_cw1h": sh[2], "share_cr": sh[3],
                     "share_out": sh[4], "usd_per_pct_lo": "%.10f" % (1 / (pct * (1 + margin))),
                     "usd_per_pct_hi": "%.10f" % (1 / (pct * (1 - margin))),
                     "usd_per_pct_hi_adj": "", "upper_unreliable": "False"})
    return rows


@test
def narrow_feasible_interval_between_grid_points_is_found():
    # 真比值 0.401、範圍 ±0.05%：可行區 0.40044～0.40156，夾在舊版格點 0.4 與 0.4025 之間，舊版會誤報「不相容」
    q = {"cw": 0.05, "cr": 0.02005, "out": 0.05, "in": 0.0}
    a, b = ab(exact_rows(q, 0.0005), ["o1b-opus-digest", "o2-opus-write", "o3-opus-reread"])
    got = CWB.ratio_bounds(a, b)
    check(got is not None, "很窄的可行區被當成無解")
    lo, hi = got
    check(lo <= 0.401 <= hi and hi - lo < 0.003, "範圍 %s 應包住 0.401 且窄於 0.003" % (got,))
    check(close(lo, 0.400444, 1e-4) and close(hi, 0.401556, 1e-4), "和精確解不符：%s" % (got,))


@test
def endpoints_are_not_quantized_to_a_grid():
    q = {"cw": 0.05, "cr": 0.02, "out": 0.05, "in": 0.0}
    a, b = ab(exact_rows(q, 0.03), ["o1b-opus-digest", "o2-opus-write", "o3-opus-reread"])
    lo, hi = CWB.ratio_bounds(a, b)
    # 精確解 0.36776～0.43447（獨立算）；舊版格點搜尋給 0.37～0.4325（往內縮了）
    check(close(lo, 0.367763, 2e-5) and close(hi, 0.434467, 2e-5), "端點應是精確解，實際 %.6f～%.6f" % (lo, hi))


@test
def degenerate_zero_cw_does_not_make_every_ratio_feasible():
    # q_out 被釘死在 0.05，再有一格 0.9·q_out + 0.1·q_cw <= 0.045 → q_cw 只能是 0，比值沒有意義。
    # 舊版把 q_cw＝q_cr＝0 當成「所有比值都可行」，報成「0 到沒有上限」。
    a = [[0, 0, 1, 0], [0, 0, -1, 0], [0.1, 0, 0.9, 0]]
    b = [0.05, -0.05, 0.045]
    check(CWB.ratio_bounds(a, b) is None, "q_cw 被逼成 0 時應回報解不出來：%s" % (CWB.ratio_bounds(a, b),))


@test
def unconstrained_cache_read_reports_no_upper_limit():
    # 沒有任何一格含讀取快取：q_cr 完全沒被限制，比值從 0 到沒夾到上限
    a, b = [[1, 0, 0, 0], [-1, 0, 0, 0]], [0.06, -0.04]
    got = CWB.ratio_bounds(a, b)
    check(got is not None and got[0] == 0.0 and got[1] is None, "預期 (0.0, None)，實際 %s" % (got,))


@test
def exactly_pinned_ratio_has_zero_width():
    # 每格範圍收到 ±1e-6：三條限制把三個未知數釘死，比值＝真值
    q = {"cw": 0.05, "cr": 0.02, "out": 0.05, "in": 0.0}
    a, b = ab(exact_rows(q, 1e-6), ["o1b-opus-digest", "o2-opus-write", "o3-opus-reread"])
    got = CWB.ratio_bounds(a, b)
    check(got is not None and got[0] <= 0.4 <= got[1] and got[1] - got[0] < 1e-3, "預期釘在 0.4：%s" % (got,))


# ───────────────────────── 結尾 ─────────────────────────

if __name__ == "__main__":
    run_all()
    if FAILS:
        print("失敗 %d 項：%s" % (len(FAILS), "、".join(FAILS)))
        sys.exit(1)
    print("共 %d 項測試通過" % len(DONE))
    print("CACHE_WEIGHT_TEST_OK")
