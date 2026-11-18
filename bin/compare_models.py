#!/usr/bin/env python3
"""把 data/cells.csv 的各格範圍換算成「模型對模型」的比較數字，讀者可以自己重算 README 結論裡的倍數。

輸入（只用標準函式庫）：
  data/cells.csv   每一格量到的「每 1% 週額度對應多少牌價美元」範圍，以及五類 token 的美元占比
                   （資料夾可用環境變數 QUOTA_LAB_DATA 指定）
  prices.json      四個模型的牌價（每百萬 token 美元），用來把美元占比還原成 token 數

名詞與算法（輸出的報告開頭也會印一份）：

1. 一格的範圍：下限 ＝ usd_per_pct_lo；上限 ＝ usd_per_pct_hi_adj，那欄是空的就用 usd_per_pct_hi。
   upper_unreliable 為 True 的格子只有下限，上限不設（報告寫「以上」）。
   Sonnet 每種工作各跑了兩輪，兩輪合併：下限取兩者較小的、上限取兩者較大的。
2. 工作與格子的對照（Sonnet 兩輪都列）：
   讀檔：stage1-sonnet、s1b-sonnet-digest（Sonnet）；o1b-opus-digest（Opus）；h1-haiku-digest（Haiku）；f1-fable-digest（Fable）
   寫長文：s2-sonnet-write、s2b-sonnet-write（Sonnet）；o2-opus-write（Opus）；f2-fable-write（Fable）
   反覆讀：s3-sonnet-reread、s3b-sonnet-reread（Sonnet）；o3-opus-reread（Opus）；f3-fable-reread（Fable）；
   h3-haiku-reread（Haiku，只有下限）
3. 所有區間都是把兩邊範圍的端點直接相除，也就是最寬的情況：一邊取最有利、另一邊取最不利。
   它不是信賴區間，也不是機率；真正的值在區間裡的哪裡，這份資料沒有辦法說。
4. 第 1 組「同樣扣 1%，換到的牌價美元是 Opus 的幾倍」：X 的範圍除以 Opus 的範圍，
   區間 ＝ [X 下限 / Opus 上限，X 上限 / Opus 下限]。
5. 第 2 組「同樣的工作，扣的額度是幾倍（X 對 Y）」，三種工作用三種單位，因為每一種的「同樣」不一樣：
   讀檔（每個子任務）：一個子任務扣的 % ＝ agent_usd_median / 每 1% 美元，
     區間 ＝ [(mX / X 上限) / (mY / Y 下限)，(mX / X 下限) / (mY / Y 上限)]，m 是子任務的牌價美元中位數。
     Sonnet 兩輪的子任務中位數不同，所以兩輪分開列，不合併。
   寫長文（同樣的 token）：用 Y 那一格的美元占比與牌價還原出 token 數
     （token ＝ usd_main × 占比 / 該類牌價），價格比 r ＝ 這些 token 照 X 牌價的成本 / 照 Y 牌價的成本，
     區間 ＝ [r × Y 下限 / X 上限，r × Y 上限 / X 下限]。
     Fable 對 Opus 另外用 Fable 那一格的 token 組成再算一次當對照。
     Opus 對 Sonnet 用 Sonnet 兩格各自的 token 組成，r 取兩者的最小與最大。
     另附一行換算：Opus 扣 2% 時，Sonnet 扣 2% 除以上面的倍數。
   反覆讀（每一次呼叫）：一次呼叫的牌價美元 ＝ agent_usd_median / 該格每個子任務的呼叫次數（CALLS 表），
     區間 ＝ [(cX / X 上限) / (cY / Y 下限)，(cX / X 下限) / (cY / Y 上限)]。
6. 第 3 組：每個讀檔子任務扣週額度的百分之幾 ＝ [m / 上限，m / 下限]。
7. 第 4 組「換便宜的模型能省多少額度」：
   Opus 換 Sonnet（寫長文）：省 ＝ 1 減掉「Sonnet 扣的額度 / Opus 扣的額度」，
     那個比值取第 2 組「Opus 對 Sonnet」倍數區間的倒數。
   Opus 換 Haiku（讀檔）：省 ＝ 1 減掉第 2 組「Opus 對 Haiku」倍數區間的倒數。

限制：只比額度扣多少，沒有比輸出品質。寫長文沒有 Haiku 的格；反覆讀的 Haiku 只有下限，所以第 2 組不比 Haiku 的反覆讀。
Haiku 只比讀檔，用的是量到的子任務美元，不需要還原 token，所以 prices.json 裡 Haiku 的高價級距在這裡用不到。

用法：python3 bin/compare_models.py [--csv]
  預設印 Markdown 報告；--csv 改印機器可讀的 name,lo,hi（百分比類的列，名稱含 _pct，數值單位是百分之幾）。
  結束碼：0 正常；3 cells.csv 缺少需要的格（或那一格沒有範圍）；2 找不到輸入檔。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("QUOTA_LAB_DATA") or ROOT / "data")
INF = float("inf")

MODEL_NAME = {"haiku": "Haiku 5.5", "sonnet": "Sonnet 5.5", "opus": "Opus 5.5", "fable": "Fable 5.2"}
MODEL_ID = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5", "fable": "claude-fable-5-2"}
TOKEN_KINDS = ["in", "cw5m", "cw1h", "cr", "out"]

WORKLOAD_LABEL = {"file_ingestion": "讀檔", "writing": "寫長文", "repeated_read": "反覆讀"}
WORKLOAD_CELLS = {
    "file_ingestion": {
        "sonnet": ["stage1-sonnet", "s1b-sonnet-digest"],
        "opus": ["o1b-opus-digest"],
        "haiku": ["h1-haiku-digest"],
        "fable": ["f1-fable-digest"],
    },
    "writing": {
        "sonnet": ["s2-sonnet-write", "s2b-sonnet-write"],
        "opus": ["o2-opus-write"],
        "fable": ["f2-fable-write"],
    },
    "repeated_read": {
        "sonnet": ["s3-sonnet-reread", "s3b-sonnet-reread"],
        "opus": ["o3-opus-reread"],
        "fable": ["f3-fable-reread"],
        "haiku": ["h3-haiku-reread"],
    },
}

# 反覆讀每個子任務的呼叫次數（來源：docs/PROMPTS.md 的參數表，欄位 calls）
CALLS = {"o3-opus-reread": 250, "f3-fable-reread": 200, "s3-sonnet-reread": 250, "s3b-sonnet-reread": 250}


class DataError(Exception):
    pass


@dataclass
class Cell:
    name: str
    model: str  # MODEL_NAME 的鍵
    usd_main: float
    shares: dict
    lo: float
    hi: float  # 只有下限的格子是 INF
    median: float


def div(a: float, b: float) -> float:
    """除法；分母是 0 時回傳無限大（上限不設的格子會走到這裡）。"""
    if b == 0:
        return INF if a > 0 else 0.0
    return a / b


def load_cells(path: Path, prices: dict) -> dict:
    """讀 cells.csv，回傳每個需要的格（名稱 -> Cell）。缺格或沒有範圍時丟 DataError，訊息點名那一格。"""
    rows = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            if "cell" not in r:
                raise DataError("cells.csv 沒有 cell 欄，讀不出格名")
            rows[r["cell"]] = r
    problems = []
    cells = {}
    for workload, by_model in WORKLOAD_CELLS.items():
        for model, names in by_model.items():
            for name in names:
                if name in cells:
                    continue
                r = rows.get(name)
                if r is None:
                    problems.append("cells.csv 缺少需要的格：%s（%s，%s）" % (name, WORKLOAD_LABEL[workload], MODEL_NAME[model]))
                    continue
                if r.get("model") != MODEL_NAME[model]:
                    problems.append("格 %s 的 model 欄是「%s」，預期「%s」" % (name, r.get("model"), MODEL_NAME[model]))
                    continue
                if not (r.get("usd_per_pct_lo") or "").strip():
                    problems.append("格 %s 在 cells.csv 裡沒有 usd_per_pct_lo（沒有可用的範圍）" % name)
                    continue
                try:
                    lo = float(r["usd_per_pct_lo"])
                    hi_text = (r.get("usd_per_pct_hi_adj") or "").strip() or (r.get("usd_per_pct_hi") or "").strip()
                    hi = float(hi_text) if hi_text else INF
                    if (r.get("upper_unreliable") or "").strip() == "True":
                        hi = INF
                    cell = Cell(
                        name=name, model=model, usd_main=float(r["usd_main"]),
                        shares={k: float(r["share_" + k]) for k in TOKEN_KINDS},
                        lo=lo, hi=hi, median=float(r["agent_usd_median"]),
                    )
                except (KeyError, ValueError) as exc:
                    problems.append("格 %s 的欄位讀不出來（%s）" % (name, exc))
                    continue
                if workload == "file_ingestion" and cell.hi == INF:
                    problems.append("格 %s 沒有上限，但讀檔比較需要上限" % name)
                    continue
                cells[name] = cell
    if problems:
        raise DataError("\n".join(problems))
    for model in MODEL_ID.values():
        if model not in prices:
            raise DataError("prices.json 缺少模型：%s" % model)
    return cells


def combine(cs: list) -> tuple:
    """兩輪（或多輪）合併：下限取最小、上限取最大。"""
    return min(c.lo for c in cs), max(c.hi for c in cs)


def tokens_of(cell: Cell, prices: dict) -> dict:
    """由美元與占比還原 token 數（單位：個）。"""
    p = prices[MODEL_ID[cell.model]]
    return {k: cell.usd_main * cell.shares[k] / p[k] * 1e6 for k in TOKEN_KINDS}


def cost_at(tokens: dict, model: str, prices: dict) -> float:
    p = prices[MODEL_ID[model]]
    return sum(tokens[k] * p[k] / 1e6 for k in TOKEN_KINDS)


def per_unit_interval(mx: float, x: tuple, my: float, y: tuple) -> tuple:
    """每個單位（子任務或呼叫）扣的額度：X 對 Y 的倍數區間。"""
    lo = div(div(mx, x[1]), div(my, y[0]))
    hi = div(div(mx, x[0]), div(my, y[1]))
    return lo, hi


def same_tokens_interval(mix_cells: list, xm: str, x: tuple, ym: str, y: tuple, prices: dict) -> tuple:
    """同樣的 token：X 對 Y 的倍數區間。回傳 (下限, 上限, r 最小, r 最大)。"""
    rs = []
    for c in mix_cells:
        tk = tokens_of(c, prices)
        rs.append(cost_at(tk, xm, prices) / cost_at(tk, ym, prices))
    rmin, rmax = min(rs), max(rs)
    return div(rmin * y[0], x[1]), div(rmax * y[1], x[0]), rmin, rmax


def compute(cells: dict, prices: dict) -> dict:
    """算出全部數字，回傳 {group: [row, ...]}；row 是 dict，至少有 name、lo、hi。"""
    rng = {}  # (workload, model) -> (lo, hi)
    for w, by_model in WORKLOAD_CELLS.items():
        for m, names in by_model.items():
            rng[(w, m)] = combine([cells[n] for n in names])
    out = {"g1": [], "g2": [], "g3": [], "g4": []}

    # 第 1 組：同樣扣 1%，換到的牌價美元是 Opus 的幾倍
    for w in WORKLOAD_CELLS:
        o = rng[(w, "opus")]
        for m in ("sonnet", "fable", "haiku"):
            if (w, m) not in rng:
                continue
            x = rng[(w, m)]
            out["g1"].append({
                "name": "per1pct_vs_opus.%s.%s" % (w, m), "workload": w, "model": m,
                "x": x, "opus": o, "lo": div(x[0], o[1]), "hi": div(x[1], o[0]),
            })

    # 第 2 組：同樣的工作，扣的額度是幾倍
    def g2(name, workload, label, method, lo, hi, r=None):
        out["g2"].append({"name": name, "workload": workload, "label": label, "method": method, "lo": lo, "hi": hi, "r": r})

    w = "file_ingestion"
    o = cells["o1b-opus-digest"]
    f = cells["f1-fable-digest"]
    h = cells["h1-haiku-digest"]
    lo, hi = per_unit_interval(f.median, (f.lo, f.hi), o.median, (o.lo, o.hi))
    g2("deduct_ratio.file_ingestion.fable_over_opus", w, "Fable 對 Opus", "每個子任務", lo, hi)
    lo, hi = per_unit_interval(o.median, (o.lo, o.hi), h.median, (h.lo, h.hi))
    g2("deduct_ratio.file_ingestion.opus_over_haiku", w, "Opus 對 Haiku", "每個子任務", lo, hi)
    for n in WORKLOAD_CELLS[w]["sonnet"]:
        s = cells[n]
        lo, hi = per_unit_interval(o.median, (o.lo, o.hi), s.median, (s.lo, s.hi))
        g2("deduct_ratio.file_ingestion.opus_over_sonnet.%s" % n, w, "Opus 對 Sonnet（%s）" % n, "每個子任務", lo, hi)

    w = "writing"
    o = cells["o2-opus-write"]
    f = cells["f2-fable-write"]
    ro, rf = (o.lo, o.hi), (f.lo, f.hi)
    lo, hi, rmin, _ = same_tokens_interval([o], "fable", rf, "opus", ro, prices)
    g2("deduct_ratio.writing.fable_over_opus.opus_mix", w, "Fable 對 Opus（用 Opus 那格的 token 組成）", "同樣的 token", lo, hi, rmin)
    lo, hi, rmin, _ = same_tokens_interval([f], "fable", rf, "opus", ro, prices)
    g2("deduct_ratio.writing.fable_over_opus.fable_mix", w, "Fable 對 Opus（用 Fable 那格的 token 組成，對照）", "同樣的 token", lo, hi, rmin)
    sonnet_cells = [cells[n] for n in WORKLOAD_CELLS[w]["sonnet"]]
    s = rng[(w, "sonnet")]
    a, b, rmin, rmax = same_tokens_interval(sonnet_cells, "opus", ro, "sonnet", s, prices)
    g2("deduct_ratio.writing.opus_over_sonnet", w, "Opus 對 Sonnet（兩輪合併）", "同樣的 token", a, b, rmin if abs(rmax - rmin) < 1e-9 else (rmin, rmax))
    out["sonnet_when_opus_2pct"] = {"name": "deduct_pct.writing.sonnet_when_opus_2pct", "lo": div(2.0, b), "hi": div(2.0, a)}

    w = "repeated_read"
    o = cells["o3-opus-reread"]
    f = cells["f3-fable-reread"]
    co, cf = o.median / CALLS[o.name], f.median / CALLS[f.name]
    lo, hi = per_unit_interval(cf, (f.lo, f.hi), co, (o.lo, o.hi))
    g2("deduct_ratio.repeated_read.fable_over_opus", w, "Fable 對 Opus", "每一次呼叫", lo, hi)
    for n in WORKLOAD_CELLS[w]["sonnet"]:
        s = cells[n]
        cs = s.median / CALLS[n]
        lo, hi = per_unit_interval(co, (o.lo, o.hi), cs, (s.lo, s.hi))
        g2("deduct_ratio.repeated_read.opus_over_sonnet.%s" % n, w, "Opus 對 Sonnet（%s）" % n, "每一次呼叫", lo, hi)

    # 第 3 組：每個讀檔子任務扣週額度的百分之幾
    for m in ("haiku", "sonnet", "opus", "fable"):
        for n in WORKLOAD_CELLS["file_ingestion"][m]:
            c = cells[n]
            name = "quota_pct_per_subtask.file_ingestion.%s" % m
            if m == "sonnet":
                name += "." + n
            out["g3"].append({"name": name, "model": m, "cell": n, "median": c.median, "range": (c.lo, c.hi),
                              "lo": div(c.median, c.hi), "hi": div(c.median, c.lo)})

    # 第 4 組：換便宜的模型省多少額度（百分比）
    by_name = {r["name"]: r for r in out["g2"]}
    for name, label, key, w in (
        ("saved_pct.opus_to_sonnet.writing", "Opus 換 Sonnet（寫長文）", "deduct_ratio.writing.opus_over_sonnet", "writing"),
        ("saved_pct.opus_to_haiku.file_ingestion", "Opus 換 Haiku（讀檔）", "deduct_ratio.file_ingestion.opus_over_haiku", "file_ingestion"),
    ):
        r = by_name[key]
        out["g4"].append({"name": name, "label": label, "workload": w,
                          "lo": 100.0 * (1.0 - div(1.0, r["lo"])), "hi": 100.0 * (1.0 - div(1.0, r["hi"]))})
    return out


# ───────────────────────── 輸出 ─────────────────────────

def f2(x: float) -> str:
    return "%.2f" % x


def fp(x: float) -> str:
    """百分比類的數字：小於 1 時印三位小數。"""
    return "%.3f" % x if abs(x) < 1 else "%.2f" % x


def span(lo: float, hi: float, f=f2, unit: str = "") -> str:
    if hi == INF:
        return "%s%s 以上" % (f(lo), unit)
    return "%s%s ~ %s%s" % (f(lo), unit, f(hi), unit)


def csv_num(x: float) -> str:
    return "inf" if x == INF else "%.4f" % x


def all_rows(res: dict) -> list:
    rows = []
    for g in ("g1", "g2", "g3", "g4"):
        rows.extend(res[g])
    rows.append(res["sonnet_when_opus_2pct"])
    return rows


def print_csv(res: dict) -> None:
    w = csv.writer(sys.stdout, lineterminator="\n")
    w.writerow(["name", "lo", "hi"])
    for r in all_rows(res):
        w.writerow([r["name"], csv_num(r["lo"]), csv_num(r["hi"])])


def print_report(res: dict, cells: dict) -> None:
    g2 = {r["name"]: r for r in res["g2"]}
    print("# 跨模型比較（由 data/cells.csv 與 prices.json 算出）")
    print()
    print("## 定義")
    print()
    print("- 一格的範圍：下限 ＝ `usd_per_pct_lo`；上限 ＝ `usd_per_pct_hi_adj`，空的就用 `usd_per_pct_hi`。"
          "`upper_unreliable` 為 True 的格子只有下限（寫「以上」）。Sonnet 每種工作跑了兩輪，兩輪合併：下限取較小的，上限取較大的。")
    print("- 工作與格子：讀檔 ＝ stage1-sonnet、s1b-sonnet-digest、o1b-opus-digest、h1-haiku-digest、f1-fable-digest；"
          "寫長文 ＝ s2-sonnet-write、s2b-sonnet-write、o2-opus-write、f2-fable-write；"
          "反覆讀 ＝ s3-sonnet-reread、s3b-sonnet-reread、o3-opus-reread、f3-fable-reread、h3-haiku-reread（只有下限）。")
    print("- 所有區間都是兩邊範圍的端點直接相除，一邊取最有利、另一邊取最不利，所以是最寬的情況；不是信賴區間。")
    print("- 第 1 組：X 的每 1% 美元範圍除以 Opus 的範圍，區間 ＝ [X 下限 / Opus 上限，X 上限 / Opus 下限]。")
    print("- 第 2 組（同樣的工作，扣的額度是幾倍）：讀檔用每個子任務（子任務美元中位數 / 每 1% 美元）；"
          "寫長文用同樣的 token（照 Y 那格的美元占比與牌價還原 token，價格比 r ＝ 照 X 牌價的成本 / 照 Y 牌價的成本）；"
          "反覆讀用每一次呼叫（子任務美元中位數 / 呼叫次數，Opus 250、Fable 200、Sonnet 兩輪各 250）。")
    print("- 第 3 組：每個讀檔子任務扣週額度的百分之幾 ＝ [子任務美元中位數 / 上限，子任務美元中位數 / 下限]。")
    print("- 第 4 組：省的額度 ＝ 1 減掉「便宜模型扣的額度 / Opus 扣的額度」，後者取第 2 組倍數區間的倒數。")
    print("- 只比額度扣多少，沒有比輸出品質。")
    print()

    print("## 1. 同樣扣 1%，換到的牌價美元是 Opus 的幾倍")
    print()
    print("| 工作 | 模型 | 該模型每 1% 的牌價美元 | Opus 每 1% 的牌價美元 | 是 Opus 的幾倍 |")
    print("|---|---|---|---|---|")
    for r in res["g1"]:
        print("| %s | %s | %s | %s | %s |" % (
            WORKLOAD_LABEL[r["workload"]], MODEL_NAME[r["model"]], span(*r["x"]), span(*r["opus"]), span(r["lo"], r["hi"])))
    print()

    print("## 2. 同樣的工作，扣的額度是幾倍（X 對 Y）")
    print()
    print("| 工作 | 比較 | 「同樣」的單位 | 價格比 r | 扣的額度是幾倍 |")
    print("|---|---|---|---|---|")
    for r in res["g2"]:
        rr = r["r"]
        if rr is None:
            rtxt = "不需要"
        elif isinstance(rr, tuple):
            rtxt = "%.3f ~ %.3f" % rr
        else:
            rtxt = "%.3f" % rr
        print("| %s | %s | %s | %s | %s |" % (WORKLOAD_LABEL[r["workload"]], r["label"], r["method"], rtxt, span(r["lo"], r["hi"])))
    print()
    d = res["sonnet_when_opus_2pct"]
    print("換算：寫長文時，Opus 扣 2%%，Sonnet 大約扣 %s%% 到 %s%%（Sonnet 兩輪合併）。" % (f2(d["lo"]), f2(d["hi"])))
    print()

    print("## 3. 每個讀檔子任務扣週額度的百分之幾")
    print()
    print("| 模型 | 格 | 子任務美元中位數 | 每 1% 的牌價美元 | 一個子任務扣週額度的 % |")
    print("|---|---|---|---|---|")
    for r in res["g3"]:
        print("| %s | `%s` | %.3f | %s | %s |" % (
            MODEL_NAME[r["model"]], r["cell"], r["median"], span(*r["range"]), span(r["lo"], r["hi"], fp)))
    print()

    print("## 4. 換便宜的模型，能省多少額度")
    print()
    print("| 換法 | 省下的額度 |")
    print("|---|---|")
    for r in res["g4"]:
        print("| %s | %s |" % (r["label"], span(r["lo"], r["hi"], fp, "%")))
    print()

    def pair(r, nd=1, unit=""):
        return "%.*f%s 到 %.*f%s" % (nd, r["lo"], unit, nd, r["hi"], unit)

    g1 = {r["name"]: r for r in res["g1"]}
    g4 = {r["name"]: r for r in res["g4"]}
    g3 = {r["name"]: r for r in res["g3"]}
    print("## 重點數字（四捨五入到小數一位）")
    print()
    print("- 同樣扣 1%%，寫長文：Sonnet 換到的牌價是 Opus 的 %s 倍。" % pair(g1["per1pct_vs_opus.writing.sonnet"]))
    print("- 同樣扣 1%%，Fable 換到的牌價是 Opus 的幾倍：寫長文 %s 倍；反覆讀 %s 倍；讀檔 %s 倍。" % (
        pair(g1["per1pct_vs_opus.writing.fable"]), pair(g1["per1pct_vs_opus.repeated_read.fable"]),
        pair(g1["per1pct_vs_opus.file_ingestion.fable"])))
    print("- 同一份工作，Fable 扣的額度是 Opus 的幾倍：讀檔每個子任務 %s 倍；寫長文同樣的 token %s 倍；反覆讀每一次呼叫 %s 倍。" % (
        pair(g2["deduct_ratio.file_ingestion.fable_over_opus"]), pair(g2["deduct_ratio.writing.fable_over_opus.opus_mix"]),
        pair(g2["deduct_ratio.repeated_read.fable_over_opus"])))
    print("- 讀檔，Opus 扣的額度是 Haiku 的 %s 倍。" % pair(g2["deduct_ratio.file_ingestion.opus_over_haiku"]))
    print("- 寫長文，Opus 扣的額度是 Sonnet 的 %s 倍；Opus 扣 2%% 時，Sonnet 扣 %s%% 到 %s%%。" % (
        pair(g2["deduct_ratio.writing.opus_over_sonnet"]), "%.1f" % d["lo"], "%.1f" % d["hi"]))
    print("- 寫長文，Opus 換 Sonnet 省 %s 的額度。" % pair(g4["saved_pct.opus_to_sonnet.writing"], 1, "%"))
    print("- 讀檔，Opus 換 Haiku 省 %s 的額度。" % pair(g4["saved_pct.opus_to_haiku.file_ingestion"], 1, "%"))
    q = {m: g3["quota_pct_per_subtask.file_ingestion." + m] for m in ("opus", "fable", "haiku")}
    print("- 一個讀檔子任務扣週額度：Opus %s，Fable %s，Haiku %s（Sonnet 兩輪見第 3 組）。" % (
        pair(q["opus"], 3, "%"), pair(q["fable"], 3, "%"), pair(q["haiku"], 3, "%")))


def main() -> int:
    ap = argparse.ArgumentParser(description="跨模型比較：由 cells.csv 與 prices.json 算出 README 結論裡的倍數")
    ap.add_argument("--csv", action="store_true", help="改印機器可讀的 name,lo,hi")
    args = ap.parse_args()
    cells_path = DATA / "cells.csv"
    prices_path = ROOT / "prices.json"
    for p in (cells_path, prices_path):
        if not p.exists():
            print("找不到輸入檔：%s" % p.name, file=sys.stderr)
            return 2
    prices = json.loads(prices_path.read_text(encoding="utf-8"))["models"]
    try:
        cells = load_cells(cells_path, prices)
    except DataError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    res = compute(cells, prices)
    if args.csv:
        print_csv(res)
    else:
        print_report(res, cells)
    return 0


if __name__ == "__main__":
    sys.exit(main())
