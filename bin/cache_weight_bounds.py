#!/usr/bin/env python3
"""從既有的實驗格，反推「讀取快取」在訂閱額度上算得多便宜。

這次實驗沒有一格是純粹的讀取快取：每一格都是幾類 token 的混合。
但是同一個模型的幾格，混合比例差很多（讀檔以寫入快取為主、反覆讀以讀取快取為主、
寫長文以輸出為主），所以可以解出各類 token 的可行範圍。

做法（每個模型各算一次）：
  未知數 q_cw、q_cr、q_out、q_in：每 1 美元牌價的該類 token，扣掉幾個百分點的週額度（都不小於 0）。
  每一格給一條限制：
      1 / 上限  <=  Σ（該類美元占比 × q）  <=  1 / 下限
    其中上限、下限是該格「每 1% 對應多少美元」的範圍（上限優先用扣掉主持對話後的值；
    只有下限的格只用右半邊）。5 分鐘與 1 小時兩種快取寫入視為同一類。
  然後用線性規劃加二分搜尋，求 q_cr / q_cw 的最小值與最大值：
    先解一次「讓每條限制的鬆弛量盡量大」的線性規劃，找一個一定可行的比值當起點；
    再從起點往 0 與往 R_MAX 兩邊各二分 40 次，每一步都是「q_cr = 比值 × q_cw 時有沒有解」的線性規劃。
    q_cw 設了很小的下限（QW_MIN），免得「q_cw 與 q_cr 都是 0」這個沒有比值可言的解讓所有比值都顯得可行。
    可行的比值一定是一整段（凸集合經線性分式映射），所以只需要找兩端；找完會抽查中間，不是一整段就在 stderr 警告。
    比值到 R_MAX（10）都還可行，就算「沒夾到上限」（Haiku 即是：它的 q_cw 可以任意小，比值實際上沒有上限）。

讀法：
  q_cr / q_cw = 1  表示「一美元的讀取快取」和「一美元的寫入快取」扣一樣多額度，
                    也就是額度照牌價算，沒有額外折扣。
  q_cr / q_cw < 1  表示額度對讀取快取算得比牌價還便宜。
  換成每個 token：讀取快取的 token 扣的額度，是寫入快取（5 分鐘）token 的
      （q_cr / q_cw）×（讀取快取牌價 / 寫入快取牌價）倍。

限制：這是用整數讀數夾出來的範圍再反推，範圍很寬；假設同一個模型在不同格裡，
各類 token 的換算不變。它是推算，不是直接量到的。印出來的是「資料容許的最小～最大」外框，
不是最可能的值，也不是信賴區間；兩端各要求其他格同時落在極端才成立。
5 分鐘與 1 小時寫入合成一類（按美元占比相加），每 token 的換算用 5 分鐘寫入牌價；
1 小時寫入只出現在 Fable 的 f1 格（占 1.1%），對結果的影響可以忽略，Opus 與 Sonnet 的格沒有。

用法：python3 bin/cache_weight_bounds.py [--csv]
  資料夾預設 <repo>/data，可用環境變數 QUOTA_LAB_DATA 改。結束碼：0 正常；2 Python 太舊；3 cells.csv 缺格。
"""
import csv
import json
import os
import sys
from pathlib import Path

if sys.version_info < (3, 10):
    print("需要 Python 3.10 以上")
    sys.exit(2)

from scipy.optimize import linprog

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("QUOTA_LAB_DATA") or ROOT / "data")

CELLS = {
    "Opus 5.5": ("claude-opus-5-5", ["o1b-opus-digest", "o2-opus-write", "o3-opus-reread"]),
    "Sonnet 5.5": ("claude-sonnet-5-5", ["stage1-sonnet", "s1b-sonnet-digest", "s2-sonnet-write",
                                         "s2b-sonnet-write", "s3-sonnet-reread", "s3b-sonnet-reread"]),
    "Fable 5.2": ("claude-fable-5-2", ["f1-fable-digest", "f2-fable-write", "f3-fable-reread"]),
    "Haiku 5.5": ("claude-haiku-5-5", ["h1-haiku-digest", "h3-haiku-reread"]),
}
VARS = ["cw", "cr", "out", "in"]  # 未知數順序
QW_MIN = 1e-4    # q_cw 的下限（百分點／牌價美元）。實際值約 0.04～0.14，這個下限只用來排除 q_cw＝q_cr＝0 的退化解
R_MAX = 10.0     # 比值搜尋的上界；到這裡還可行就當「沒夾到上限」
BOUNDS = [(QW_MIN, None), (0, None), (0, None), (0, None)]


