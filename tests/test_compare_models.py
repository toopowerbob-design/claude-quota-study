#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_compare_models.py：bin/compare_models.py 的測試（只用標準函式庫）。

用法：python tests/test_compare_models.py
每個檢查印一行 ok／FAIL；有任何 FAIL 時結束碼 1。全部通過時最後一行是 COMPARE_MODELS_TEST_OK。

測到的行為：
  1. 用 repo 自己的 data/cells.csv 跑 --csv，每個預期值都要落在容差內（倍數 0.02，百分比 0.5 個百分點）；
  2. 預設的 Markdown 報告可以跑完、結束碼 0、輸出裡沒有 nan；
  3. QUOTA_LAB_DATA 指到缺少某一格的資料夾（或某一格沒有範圍）時，結束碼 3，訊息點名那一格。
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "bin" / "compare_models.py"
PY = sys.executable
fails = 0

# 預期值（下限，上限）。README 寫的是四捨五入後的數字，這裡用兩位小數：
# Opus 對 Haiku 讀檔是 3.23 與 9.66（README 寫 3.2 與 9.7），其餘與 README 一致。
EXPECTED = {
    "per1pct_vs_opus.writing.sonnet": (0.54, 0.86),
    "per1pct_vs_opus.writing.fable": (0.37, 0.65),
    "per1pct_vs_opus.repeated_read.fable": (0.18, 0.47),
    "per1pct_vs_opus.file_ingestion.fable": (0.36, 1.07),
    "deduct_ratio.file_ingestion.fable_over_opus": (1.80, 5.37),
    "deduct_ratio.writing.fable_over_opus.opus_mix": (3.02, 5.39),
    "deduct_ratio.writing.fable_over_opus.fable_mix": (3.06, 5.44),
    "deduct_ratio.repeated_read.fable_over_opus": (2.43, 6.43),
    "deduct_ratio.file_ingestion.opus_over_haiku": (3.23, 9.66),
    "deduct_ratio.writing.opus_over_sonnet": (1.08, 1.71),
    "deduct_pct.writing.sonnet_when_opus_2pct": (1.17, 1.85),
    "saved_pct.opus_to_sonnet.writing": (7.7, 41.5),
    "saved_pct.opus_to_haiku.file_ingestion": (69.0, 89.7),
}
TOL_RATIO = 0.02
TOL_PERCENT = 0.5  # 單位是百分點，只用在 saved_pct.*


def check(name: str, cond: bool, detail: str = "") -> None:
    global fails
    print(("ok   " if cond else "FAIL ") + name + ("" if cond or not detail else "：" + detail))
    if not cond:
        fails += 1


def clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if not k.startswith("QUOTA_LAB_")}


def run(args: list, data_dir: Path | None = None) -> subprocess.CompletedProcess:
    env = clean_env()
    if data_dir is not None:
        env["QUOTA_LAB_DATA"] = str(data_dir)
    return subprocess.run([PY, str(SCRIPT)] + args, cwd=str(ROOT), env=env, capture_output=True, text=True)


def parse_csv(text: str) -> dict:
    rows = {}
    for r in csv.DictReader(text.splitlines()):
        rows[r["name"]] = (float(r["lo"]), float(r["hi"]))
    return rows


def write_variant(folder: Path, drop_cell: str | None = None, blank_range_of: str | None = None) -> None:
    """複製 cells.csv 到 folder，拿掉某一格（drop_cell），或把某一格的 usd_per_pct_lo 清空。"""
    with open(ROOT / "data" / "cells.csv", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames
        rows = [r for r in reader if r["cell"] != drop_cell]
    for r in rows:
        if r["cell"] == blank_range_of:
            r["usd_per_pct_lo"] = ""
    with open(folder / "cells.csv", "w", encoding="utf-8", newline="") as out:
        w = csv.DictWriter(out, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    # 1. 預期值
    p = run(["--csv"])
    check("--csv 結束碼 0", p.returncode == 0, p.stderr.strip())
    rows = parse_csv(p.stdout) if p.returncode == 0 else {}
    check("--csv 第一列是 name,lo,hi", p.stdout.splitlines()[:1] == ["name,lo,hi"])
    for name, (elo, ehi) in EXPECTED.items():
        tol = TOL_PERCENT if name.startswith("saved_pct.") else TOL_RATIO
        got = rows.get(name)
        ok = got is not None and abs(got[0] - elo) <= tol and abs(got[1] - ehi) <= tol
        check("%s 預期 %.2f ~ %.2f" % (name, elo, ehi), ok, "實際 %s" % (got,))
    hr = rows.get("per1pct_vs_opus.repeated_read.haiku")
    check("Haiku 反覆讀只有下限（上限不設）", hr is not None and abs(hr[0] - 0.32) <= TOL_RATIO and hr[1] == float("inf"), str(hr))
    check("CSV 裡沒有 nan", "nan" not in p.stdout.lower())

    # 2. Markdown 報告
    m = run([])
    check("Markdown 報告結束碼 0", m.returncode == 0, m.stderr.strip())
    check("Markdown 報告有四組表與重點數字", all(t in m.stdout for t in ("## 1.", "## 2.", "## 3.", "## 4.", "## 重點數字")))
    check("Markdown 報告裡沒有 nan", "nan" not in m.stdout.lower())

    # 3. QUOTA_LAB_DATA 指到同一份資料時結果相同（確認環境變數有被採用）
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        write_variant(folder)
        same = run(["--csv"], folder)
        check("QUOTA_LAB_DATA 指到複本，結果與原資料相同", same.returncode == 0 and same.stdout == p.stdout)

    # 4. 缺格與沒有範圍的格：結束碼 3，訊息點名那一格
    for label, kwargs, cell in (
        ("缺 f2-fable-write", {"drop_cell": "f2-fable-write"}, "f2-fable-write"),
        ("缺 h3-haiku-reread", {"drop_cell": "h3-haiku-reread"}, "h3-haiku-reread"),
        ("o1b-opus-digest 沒有範圍", {"blank_range_of": "o1b-opus-digest"}, "o1b-opus-digest"),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_variant(folder, **kwargs)
            for args in (["--csv"], []):
                r = run(args, folder)
                msg = r.stdout + r.stderr
                check("%s：結束碼 3%s" % (label, "（--csv）" if args else ""), r.returncode == 3, "實際 %s" % r.returncode)
                check("%s：訊息點名 %s%s" % (label, cell, "（--csv）" if args else ""), cell in r.stderr, msg.strip())
                check("%s：沒有印出報告或 CSV%s" % (label, "（--csv）" if args else ""), r.stdout == "")

    if fails:
        print("%d 項失敗" % fails)
        return 1
    print("COMPARE_MODELS_TEST_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
