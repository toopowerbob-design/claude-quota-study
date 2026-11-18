#!/usr/bin/env python3
"""把別人公開的換算公式套到這份資料，看預測落不落在我們量到的範圍裡。

公式出處：tksfjt1024，Zenn，2026-10-01（量測在 2026 年 9 月中下旬，Max 20x 方案）
https://zenn.dev/tksfjt1024/articles/25c0ab111c277c

  每次請求的用量單位 ＝ 模型權重 ×（輸入 ＋ 快取寫入 × 1.25 ＋ 輸出 × 5 ＋ 快取讀取 × 0.025）
  模型權重（以 Haiku 4.5 為 1）：Sonnet 5.5 ＝ 1.5、Opus 5.5 ＝ 2.2、Fable 5.1 ＝ 10
  週額度約 11.3 億單位，所以 1% 約 1,130 萬單位

這是該作者從實測反推的公式，不是官方規則。套用時多做了三個假設，都沒有人證實：
九月的係數到十一月還適用；Fable 5.2 沿用 Fable 5.1 的權重；兩種快取寫入（5 分鐘、1 小時）都算 1.25。
Haiku 5.5 沒有對應的權重（原文量的是 Haiku 4.5），所以不比。

做法：data/cells.csv 每一格有照牌價的美元數與五類 token 的美元占比，
除以牌價（prices.json）還原成 token 數，代入公式得到「預測扣幾個百分點」，
再算「預測每 1% 對應多少美元」，和量到的範圍比。

用法：python3 bin/prior_formula_check.py [--csv]
"""
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("QUOTA_LAB_DATA") or ROOT / "data")

WEIGHT = {"Sonnet 5.5": 1.5, "Opus 5.5": 2.2, "Fable 5.2": 10.0}
MODEL_ID = {"Sonnet 5.5": "claude-sonnet-5-5", "Opus 5.5": "claude-opus-5-5", "Fable 5.2": "claude-fable-5-2"}
COEF = {"in": 1.0, "cw5m": 1.25, "cw1h": 1.25, "out": 5.0, "cr": 0.025}
UNITS_PER_PCT = 1.13e9 / 100


def main() -> int:
    prices = json.loads((ROOT / "prices.json").read_text(encoding="utf-8"))["models"]
    rows = list(csv.DictReader(open(DATA / "cells.csv", encoding="utf-8")))
    out = []
    for r in rows:
        if r["model"] not in WEIGHT or not r["usd_per_pct_lo"]:
            continue
        usd = float(r["usd_main"])
        p = prices[MODEL_ID[r["model"]]]
        units = 0.0
        for k, c in COEF.items():
            tokens = usd * float(r["share_" + k]) / p[k] * 1e6
            units += WEIGHT[r["model"]] * c * tokens
        pred_pct = units / UNITS_PER_PCT
        pred = usd / pred_pct
        lo = float(r["usd_per_pct_lo"])
        hi = float(r["usd_per_pct_hi_adj"] or r["usd_per_pct_hi"])
        if r["upper_unreliable"] == "True":
            hi = float("inf")
        where = "範圍內" if lo <= pred <= hi else ("低於下限 %.2f" % (lo - pred) if pred < lo else "高於上限 %.2f" % (pred - hi))
        out.append((r["cell"], r["model"], pred_pct, pred, lo, hi, where))
    if "--csv" in sys.argv:
        w = csv.writer(sys.stdout)
        w.writerow(["cell", "model", "predicted_pct", "predicted_usd_per_pct", "measured_lo", "measured_hi", "verdict"])
        for o in out:
            w.writerow([o[0], o[1], "%.2f" % o[2], "%.2f" % o[3], "%.2f" % o[4], "%.2f" % o[5], o[6]])
        return 0
    print("| 格 | 模型 | 公式預測扣幾個百分點 | 公式預測每 1% 美元 | 我們量到的範圍 | 結果 |")
    print("|---|---|---|---|---|---|")
    for cell, model, pct, pred, lo, hi, where in out:
        print("| `%s` | %s | %.2f | %.2f | %.2f ~ %.2f | %s |" % (cell, model, pct, pred, lo, hi, where))
    inside = sum(1 for o in out if o[6] == "範圍內")
    print()
    print("%d 格裡有 %d 格的預測落在量到的範圍內。" % (len(out), inside))
    return 0


if __name__ == "__main__":
    sys.exit(main())