def constraints(rows, names):
    """回傳 A_ub、b_ub（A_ub x <= b_ub）。"""
    a_ub, b_ub = [], []
    for n in names:
        r = rows[n]
        share = [float(r["share_cw5m"]) + float(r["share_cw1h"]), float(r["share_cr"]),
                 float(r["share_out"]), float(r["share_in"])]
        lo = float(r["usd_per_pct_lo"])
        a_ub.append(share)
        b_ub.append(1.0 / lo)                      # Σ share q <= 1/lo
        if r["upper_unreliable"] != "True":
            hi = float(r["usd_per_pct_hi_adj"] or r["usd_per_pct_hi"])
            a_ub.append([-x for x in share])
            b_ub.append(-1.0 / hi)                 # Σ share q >= 1/hi
    return a_ub, b_ub


def feasible_ratio(a_ub, b_ub, ratio):
    """q_cr = ratio × q_cw 時有沒有解（q_cw 不小於 QW_MIN）。"""
    a_eq = [[-ratio, 1.0, 0.0, 0.0]]
    res = linprog([0, 0, 0, 0], A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=[0.0],
                  bounds=BOUNDS, method="highs")
    if res.status not in (0, 2):                   # 0＝有解、2＝無解；其他是求解器出問題，不能當無解
        raise RuntimeError("linprog 回報狀態 %s：%s" % (res.status, res.message))
    return res.status == 0


def seed_ratio(a_ub, b_ub):
    """找一個一定可行的比值當搜尋起點；各格範圍互相不相容就回傳 None。
    解「每條限制的鬆弛量 s 盡量大」的線性規劃：A x + s <= b，s >= 0，最大化 s。
    起點離邊界越遠，越不會被求解器的容許誤差擠出可行區。"""
    a_s = [list(row) + [1.0] for row in a_ub]
    res = linprog([0, 0, 0, 0, -1.0], A_ub=a_s, b_ub=b_ub, bounds=BOUNDS + [(0, None)], method="highs")
    if res.status == 2:
        return None
    if res.status != 0:
        raise RuntimeError("linprog 回報狀態 %s：%s" % (res.status, res.message))
    r0 = float(res.x[1] / res.x[0])
    if feasible_ratio(a_ub, b_ub, r0):
        return r0
    for i in range(0, 201):                        # 數值上起點被擠出去：退回 0 到 10 每 0.05 一格找一個
        g = i * R_MAX / 200
        if feasible_ratio(a_ub, b_ub, g):
            return g
    return None


def _edge(a_ub, b_ub, ok, bad, iters=40):
    """ok 可行、bad 不可行；二分找兩者之間可行區的邊界，回傳可行那一側（誤差 < 10/2^40，遠小於求解器容許誤差）。"""
    for _ in range(iters):
        mid = (ok + bad) / 2.0
        if feasible_ratio(a_ub, b_ub, mid):
            ok = mid
        else:
            bad = mid
    return ok


def ratio_bounds(a_ub, b_ub, label=""):
    """回傳 (最小比值, 最大比值)；最大比值是 None 表示到 R_MAX 都還可行（沒夾到上限）。
    各格範圍互相不相容時回傳 None。"""
    r0 = seed_ratio(a_ub, b_ub)
    if r0 is None:
        return None
    lo = 0.0 if feasible_ratio(a_ub, b_ub, 0.0) else _edge(a_ub, b_ub, r0, 0.0)
    hi = None if (r0 >= R_MAX or feasible_ratio(a_ub, b_ub, R_MAX)) else _edge(a_ub, b_ub, r0, R_MAX)
    top = R_MAX if hi is None else hi
    for i in range(1, 10):                         # 可行比值理論上是一整段；抽查中間九點，防求解器出怪結果
        g = lo + (top - lo) * i / 10.0
        if g > lo and not feasible_ratio(a_ub, b_ub, g):
            print("警告%s：可行比值在 %.4f 到 %.4f 之間不是一整段（%.4f 不可行），端點可能不可靠"
                  % ("（" + label + "）" if label else "", lo, top, g), file=sys.stderr)
            break
    return lo, hi


def frac(v):
    """每 token 的比值 v（0 到 1 之間）寫成「1/幾」；0 寫 0。"""
    if v <= 0:
        return "0"
    d = 1.0 / v
    return "1/%.0f" % d if d >= 10 else "1/%.1f" % d


def list_frac(v):
    """牌價比值寫成「1/幾」，保留小數（Haiku 是 1/12.5，不能四捨五入成 1/12）。"""
    return "1/%g" % round(1.0 / v, 2)


def main() -> int:
    prices = json.loads((ROOT / "prices.json").read_text(encoding="utf-8"))["models"]
    with open(DATA / "cells.csv", encoding="utf-8", newline="") as fh:
        rows = {r["cell"]: r for r in csv.DictReader(fh)}
    out = []
    for model, (mid, names) in CELLS.items():
        missing = [n for n in names if n not in rows or not rows[n]["usd_per_pct_lo"]]
        if missing:
            print("cells.csv 缺少：" + "、".join(missing), file=sys.stderr)
            return 3
        a_ub, b_ub = constraints(rows, names)
        rb = ratio_bounds(a_ub, b_ub, model)
        p = prices[mid]
        if "long_context" in p:                    # Haiku 的實驗格幾乎都落在長上下文級距（讀取÷寫入的牌價比兩個級距相同，都是 1/12.5）
            p = p["long_context"]
        list_ratio = p["cr"] / p["cw5m"]
        out.append((model, rb, list_ratio, p["cr"] / p["in"]))
    if "--csv" in sys.argv:
        w = csv.writer(sys.stdout)
        w.writerow(["model", "ratio_lo", "ratio_hi", "list_cr_over_cw5m", "token_lo", "token_hi"])
        for model, rb, lr, _ in out:
            if rb is None:
                w.writerow([model, "", "", "%.4f" % lr, "", ""])
            else:
                lo, hi = rb
                w.writerow([model, "%.3f" % lo, "" if hi is None else "%.3f" % hi, "%.4f" % lr,
                            "%.5f" % (lo * lr), "" if hi is None else "%.5f" % (hi * lr)])
        return 0
    print("| 模型 | 一美元讀取快取扣的額度 ÷ 一美元寫入快取扣的額度（最少 ~ 最多） | 牌價：讀取快取 ÷ 寫入快取（每 token） | 額度：讀取快取 ÷ 寫入快取（每 token，最少 ~ 最多） |")
    print("|---|---|---|---|")
    for model, rb, lr, _ in out:
        if rb is None:
            print("| %s | 這幾格的範圍互相不相容（或只剩寫入快取不扣額度的解），解不出來 | %s | 解不出來 |" % (model, list_frac(lr)))
            continue
        lo, hi = rb
        r_txt = "最少 %.2f ~ 最多 %s" % (lo, "沒夾到上限（>%g）" % R_MAX if hi is None else "%.2f" % hi)
        t_txt = "最少 %s ~ 最多 %s" % (frac(lo * lr), "沒夾到上限" if hi is None else frac(hi * lr))
        print("| %s | %s | %s | %s |" % (model, r_txt, list_frac(lr), t_txt))
    print()
    print("第二欄等於 1 表示額度照牌價算；小於 1 表示額度對讀取快取算得比牌價還便宜；範圍跨過 1 就是資料分不出便宜還是貴。")
    print("第四欄是「重讀一個快取 token」扣的額度，相當於「第一次寫入快取（5 分鐘）的一個 token」的幾分之一；")
    print("「最少」是資料容許的最小值（分母大），「最多」是最大值（分母小）。這是外框，不是最可能的值。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
