#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""額度換算研究：把「額度讀數」對回「各模型各類 token 的 API 等值美元」，做換算與出圖。

用法（在專案夾，用 .venv 的 python 才有繪圖套件）：
  .venv/bin/python bin/analyze.py phases          # 各階段：用量組成、讀數起訖
  .venv/bin/python bin/analyze.py jumps           # 每次跳 1% 的時間區間與當時累計用量 → data/jumps.csv
  .venv/bin/python bin/analyze.py cells [--lead 60]  # 每一格實驗的組成與「每 1% 美元」範圍 → data/cells.csv
  .venv/bin/python bin/analyze.py fit [--lag 0] [--lead 60] [--slack 0.1] [--unlogged 0.1] [--start free|zero]
  .venv/bin/python bin/analyze.py sweep           # 掃 lead × slack，三個假設各自可行不可行 → data/fit/sweep.csv
  .venv/bin/python bin/analyze.py checks          # 幾項資料衛生檢查（只印 OK／WARN，永遠結束碼 0）
  .venv/bin/python bin/analyze.py summary         # 產生 RESULTS.md（自動產生，勿手改）
  .venv/bin/python bin/analyze.py charts          # 出圖到 charts/

環境變數：
  QUOTA_LAB_DATA   資料目錄（預設＝repo 的 data/）
  QUOTA_LAB_CHARTS 出圖目錄（預設＝repo 的 charts/）
  QUOTA_LAB_TZ_OFFSET_HOURS 列印本地時間用的時區位移（小時，預設 8）

模型（讀數是整數，但官方沒說怎麼取整）：
  總額度%  = Σ_模型 Σ_類別 θ[模型,類別] × API 等值美元[模型,類別]
  Fable%   = 2 × Σ_類別 θ[Fable,類別] × API 等值美元[Fable,類別]   （Fable 上限＝總額度的 50%）
每一筆讀數 r（時刻 t）給兩條限制： 用量(t) ≥ r 、 用量(t − lag) ≤ r + 1。
讀數怎麼取整沒有公開說明，--rounding 測三種方式（無條件捨去、四捨五入、無條件進位）。
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 10):  # 這一段要在 3.9 也能解析：只用到 sys 與 print
    print("需要 Python 3.10 以上（目前是 " + sys.version.split()[0] + "）；macOS 內建的 python3 是 3.9，請另外安裝較新的版本。")
    raise SystemExit(2)

import argparse
import bisect
import csv
import json
import math
import os
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("QUOTA_LAB_DATA") or (ROOT / "data"))
if os.environ.get("QUOTA_LAB_CHARTS"):
    CHARTS = Path(os.environ["QUOTA_LAB_CHARTS"])
elif os.environ.get("QUOTA_LAB_DATA"):
    CHARTS = DATA / "charts"  # 指定資料目錄時，圖也放在資料目錄底下，不寫回 repo
else:
    CHARTS = ROOT / "charts"
CLASSES = ["in", "cw5m", "cw1h", "cr", "out"]
CLASS_ZH = {"in": "一般輸入", "cw5m": "快取寫入(5分)", "cw1h": "快取寫入(1時)", "cr": "快取讀取", "out": "輸出"}
SHORT = {"claude-fable-5-2": "Fable 5.2", "claude-opus-5-5": "Opus 5.5",
         "claude-sonnet-5-5": "Sonnet 5.5", "claude-haiku-5-5": "Haiku 5.5"}
FABLE = "claude-fable-5-2"
try:
    TZ = float(os.environ.get("QUOTA_LAB_TZ_OFFSET_HOURS") or 8) * 3600
except ValueError:
    TZ = 8 * 3600
PALETTE = ["#7c3aed", "#dc2626", "#2563eb", "#059669", "#d97706", "#0891b2", "#be185d", "#4b5563"]
COLOR = {"claude-fable-5-2": "#7c3aed", "claude-opus-5-5": "#dc2626",
         "claude-sonnet-5-5": "#2563eb", "claude-haiku-5-5": "#059669"}
INF = float("inf")


def name_of(m: str) -> str:
    return SHORT.get(m, m)


def jl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]


def hhmm(t: float, sec: bool = True) -> str:
    import time
    return time.strftime("%m-%d %H:%M:%S" if sec else "%H:%M", time.gmtime(t + TZ))


def full_time(t: float) -> str:
    """含年份的本地時間（年-月-日 時:分:秒），RESULTS.md 用。"""
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t + TZ))


def year_of(t: float) -> int:
    import time
    return time.gmtime(t + TZ).tm_year


def tz_label() -> str:
    """時區位移的文字，例如 UTC+8（位移由 QUOTA_LAB_TZ_OFFSET_HOURS 決定，預設 8＝台灣時間）。"""
    h = TZ / 3600
    return f"UTC{h:+g}"


def json_safe(obj):
    """把 inf／nan 換成 null，讓 json.dumps(allow_nan=False) 過得去（產生嚴格 JSON）。"""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj


def csv_num(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and not math.isfinite(v):
        return ""
    return f"{v}"


class Book:
    """帳本的累計視圖：任一時刻、各模型各類別的累計 API 等值美元與 token。"""

    def __init__(self) -> None:
        global FABLE
        self.rows = sorted(jl(DATA / "ledger.jsonl"), key=lambda r: r["t"])
        # 帳本沒有預設的 Fable 代號、但只有一個 claude-fable 系列時，沿用它
        ids = {str(r.get("model")) for r in self.rows}
        if FABLE not in ids:
            cands = sorted(m for m in ids if m.startswith("claude-fable"))
            if len(cands) == 1:
                FABLE = cands[0]
                SHORT.setdefault(FABLE, "Fable")
                COLOR.setdefault(FABLE, "#7c3aed")
        self.ts = [r["t"] for r in self.rows]
        priced = {r["model"] for r in self.rows if isinstance(r.get("usd"), (int, float))}
        # 帳本裡「有價錢」的模型全部納入；已知的四個照 SHORT 的順序，其餘按字母排在後面
        self.models = [m for m in SHORT if m in priced] + sorted(m for m in priced if m not in SHORT)
        self.keys = [(m, c) for m in self.models for c in CLASSES]
        bad = [r for r in self.rows if not isinstance(r.get("usd"), (int, float)) or not r.get("usd_parts")]
        if bad:
            print(f"警告：帳本有 {len(bad)} 筆缺少 usd 或 usd_parts（照 0 計，無法計價）", file=sys.stderr)
        acc = {k: 0.0 for k in self.keys}
        tok = {k: 0 for k in self.keys}
        self.cum_usd, self.cum_tok = [], []
        for r in self.rows:
            m = r["model"]
            if m in self.models:
                parts = r.get("usd_parts") or {}
                for c in CLASSES:
                    acc[(m, c)] += parts.get(c, 0.0) or 0.0
                    tok[(m, c)] += r.get(c, 0) or 0
            self.cum_usd.append(dict(acc))
            self.cum_tok.append(dict(tok))

    def at(self, t: float, tok: bool = False) -> dict:
        i = bisect.bisect_right(self.ts, t) - 1
        if i < 0:
            return {k: 0 for k in self.keys}
        return (self.cum_tok if tok else self.cum_usd)[i]

    def usd_by_model(self, t: float) -> dict:
        x = self.at(t)
        return {m: sum(x[(m, c)] for c in CLASSES) for m in self.models}


def ledger_empty(bk: "Book") -> bool:
    """帳本是空的（或沒有任何可計價的模型）時印一行說明並回 True；呼叫端以結束碼 3 結束。"""
    if bk.models:
        return False
    print(f"帳本是空的，或裡面沒有可計價的資料（{DATA / 'ledger.jsonl'}）：先用 quota_lab.py ledger 建好帳本，再跑這個子命令。")
    return True


def samples() -> list[dict]:
    return [r for r in jl(DATA / "samples.jsonl") if r.get("ok") and r.get("weekly_all") is not None]


def events() -> list[dict]:
    return jl(DATA / "events.jsonl")


def has_fable(ss: list[dict]) -> bool:
    return any(r.get("scoped_fable") is not None for r in ss)


def fable_meter_unmatched(bk: "Book", ss: list[dict]) -> bool:
    """讀數有 Fable 分項、但帳本沒有 claude-fable 系列的模型：對不上。"""
    return (any((r.get("scoped_fable") or 0) > 0 for r in ss)
            and not any(str(m).startswith("claude-fable") for m in bk.models))


def warn_fable_meter(bk: "Book", ss: list[dict]) -> None:
    if fable_meter_unmatched(bk, ss):
        print("警告：讀數有 Fable 分項，但帳本沒有任何 claude-fable 系列的模型，分項對不上；這次跳過 Fable 分項的限制。",
              file=sys.stderr)


def at_or_before(ss: list[dict], t: float) -> dict:
    """最後一筆 t 之前（含）的讀數；全都比 t 晚就用第一筆（事件可能比讀數還早，例如手動讀數）。"""
    x = [r for r in ss if r["t"] <= t]
    return x[-1] if x else ss[0]


def find_jumps(ss: list[dict]) -> list[dict]:
    out = []
    for field in ("weekly_all", "scoped_fable"):
        prev = None
        for r in ss:
            v = r.get(field)
            if v is None:
                continue
            if prev is not None and v != prev[field]:
                out.append({"meter": field, "from": prev[field], "to": v, "t0": prev["t"], "t1": r["t"]})
            prev = r
    return sorted(out, key=lambda j: j["t1"])


# ---------------------------------------------------------------- phases
def cmd_phases(a) -> int:
    bk, ss, ev = Book(), samples(), events()
    if not ss:
        print("還沒有額度讀數")
        return 1
    marks = [(e["t"], e["label"]) for e in ev] + [(ss[-1]["t"], "now")]
    show_fable = has_fable(ss)
    print(f"{'階段':28} {'起':>8} {'迄':>8} {'總%':>7} " + (f"{'Fable%':>7} " if show_fable else "") + " 各模型 API 等值美元（類別占比）")
    for (t0, lab), (t1, _) in zip(marks, marks[1:]):
        s0, s1 = at_or_before(ss, t0), at_or_before(ss, t1)
        x0, x1 = bk.at(t0), bk.at(t1)
        parts = []
        for m in bk.models:
            d = {c: x1[(m, c)] - x0[(m, c)] for c in CLASSES}
            tot = sum(d.values())
            if tot < 0.005:
                continue
            mix = " ".join(f"{CLASS_ZH[c]}{d[c] / tot * 100:.0f}%" for c in CLASSES if d[c] / tot >= 0.005)
            parts.append(f"{name_of(m)} ${tot:.2f}（{mix}）")
        fable_col = f"{str(s0.get('scoped_fable', '-')):>3}→{str(s1.get('scoped_fable', '-')):<3} " if show_fable else ""
        print(f"{lab:28} {hhmm(t0, False):>8} {hhmm(t1, False):>8} "
              f"{s0['weekly_all']:>3}→{s1['weekly_all']:<3} {fable_col} " + "； ".join(parts))
    return 0


# ---------------------------------------------------------------- cells
def sub_and_host(bk: "Book", t0: float, t1: float) -> tuple:
    """格內子代理（帳本有 agent 代號）各模型的美元，以及主持對話（沒有 agent）的 Fable 美元。"""
    sub: dict = {}
    host = 0.0
    for row in bk.rows:
        if not (t0 < row["t"] <= t1):
            continue
        if row.get("agent"):
            sub[row["model"]] = sub.get(row["model"], 0.0) + (row.get("usd") or 0.0)
        elif row.get("model") == FABLE:
            host += row.get("usd") or 0.0
    return sub, host


def cell_spans(ev: list[dict], ss: list[dict]) -> list[tuple]:
    """每一格的 (名稱, 開始, 結束)；同名開始事件重複記錄時只取第一筆。"""
    out = []
    seen: set = set()
    for e in ev:
        if not e["label"].endswith("-start"):
            continue
        if e["label"] in seen:
            continue
        seen.add(e["label"])
        name, t0 = e["label"][:-6], e["t"]
        ends = [x["t"] for x in ev if x["label"] == name + "-end" and x["t"] >= t0]
        later = [x["t"] for x in ev if x["t"] > t0 and x["label"].endswith("-start")]
        t1 = ends[0] if ends else (later[0] if later else ss[-1]["t"])
        out.append((name, t0, t1))
    return out


def fable_cell_at(bk: "Book", t0: float, t1: float) -> tuple:
    """(這一格是不是 Fable 子代理格, 格內子代理各模型美元, 主持對話美元)。
    主持對話本身也是 Fable，所以用帳本有沒有 agent 代號把兩者分開。"""
    sub, host = sub_and_host(bk, t0, t1)
    return (bool(sub) and max(sub, key=sub.get) == FABLE and sub[FABLE] > 0.5), sub, host


def cells_rows(bk: "Book", ss: list[dict], ev: list[dict], lead: float = 60.0) -> list[dict]:
    """每一格實驗（事件 <名>-start 到 <名>-end）：主模型、用量組成、讀數起訖，
    以及用「格內第一個跳點到最後一個跳點」夾出來的每 1% 美元範圍（至少要夾出 1%）。
    跳點要兩個讀數都在格內才算；lead＝紀錄檔比伺服器計量晚寫入的秒數上限，
    夾住的區間兩端各放寬 lead 秒（上限不放寬超過格子的結束時間）。
    非 Fable 格另外估計主持對話同時段的 Fable 用量：那份用量也推高總額度讀數，
    所以主模型實際用掉的百分點數比讀數差少，上限要往上修。
    usd_per_pct_lo／hi／hi_adj 是取到 0.01 的值（寫進 cells.csv）；以底線開頭的 _lo_raw／_hi_raw／_hi_adj_raw
    是沒取整的原值，只在記憶體裡，顯示一位小數（RESULTS.md、圖）一律從原值取整，免得取整兩次。"""
    js = [j for j in find_jumps(ss) if j["meter"] == "weekly_all"]
    rows = []
    for name, t0, t1 in cell_spans(ev, ss):
        x0, x1 = bk.at(t0), bk.at(t1)
        usd = {m: sum(x1[(m, c)] - x0[(m, c)] for c in CLASSES) for m in bk.models}
        # 這一格是不是「Fable 子代理」格：看格內子代理的用量哪個模型最多。
        fable_cell, sub, host_in_cell = fable_cell_at(bk, t0, t1)
        if fable_cell:
            main = FABLE
            # 受控＝主持對話同時段的 Fable 用量不到子代理的一成。
            # Fable 分項讀數只算 Fable，所以同時段其他模型的用量不影響這一格（仍列在「他模型$」供參考）
            controlled = host_in_cell <= 0.10 * sub[FABLE]
            # Fable 有自己的分項讀數（上限＝總額度的一半）：分項每跳 1 點＝總額度 0.5%
            js_use, per, meter_used = [j for j in find_jumps(ss) if j["meter"] == "scoped_fable"], 0.5, "scoped_fable"
        else:
            guests = [m for m in bk.models if m != FABLE and usd[m] > 0.005]
            if not guests:
                continue
            main = max(guests, key=lambda m: usd[m])
            non_fable = sum(v for m, v in usd.items() if m != FABLE)  # 同一格裡其他模型的用量（不含 Fable）
            controlled = usd[main] >= 0.95 * non_fable
            js_use, per, meter_used = js, 1.0, "weekly_all"
        mix = {c: (x1[(main, c)] - x0[(main, c)]) / usd[main] for c in CLASSES}
        other = sum(v for m, v in usd.items() if m != main)
        r0 = at_or_before(ss, t0)
        r1 = at_or_before(ss, t1)
        # 兩個讀數都在格內才算這一格的跳點；結束時剛好有一跳（前後跨過結束事件）的不算。
        inside = [j for j in js_use if j["t0"] >= t0 and j["t1"] <= t1]  # 兩筆讀數都要落在格內
        straddle_end = any(j["t0"] <= t1 < j["t1"] for j in js_use)
        U = lambda t: sum(bk.at(t)[(main, c)] for c in CLASSES)  # noqa: E731
        span = u_lo = u_hi = None
        host_win = None
        # 不管受不受控都算範圍（混合工作只是不顯示），這樣 kf 的挑選才有東西可挑
        if inside:
            # 分母＝格內第一跳到最後一跳的實際百分點差（跳點可能一次跳好幾個百分點，例如 2→4）
            n = (inside[-1]["to"] - inside[0]["to"]) * per
            if n >= 1:
                span = n
                j0, j1 = inside[0], inside[-1]
                hi_end = min(j1["t1"] + lead, t1)
                u_lo = U(j1["t0"]) - U(min(j0["t1"] + lead, j1["t0"]))
                u_hi = U(hi_end) - U(j0["t0"])
                # 主持對話的 Fable 用量也在同一個放寬後的區間裡（Fable 格只是照實列出，不做修正）
                host_win = sum((row.get("usd") or 0.0) for row in bk.rows
                               if j0["t0"] < row["t"] <= hi_end
                               and row.get("model") == FABLE and not row.get("agent"))
        # 子任務一致性：只算有 agent 代號、且模型＝這一格主模型的列
        per_agent: dict = {}
        for row in bk.rows:
            if t0 < row["t"] <= t1 and row.get("agent") and row["model"] == main:
                per_agent[row["agent"]] = per_agent.get(row["agent"], 0.0) + (row.get("usd") or 0.0)
        agents_n = agents_low_n = agent_med = None
        if per_agent:
            med = statistics.median(list(per_agent.values()))
            # 太小的子任務（不到中位數的 20%）多半是零頭或收尾，不計入
            keep = [v for v in per_agent.values() if v >= 0.20 * med]
            agents_n, agents_low_n = len(keep), sum(1 for v in keep if v < 0.80 * med)
            agent_med = statistics.median(keep)  # 一個子任務的中位數美元（同一份工作跨模型比較用）
        lo = None if (u_lo is None or span is None) else u_lo / span
        hi = None if (u_hi is None or span is None) else u_hi / span
        if lo is not None and lo <= 0:
            lo = hi = None  # 跳太快：兩個跳點之間沒夾出正的下限，整段範圍都不成立
        rows.append({"cell": name, "model": name_of(main), "start": hhmm(t0), "end": hhmm(t1),
                     "usd_main": round(usd[main], 2), "usd_other_models": round(other, 2),
                     **{f"share_{c}": round(mix[c], 3) for c in CLASSES},
                     "meter_from": r0["weekly_all"], "meter_to": r1["weekly_all"],
                     "scoped_from": r0.get("scoped_fable"), "scoped_to": r1.get("scoped_fable"),
                     "jumps_inside": len(inside), "straddle_end": bool(straddle_end),
                     "lead_s": lead,
                     "span_pct": None if span is None else round(span, 2),
                     "meter_used": meter_used, "controlled": bool(controlled),
                     "host_fable_usd_window": None if host_win is None else round(host_win, 2),
                     "usd_per_pct_lo": None if lo is None else round(lo, 2),
                     "usd_per_pct_hi": None if hi is None else round(hi, 2),
                     "usd_per_pct_hi_adj": None,
                     "agents_n": agents_n, "agents_low_n": agents_low_n,
                     "agent_usd_median": None if agent_med is None else round(agent_med, 3),
                     "_u_hi": u_hi, "_span": span, "_fable": fable_cell, "_lo": lo, "_host": host_win,
                     "_lo_raw": lo, "_hi_raw": hi, "_hi_adj_raw": None})
    # 第二輪：用受控 Fable 格裡最小的「每 1% 美元」下限，把非 Fable 格的上限往上修。
    # 主持對話的 Fable 用量也扣總額度，讀數差 n 裡有一部分是主持對話造成的。
    kf = [r["_lo"] for r in rows  # 用沒四捨五入過的值算，最後只取整一次
          if r["_fable"] and r["controlled"] and r["_lo"] is not None and r["_lo"] > 0]
    kf_min = min(kf) if kf else None
    for r in rows:
        adj = r["usd_per_pct_hi"]
        adj_raw = r["_hi_raw"]
        unreliable = False
        if (not r["_fable"]) and kf_min is not None and r["usd_per_pct_lo"] is not None \
                and r["_u_hi"] is not None:
            n_eff = r["_span"] - (r["_host"] or 0.0) / kf_min
            if n_eff < 0.5:
                # 主持對話的用量可能占掉這段讀數差的一半以上：上限算不出來，只有下限可用
                adj, adj_raw, unreliable = None, None, True
            else:
                adj_raw = r["_u_hi"] / n_eff
                adj = round(adj_raw, 2)
        if not r["controlled"]:
            # 混合工作不給範圍：CSV 裡也留空，免得被引用
            r["usd_per_pct_lo"] = r["usd_per_pct_hi"] = None
            r["_lo_raw"] = r["_hi_raw"] = None
            adj, adj_raw, unreliable = None, None, False
        r["usd_per_pct_hi_adj"] = adj
        r["_hi_adj_raw"] = adj_raw
        r["upper_unreliable"] = unreliable
        for k in ("_u_hi", "_span", "_fable", "_lo", "_host"):
            del r[k]
    return rows


def host_only_rate(bk: "Book", ss: list[dict], ev: list[dict]):
    """主持對話單獨在 Fable 分項上的「每 1% 週額度美元」範圍：
    只取第一個 Fable 格開始之前的 scoped_fable 跳點（沒有 Fable 格時取全部），
    至少要有兩個跳點才夾得出來，否則回 None。主持對話的美元＝沒有 agent 的 Fable 列。"""
    js = [j for j in find_jumps(ss) if j["meter"] == "scoped_fable"]
    cutoff = None
    for _name, t0, t1 in cell_spans(ev, ss):
        if fable_cell_at(bk, t0, t1)[0]:
            cutoff = t0
            break
    if cutoff is not None:
        js = [j for j in js if j["t1"] <= cutoff]
    if len(js) < 2:
        return None
    j0, j1 = js[0], js[-1]

    def U(t: float) -> float:
        return sum((row.get("usd") or 0.0) for row in bk.rows
                   if row["t"] <= t and row.get("model") == FABLE and not row.get("agent"))

    n = (j1["to"] - j0["to"]) * 0.5
    if n <= 0:
        return None
    u_lo = U(j1["t0"]) - U(j0["t1"])
    u_hi = U(j1["t1"]) - U(j0["t0"])
    if u_lo <= 0:
        return None
    return u_lo / n, u_hi / n


def f1(x: float) -> str:
    """一位小數、四捨五入（不用浮點數的進位規則，免得 24.95 印成 24.9）。"""
    from decimal import Decimal, ROUND_HALF_UP
    return str(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def exact(r: dict, key: str):
    """顯示用的原值：cells_rows 附在列上、沒取整的 _<key>_raw（key 是 lo、hi、hi_adj）；
    沒有（例如列是別處組的）就退回已取到 0.01 的 usd_per_pct_<key>。顯示一位小數時從這裡取，不要拿取整過的值再取整。"""
    v = r.get(f"_{key}_raw")
    return r.get(f"usd_per_pct_{key}") if v is None else v


def range_text(r: dict) -> str:
    """實驗格範圍的字串（CLI 與 RESULTS.md 共用）。"""
    if not r["controlled"]:
        return "混合工作，不列"
    if r["usd_per_pct_lo"] is None:
        return "跳太快，沒夾出範圍" if r["span_pct"] is not None else "跳點不足 1 個百分點"
    lo, hi, adj = exact(r, "lo"), exact(r, "hi"), exact(r, "hi_adj")
    if r.get("upper_unreliable"):
        lo_floor = math.floor(lo * 10) / 10  # 只講下限時往下取，不能講得比量到的高
        return f"${lo_floor:.1f} 以上（主持對話的用量占了這段的大半，上限不可靠）"
    s = f"${f1(lo)} ~ ${f1(hi)}"
    if adj is not None and adj > hi * 1.03:
        s += f"（扣掉主持對話後上限 ${f1(adj)}）"
    return s


def span_text(r: dict) -> str:
    if r["span_pct"] is None:
        return "—"
    s = f"{r['span_pct']:g} 個百分點"
    if r["span_pct"] == 1:
        s += "（只夾到 1 個，範圍較寬）"
    return s


def agents_text(r: dict) -> str:
    if r["agents_n"] is None:
        return "—"
    return f"{r['agents_n']}（{r['agents_low_n']}）"


def host_text(r: dict) -> str:
    v = r["host_fable_usd_window"]
    return "—" if v is None else f"${v:.2f}"


def read_text(r: dict) -> str:
    """讀數欄：Fable 格看分項（每點＝總額度 0.5%），其他格看總額度。"""
    if r["meter_used"] == "scoped_fable":
        if r.get("scoped_from") is None or r.get("scoped_to") is None:
            return "—"
        return f"分項 {r['scoped_from']}→{r['scoped_to']}"
    return f"{r['meter_from']}→{r['meter_to']}"


def jumps_text(r: dict) -> str:
    s = str(r["jumps_inside"])
    if r.get("straddle_end"):
        s += "（結束時剛好有一跳，未計入）"
    return s


def cmd_cells(a) -> int:
    bk, ss, ev = Book(), samples(), events()
    warn_fable_meter(bk, ss)
    if not ss:
        print("還沒有額度讀數")
        return 1
    lead = float(getattr(a, "lead", 60.0))
    rows = cells_rows(bk, ss, ev, lead=lead)
    if not rows:
        print("沒有可列的實驗格"); return 0
    path = DATA / "cells.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        # 底線開頭的欄（沒取整的原值）只留在記憶體裡，不寫進 cells.csv
        w = csv.DictWriter(f, fieldnames=[k for k in rows[0] if not k.startswith("_")], extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    print(f"{'格':22} {'模型':11} {'主模型$':>8} {'他模型$':>7} {'主持Fable$（夾住區間內）':>11} {'讀數':>11} {'跳點':>4} {'控制':<4} "
          f"{'夾住':<16} {'子任務（偏低）':<12} {'每 1% 美元（格內跳點夾出）':<24} 組成")
    for r in rows:
        mixs = " ".join(f"{CLASS_ZH[c]}{r['share_' + c] * 100:.0f}%" for c in CLASSES if r["share_" + c] >= 0.02)
        print(f"{r['cell']:22} {r['model']:11} {r['usd_main']:>8.2f} {r['usd_other_models']:>7.2f} "
              f"{host_text(r):>10} {read_text(r):>11} {jumps_text(r):>4} {'受控' if r['controlled'] else '混合':<4} "
              f"{span_text(r):<16} {agents_text(r):<12} {range_text(r):<24} {mixs}")
    hr = host_only_rate(bk, ss, ev)
    if hr is not None:
        print(f"主持對話單獨在 Fable 分項上（第一個 Fable 格開始之前）：每 1% 週額度 ${hr[0]:.1f} ~ ${hr[1]:.1f}")
    print(f"→ {path}\n注意：格內若有其他模型同時用量（他模型$），夾出來的範圍會偏低估；嚴謹值以 fit 為準。"
          "受控＝主模型占格內非 Fable 美元 ≥95%（混合工作不列範圍）。"
          "非 Fable 格用總額度讀數，主持對話的 Fable 用量也會推高讀數；差超過 3% 時附上扣掉後的上限。"
          f"夾住的範圍容許紀錄檔比計量晚寫 {lead:g} 秒；--lead 0 給不含這個容許的較窄範圍。")
    return 0


# ---------------------------------------------------------------- jumps
def cmd_jumps(a) -> int:
    bk, ss = Book(), samples()
    if not ss:
        print("還沒有額度讀數")
        return 1
    js = find_jumps(ss)
    path = DATA / "jumps.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["meter", "from", "to", "local_before", "local_after", "gap_s"]
                   + [f"usd_{name_of(m)}_{x}" for m in bk.models for x in ("before", "after")]
                   + [f"usd_{name_of(m)}_{c}_after" for m in bk.models for c in CLASSES])
        for j in js:
            u0, u1, x1 = bk.usd_by_model(j["t0"]), bk.usd_by_model(j["t1"]), bk.at(j["t1"])
            w.writerow([j["meter"], j["from"], j["to"], hhmm(j["t0"]), hhmm(j["t1"]), round(j["t1"] - j["t0"])]
                       + [round(v, 3) for m in bk.models for v in (u0[m], u1[m])]
                       + [round(x1[(m, c)], 3) for m in bk.models for c in CLASSES])
            print(f"{j['meter']:13} {j['from']:>2}→{j['to']:<2} {hhmm(j['t0'])} ~ {hhmm(j['t1'])[6:]} "
                  f"({round(j['t1'] - j['t0']):>3}s)  "
                  + "  ".join(f"{name_of(m)} ${u0[m]:.2f}~{u1[m]:.2f}" for m in bk.models if u1[m] > 0.005))
    print(f"→ {path}")
    return 0


# ---------------------------------------------------------------- fit
ROUNDING = {"floor": 0.0, "round": 0.5, "ceil": 1.0}  # 讀數 v 代表真值落在 [v-位移, v+1-位移)
ROUNDING_ZH = {"floor": "無條件捨去", "round": "四捨五入", "ceil": "無條件進位"}


def build_constraints(bk: Book, ss: list[dict], lag: float, lead: float = 0.0, offset: float = 0.0,
                      start: str = "zero"):
    """回傳 (A, b, note, starts)：A·θ ≤ b；θ 順序＝bk.keys。只留每個讀數值的第一筆與最後一筆（其餘是多餘限制）。
    offset＝取整位移：0 無條件捨去、0.5 四捨五入、1 無條件進位（官方沒有說明讀數怎麼取整，三種都要測）。
    start="free" 時多兩個變數：w0（帳本開始時總額度表上已經有的百分點數）與 f0（分項表上已經有的點數），
    每個讀數都加上它；starts 回傳 [(變數名, 上界), …]，start="zero" 時是空 list。"""
    import numpy as np
    A, b, note = [], [], []
    nk = len(bk.keys)
    rw = next((r["weekly_all"] for r in ss if r.get("weekly_all") is not None), None)
    rf = next((r["scoped_fable"] for r in ss if r.get("scoped_fable") is not None), None)
    starts = []
    if start == "free":
        if rw is not None:
            # 讀數 rw 只說真值在 rw+1-offset 以下，所以起跑值最大就到那裡
            starts.append(("w0", float(rw) + 1.0 - offset))
        if rf is not None and not fable_meter_unmatched(bk, ss):
            starts.append(("f0", float(rf) + 1.0 - offset))
    idx = {nm: nk + i for i, (nm, _hi) in enumerate(starts)}

    def vec(t: float, fable_only: bool) -> "np.ndarray":
        x = bk.at(t)
        scale = 2.0 if fable_only else 1.0
        return np.array([scale * x[k] if (not fable_only or k[0] == FABLE) else 0.0 for k in bk.keys])

    def add(t: float, fo: bool, sign: str, rhs: float, field: str, v: int) -> None:
        """「用量 ≥ 讀數」兩邊乘 -1 就變成 ≤ 的形式；起跑值跟用量同一邊，係數跟著正負。"""
        s = -1.0 if sign == "≥" else 1.0
        vals = list(vec(t, fo) * s) + [0.0] * len(starts)
        col = "w0" if field == "weekly_all" else "f0"
        if col in idx:  # 用量＝θ·美元＋起跑值
            vals[idx[col]] = s
        A.append(vals); b.append(s * rhs); note.append((field, sign, v, t))

    for field, fo in (("weekly_all", False), ("scoped_fable", True)):
        if field == "scoped_fable" and fable_meter_unmatched(bk, ss):
            continue  # 帳本沒有 Fable 系列模型時，分項讀數無從對起，先跳過
        first, last = {}, {}
        for r in ss:
            v = r.get(field)
            if v is None:
                continue
            first.setdefault(v, r["t"])
            last[v] = r["t"]
        for v, t in first.items():
            if v - offset > 0:  # 用量(t) ≥ v-位移
                # lead：伺服器可能在本機紀錄檔寫入之前就先計量（請求送出時先算輸入，回應結束才寫紀錄檔）
                add(t + lead, fo, "≥", float(v) - offset, field, v)
        for v, t in last.items():  # 用量(t-lag) ≤ v+1-位移
            add(t - lag, fo, "≤", float(v + 1) - offset, field, v)
    if "w0" in idx and "f0" in idx:  # 分項是總額度的一部分：f0 點＝f0/2 個百分點，不能超過 w0
        vals = [0.0] * (nk + len(starts))
        vals[idx["w0"]] = -1.0
        vals[idx["f0"]] = 0.5
        A.append(vals); b.append(0.0); note.append(("start", "≤", 0, 0.0))
    return np.array(A), np.array(b), note, starts


def apply_tolerances(A, b, note, slack: float, unlogged: float, weekly_only: bool = False):
    """slack＝每條限制對稱放寬的百分點；unlogged＝只放寬「用量 ≥ 讀數」那半邊（帳本沒記到的用量）。
    weekly_only=True 時只放寬總額度表的「用量 ≥ 讀數」，分項表不動。"""
    import numpy as np
    b = b + slack
    if unlogged:
        b = b + np.array([unlogged if (nt[1] == "≥" and (not weekly_only or nt[0] == "weekly_all")) else 0.0
                          for nt in note])
    return A, b


def solve(bk: Book, A, b, tie: str, mult: dict | None = None, slack: bool = False,
          starts: list | None = None):
    """tie: 'one'＝全部模型同一個 K；'model'＝每個模型一個 K（類別間照 API 價格比例）；'free'＝每個模型每個類別各自一個權重。
    mult: 類別倍率（相對 API 價格），例如 {'cr': 0.5}。starts＝build_constraints 回傳的起跑偏移欄位。
    回傳各參數的可行範圍（線性規劃逐一求最小／最大）；起跑偏移的範圍放在 "_start"。"""
    import numpy as np
    from scipy.optimize import linprog
    mult = mult or {}
    starts = starts or []
    nk = len(bk.keys)
    if tie == "one":
        names = ["all"]
        M = np.array([[mult.get(c, 1.0)] for (_m, c) in bk.keys])
    elif tie == "model":
        names = list(bk.models)
        M = np.zeros((nk, len(names)))
        for i, (m, c) in enumerate(bk.keys):
            M[i, names.index(m)] = mult.get(c, 1.0)
    else:
        names = [f"{m}|{c}" for (m, c) in bk.keys]
        M = np.eye(nk)
    A2 = np.hstack([A[:, :nk] @ M, A[:, nk:]])
    n = len(names)
    # 起跑偏移是輔助變數：算 θ 的範圍時一起放進限制式，但不算模型參數
    bounds = [(0, None)] * n + [(0.0, hi) for (_nm, hi) in starts]
    if slack:  # 允許每條限制有 s 個百分點的誤差，求最小的 s
        A3 = np.hstack([A2, -np.ones((A2.shape[0], 1))])
        res = linprog(np.r_[np.zeros(n + len(starts)), 1.0],
                      A_ub=A3, b_ub=b, bounds=bounds + [(0, None)], method="highs")
        return {"min_slack": float(res.x[-1]) if res.success else None}
    out = {}
    for i, nm in enumerate(names):
        c = np.zeros(n + len(starts)); c[i] = 1.0
        lo = linprog(c, A_ub=A2, b_ub=b, bounds=bounds, method="highs")
        hi = linprog(-c, A_ub=A2, b_ub=b, bounds=bounds, method="highs")
        if lo.status == 2 or hi.status == 2:  # 2＝無解（與讀數矛盾）
            return None
        # 3＝無上界：這個參數沒有被任何讀數限制到（例如該類別用量為零）
        out[nm] = (float(lo.x[i]) if lo.success else 0.0, float(hi.x[i]) if hi.success else INF)
    for j, (nm, _hi) in enumerate(starts):
        c = np.zeros(n + len(starts)); c[n + j] = 1.0
        lo = linprog(c, A_ub=A2, b_ub=b, bounds=bounds, method="highs")
        hi = linprog(-c, A_ub=A2, b_ub=b, bounds=bounds, method="highs")
        out.setdefault("_start", {})[nm] = (float(lo.x[n + j]) if lo.success else 0.0,
                                           float(hi.x[n + j]) if hi.success else INF)
    return out


def theta_to_usd_per_pct(lo: float, hi: float) -> tuple:
    """θ（每美元扣幾個百分點）的範圍 → 每 1% 幾美元。"""
    if hi == INF:
        k_lo = 0.0
    elif hi > 1e-12:
        k_lo = 1.0 / hi
    else:
        k_lo = INF
    k_hi = 1.0 / lo if lo > 1e-12 else INF
    return k_lo, k_hi


def fmt_k(rng: tuple) -> str:
    """每 1% 美元的範圍；外推 100% 只是把同一個範圍線性放大，不是預測。"""
    k_lo, k_hi = theta_to_usd_per_pct(*rng)
    s_lo = "0" if k_lo == 0.0 else f"{k_lo:.2f}"
    s_hi = "∞" if k_hi == INF else f"{k_hi:.2f}"
    s = f"每 1% ${s_lo} ~ ${s_hi}"
    if math.isfinite(k_hi):
        s += f"；線性外推到 100%：${k_lo * 100:,.0f} ~ ${k_hi * 100:,.0f}（推估）"
    return s


def fit_once(bk: Book, ss: list[dict], lag: float, lead: float, slack: float, unlogged: float,
             rounding: str = "floor", start: str = "free", weekly_only: bool = False) -> dict:
    """跑三個假設，回傳 {"one"/"model"/"free": 可行時 {'feasible':True,'theta':…,'start_offsets':…}，
    不可行時 {'feasible':False,'extra_tolerance':…,'total_tolerance':…}}，外加 n_constraints。
    start="free" 假設兩個表起跑時的讀數真值未知（不超過第一筆讀數加 1 點）；"zero" 假設剛好是 0。"""
    A, b, note, starts = build_constraints(bk, ss, lag, lead, ROUNDING[rounding], start=start)
    A, b = apply_tolerances(A, b, note, slack, unlogged, weekly_only=weekly_only)
    out: dict = {"n_constraints": int(len(b))}
    for tie in ("one", "model", "free"):
        r = solve(bk, A, b, tie, starts=starts)
        if r is not None:
            offs = r.pop("_start", {})
            out[tie] = {"feasible": True, "theta": r}
            if offs:
                out[tie]["start_offsets"] = offs
            continue
        s = solve(bk, A, b, tie, slack=True, starts=starts)
        extra = s["min_slack"]
        out[tie] = {"feasible": False, "extra_tolerance": extra,
                    "total_tolerance": None if extra is None else slack + extra}
    return out


def unlogged_needed(bk: "Book", ss: list[dict], tie: str, weekly_only: bool = False,
                    start: str = "free") -> float | None:
    """二分搜尋：要放寬多少個百分點的「帳本沒記到的用量」，這個假設才與讀數相容。
    容差 0.001，回傳向上取到 0.01 的值（拿這個值去跑 fit 一定可行）。
    weekly_only=True 只放寬總額度表的「用量 ≥ 讀數」，最多找到 40 個百分點；否則 20 個百分點還不行就回 None。"""
    limit = 40.0 if weekly_only else 20.0

    def ok(u: float) -> bool:
        return fit_once(bk, ss, 0.0, 60.0, 0.1, u, start=start, weekly_only=weekly_only)[tie]["feasible"]

    if not ok(limit):
        return None
    lo, hi = 0.0, limit
    while hi - lo > 0.001:
        mid = (lo + hi) / 2
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return math.ceil(hi * 100) / 100


def cmd_fit(a) -> int:
    bk, ss = Book(), samples()
    if not ss:
        print("還沒有額度讀數")
        return 1
    if ledger_empty(bk):
        return 3
    warn_fable_meter(bk, ss)
    res = fit_once(bk, ss, a.lag, a.lead, a.slack, a.unlogged, a.rounding, start=a.start)
    res.update({"lag": a.lag, "lead": a.lead, "slack": a.slack, "unlogged": a.unlogged, "rounding": a.rounding,
                "start": a.start, "n_samples": len(ss), "last_sample_ts": ss[-1]["ts"]})
    print(f"讀數 {len(ss)} 筆 → 限制 {res['n_constraints']} 條；假設讀數延遲上限 {a.lag:.0f} 秒、紀錄檔落後上限 {a.lead:.0f} 秒；模型：{', '.join(name_of(m) for m in bk.models)}")
    print("起跑值：" + ("未知，兩個表各自不超過第一筆讀數加 1 點（w0、f0 由線性規劃求）" if a.start == "free"
                     else "假設兩個表起跑時剛好是 0"))
    if a.rounding != "floor":
        print(f"讀數取整方式假設為：{ROUNDING_ZH[a.rounding]}")
    if a.slack:
        print(f"每條限制對稱放寬 {a.slack:.2f} 個百分點")
    if a.unlogged:
        print(f"「用量 ≥ 讀數」那半邊另外放寬 {a.unlogged:.2f} 個百分點（帳本沒記到的用量）")

    def show(tie: str, title: str) -> None:
        r = res[tie]
        print("\n" + title)
        if not r["feasible"]:
            if r["extra_tolerance"] is None:
                print("  ✗ 與讀數矛盾（無解），連放寬也找不到最小誤差")
            else:
                print(f"  ✗ 與讀數矛盾（無解）。還要再多 {r['extra_tolerance']:.2f} 個百分點，"
                      f"合計要容許 {r['total_tolerance']:.2f} 個百分點")
            return
        for nm, rng in r["theta"].items():
            print(f"  ✓ {name_of(nm) if nm != 'all' else '全部模型':10} {fmt_k(rng)}")

    show("one", "假設一：所有模型共用同一個換算（完全照 API 價格扣）")
    show("model", "假設二：每個模型各有自己的換算（模型內各類 token 照 API 價格比例）")
    r3 = res["free"]
    print("\n假設三：每個模型的每一類 token 各自一個換算（最寬鬆）")
    last = bk.at(ss[-1]["t"])
    if not r3["feasible"]:
        if r3["extra_tolerance"] is None:
            print("  ✗ 連最寬鬆的模型都與讀數矛盾（代表有讀數延遲或帳本沒記到的用量）")
        else:
            print(f"  ✗ 連最寬鬆的模型都與讀數矛盾，還要再多 {r3['extra_tolerance']:.2f} 個百分點，"
                  f"合計要容許 {r3['total_tolerance']:.2f} 個百分點（代表有讀數延遲或帳本沒記到的用量）")
    else:
        for (m, c) in bk.keys:
            if last[(m, c)] < 1.0:  # 累計不到 1 美元的類別，資料不足以限制
                continue
            k_lo, k_hi = theta_to_usd_per_pct(*r3["theta"][f"{m}|{c}"])
            print(f"  ✓ {name_of(m):10} {CLASS_ZH[c]:12} 累計 ${last[(m, c)]:>7.2f}  每 1% ≈ "
                  f"${'0' if k_lo == 0.0 else f'{k_lo:.1f}'} ~ ${'∞' if k_hi == INF else f'{k_hi:.1f}'}")
    # 類別倍率掃描：快取讀取、1 小時快取寫入、5 分鐘快取寫入相對 API 價格的倍率，哪些值與讀數相容
    A, b, note, starts = build_constraints(bk, ss, a.lag, a.lead, ROUNDING[a.rounding], start=a.start)
    A, b = apply_tolerances(A, b, note, a.slack, a.unlogged)
    grid = [x / 20 for x in range(0, 121)]  # 0 ~ 6，每 0.05 一格
    for cls in ("cr", "cw1h", "cw5m"):
        ok = [g for g in grid if solve(bk, A, b, "model", {cls: g}, starts=starts) is not None]
        if not ok:
            rng = "無（任何倍率都矛盾）"
        elif max(ok) >= grid[-1]:
            rng = f"{min(ok):.2f} ~ ≥ {grid[-1]:.2f}（掃描上限）"
        else:
            rng = f"{min(ok):.2f} ~ {max(ok):.2f}"
        print(f"\n「{CLASS_ZH[cls]}」相對 API 價格的倍率（其餘照 API 比例、每模型各自換算）可行範圍：{rng}")
        res[f"mult_{cls}"] = ok
    outdir = DATA / "fit"
    outdir.mkdir(parents=True, exist_ok=True)
    suffix = "" if a.rounding == "floor" else f"_{a.rounding}"  # 預設（無條件捨去）沿用舊檔名
    if a.start == "zero":  # 預設（起跑值未知）不加後綴
        suffix += "_start0"
    path = outdir / f"fit_lag{a.lag:g}_lead{a.lead:g}_slack{a.slack:g}_unlogged{a.unlogged:g}{suffix}.json"
    path.write_text(json.dumps(json_safe(res), ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
    print(f"→ {path}")
    return 0


# ---------------------------------------------------------------- sweep
SWEEP_LEADS = [0, 30, 60, 90, 120, 180, 240, 300]
SWEEP_SLACKS = [0, 0.1, 0.25]


def sweep_rows(bk: Book, ss: list[dict]) -> list[dict]:
    """lead × slack 掃描（lag 0、unlogged 0）：每個設定三個假設的結果，free 可行時附各類別範圍。"""
    out = []
    last = bk.at(ss[-1]["t"])
    for lead in SWEEP_LEADS:
        for slack in SWEEP_SLACKS:
            res = fit_once(bk, ss, 0.0, float(lead), float(slack), 0.0)
            ranges = {}
            fr = res["free"]
            if fr["feasible"]:
                for (m, c) in bk.keys:
                    if last[(m, c)] < 1.0:
                        continue
                    k_lo, k_hi = theta_to_usd_per_pct(*fr["theta"][f"{m}|{c}"])
                    ranges[(m, c)] = (k_lo, k_hi)
            out.append({"lead": lead, "slack": slack, "res": res, "ranges": ranges})
    return out


def tolerance_of(r: dict):
    if r["feasible"]:
        return None
    return r.get("total_tolerance")


def cmd_sweep(a) -> int:
    bk, ss = Book(), samples()
    if not ss:
        print("還沒有額度讀數")
        return 1
    if ledger_empty(bk):
        return 3
    rows = sweep_rows(bk, ss)
    outdir = DATA / "fit"
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / "sweep.csv"
    print(f"{'lead':>5} {'slack':>6}  " + "  ".join(f"{h:>26}" for h in ("one", "model", "free")))
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["lead", "slack", "hypothesis", "feasible", "total_tolerance",
                    "model", "token_class", "usd_per_pct_lo", "usd_per_pct_hi"])
        for row in rows:
            cells = []
            for tie in ("one", "model", "free"):
                r = row["res"][tie]
                tol = tolerance_of(r)
                cells.append("可行" if r["feasible"] else
                             (f"還需 {tol:.2f}" if tol is not None else "無解"))
                w.writerow([row["lead"], row["slack"], tie, r["feasible"],
                            csv_num(tol), "", "", "", ""])
            print(f"{row['lead']:>5} {row['slack']:>6}  " + "  ".join(f"{c:>26}" for c in cells))
            for (m, c), (k_lo, k_hi) in sorted(row["ranges"].items()):
                print(f"      {name_of(m):10} {CLASS_ZH[c]:12} 每 1% ≈ "
                      f"${'0' if k_lo == 0.0 else f'{k_lo:.2f}'} ~ ${'∞' if k_hi == INF else f'{k_hi:.2f}'}")
                w.writerow([row["lead"], row["slack"], "free", True, "", m, c,
                            csv_num(k_lo), csv_num(k_hi)])
    print(f"→ {path}")
    return 0


# ---------------------------------------------------------------- checks
def cmd_checks(a) -> int:
    bk, ss = Book(), samples()
    # 1. 總額度與 Fable 分項同時變動的間隔：只列資訊。
    #    這裡不能拿來證明讀數怎麼取整，所以不給 OK/WARN。
    pairs = []
    prev = None
    for r in ss:
        if prev is not None and prev.get("weekly_all") != r.get("weekly_all") \
                and prev.get("scoped_fable") != r.get("scoped_fable") \
                and r.get("scoped_fable") is not None:
            pairs.append((prev, r))
        prev = r
    fable_only = []
    for p0, p1 in pairs:
        inside = [row for row in bk.rows if p0["t"] < row["t"] <= p1["t"]]
        tot = sum((row.get("usd") or 0.0) for row in inside)
        fable = sum((row.get("usd") or 0.0) for row in inside if row.get("model") == FABLE)
        if tot > 0 and fable >= 0.95 * tot:
            fable_only.append((p0, p1))
    print(f"INFO 總額度與 Fable 分項同時變動的間隔：{len(fable_only)} 組"
          "（間隔內帳本 ≥95% 的美元是 Fable）")
    for p0, p1 in fable_only:
        print(f"     {hhmm(p0['t'])} → {hhmm(p1['t'])}：總 {p0['weekly_all']}→{p1['weekly_all']}%、"
              f"Fable {p0.get('scoped_fable')}→{p1.get('scoped_fable')}%")
    print("     這只是間隔清單，不能用來推論讀數的取整方式。")
    # 2. 污染：任何 seven_day_breakdown 裡非 claude_code 的 row 且 percent > 0
    bad = []
    for rec in jl(DATA / "payload_changes.jsonl"):
        pl = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
        bl = pl.get("seven_day_breakdown") if isinstance(pl.get("seven_day_breakdown"), dict) else {}
        for row in (bl.get("rows") or []):
            if isinstance(row, dict) and row.get("key") != "claude_code" and (row.get("percent") or 0) > 0:
                bad.append((rec.get("ts"), "payload", row.get("key"), row.get("percent")))
    for s in ss:
        bd = s.get("bd")
        if isinstance(bd, dict):
            for k, v in bd.items():
                if k != "claude_code" and (v or 0) > 0:
                    bad.append((s.get("ts"), "sample", k, v))
    print(f"{'OK' if not bad else 'WARN'} seven_day_breakdown 污染（非 claude_code 且 >0）：{len(bad)} 筆")
    for ts, src, k, v in bad[:5]:
        print(f"     {ts} [{src}] {k}={v}%")
    # 3. 帳本基本盤
    rows = jl(DATA / "ledger.jsonl")
    no_price = sorted({r.get("model") for r in rows if not isinstance(r.get("usd"), (int, float))})
    no_usd = sum(1 for r in rows if not isinstance(r.get("usd"), (int, float)))
    if no_price:
        print(f"WARN 帳本 {len(rows)} 筆；缺牌價的模型 {len(no_price)} 個（{', '.join(no_price)}）；缺 usd 的列 {no_usd} 筆")
    else:
        print(f"OK 帳本 {len(rows)} 筆；缺牌價的模型 0 個；缺 usd 的列 {no_usd} 筆")
    # 4. 取樣空窗
    gaps = [b["t"] - a["t"] for a, b in zip(ss, ss[1:])]
    big = [g for g in gaps if g > 300]
    longest = max(gaps) if gaps else 0.0
    print(f"{'OK' if not big else 'WARN'} 取樣空窗：超過 300 秒的有 {len(big)} 段；最長 {longest:.0f} 秒")
    return 0


# ---------------------------------------------------------------- summary
def cmd_summary(a) -> int:
    bk, ss, ev = Book(), samples(), events()
    if not ss:
        print("還沒有額度讀數")
        return 1
    if ledger_empty(bk):
        return 3
    warn_fable_meter(bk, ss)
    last = ss[-1]
    t1 = last["t"]
    cum = bk.at(t1)
    L = []
    L.append("> 本檔案由 `analyze.py summary` 自動產生，請勿手改；「資料截至」時間＝最後一筆讀數的時間。\n")
    L.append("# 額度換算結果摘要\n")
    tw = TZ == 8 * 3600
    L.append(f"資料截至 {full_time(t1)}（{'台灣時間，' if tw else ''}{tz_label()}），為最後一筆讀數的時間。\n")
    L.append("## 最新讀數\n")
    L.append(f"- 每週所有模型：**{last.get('weekly_all')}%**")
    if has_fable(ss):
        fable_now = last.get("scoped_fable")
        L.append(f"- 每週 Fable：**{'無' if fable_now is None else f'{fable_now}%'}**（上限＝總額度的一半）")
    else:
        L.append("- 每週 Fable：讀數裡沒有這個欄位（手動讀數），略過。")
    u7, u5 = last.get("util7d"), last.get("util5h")
    L.append(f"- 7 天利用率：{'無' if u7 is None else f'{u7}%'}；5 小時 session：{'無' if u5 is None else u5}\n")
    L.append("## 各模型累計 API 等值美元（依 token 類別）\n")
    L.append("| 模型 | " + " | ".join(CLASS_ZH[c] for c in CLASSES) + " | 合計 |")
    L.append("|---" * (len(CLASSES) + 2) + "|")
    tot_all = 0.0
    for m in bk.models:
        vals = [cum[(m, c)] for c in CLASSES]
        tot = sum(vals)
        tot_all += tot
        L.append(f"| {name_of(m)} | " + " | ".join(f"${v:.2f}" for v in vals) + f" | **${tot:.2f}** |")
    L.append(f"| **合計** | " + " | ".join("—" for _ in CLASSES) + f" | **${tot_all:.2f}** |\n")
    L.append("## 實驗格（cells）\n")
    rows = cells_rows(bk, ss, ev)
    if rows:
        L.append(f"名詞：主持對話（發動並記錄這次實驗的那個 Claude Code 對話，用的是 {name_of(FABLE)}）；"
                 "跳點＝額度讀數往上跳的時刻；夾住＝用格內第一個和最後一個跳點，把中間這段時間框起來，"
                 "「夾住」欄寫的是這兩個跳點之間差了幾個百分點，「夾住區間」就是被框住的那段時間。\n")
        years = sorted({year_of(t) for _n, c0, c1 in cell_spans(ev, ss) for t in (c0, c1)})
        years_zh = f"{years[0]}" if len(years) == 1 else f"{years[0]} 至 {years[-1]}"
        L.append(f"下表「起」「迄」兩欄的時間是 {years_zh} 年的{'台灣時間' if tw else '當地時間'}（{tz_label()}），"
                 "格式為「月-日 時:分:秒」。\n")
        L.append("| 格 | 模型 | 起 | 迄 | 主模型$ | 他模型$ | 主持Fable$（夾住區間內） | 讀數 | 跳點 | 夾住 | 子任務（偏低） | 控制 | 每 1% 美元 |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            L.append(f"| {r['cell']} | {r['model']} | {r['start']} | {r['end']} | ${r['usd_main']:.2f} | "
                     f"${r['usd_other_models']:.2f} | {host_text(r)} | {read_text(r)} | {jumps_text(r)} | "
                     f"{span_text(r)} | {agents_text(r)} | "
                     f"{'受控' if r['controlled'] else '混合'} | {range_text(r)} |")
        L.append("")
        L.append("- 範圍只用每格的第一個與最後一個跳點，只夾到 1 個百分點的格範圍會很寬。")
        L.append("- 上面的範圍容許本機紀錄檔比伺服器計量晚最多 60 秒寫入；`analyze.py cells --lead 0` "
                 "會給出不含這個容許的較窄範圍。")
        L.append("- Fable 格用 Fable 分項讀數，分項 1 點等於總額度 0.5%；其他格用總額度讀數。")
        L.append("- 這些範圍與讀數怎麼取整無關。")
        kf_lo = [exact(r, "lo") for r in rows
                 if r["meter_used"] == "scoped_fable" and r["controlled"]
                 and r["usd_per_pct_lo"] is not None]
        kf_min = min(kf_lo) if kf_lo else None
        hr = host_only_rate(bk, ss, ev)
        if kf_min is not None:
            L.append(f"- 主持對話的修正是把夾住區間內主持對話的 Fable 美元，用受控 Fable 格裡最小的下限"
                     f"（每 1% ${kf_min:.2f}）換成百分點；這是保守的選法，因為主持對話單獨在 Fable 分項上、"
                     f"在任何 Fable 格開始之前，每 1% 的美元更高"
                     + (f"（${hr[0]:.1f} ~ ${hr[1]:.1f}）" if hr else "（資料不足，夾不出來）")
                     + "，所以換出來的百分點偏多；只有上限會動，下限不變。")
        L.append("- 子任務數只算子代理（帳本有 agent 代號）裡，同一模型、格內合計美元≥中位數 20% 的；"
                 "括號裡是其中低於中位數 80% 的數量。20% 那個門檻是濾掉收尾的零頭，80% 那個門檻是「明顯偏低」。")
        L.append("")
    else:
        L.append("（沒有可列的實驗格）\n")
    L.append("## fit（lag 0、lead 60、slack 0.1、unlogged 0；起跑值未知）\n")
    res = fit_once(bk, ss, 0.0, 60.0, 0.1, 0.0)
    L.append(f"限制 {res['n_constraints']} 條、讀數 {len(ss)} 筆。這張表假設兩個表起跑時的讀數真值未知（不超過第一筆讀數加 1 點）。\n")
    # 起跑值的影響：同一組參數下，假設真值剛好是 0 會多需要多少容許量
    zero = fit_once(bk, ss, 0.0, 60.0, 0.1, 0.0, start="zero")
    L.append("| 起跑假設 | 假設一 | 假設二 | 假設三 |")
    L.append("|---|---|---|---|")
    for tag, rr in (("起跑時讀數真值為 0", zero), ("起跑值未知（不超過第一筆讀數加 1 點）", res)):
        vals_ = []
        for tie in ("one", "model", "free"):
            tol = 0.0 if rr[tie]["feasible"] else rr[tie].get("total_tolerance")
            vals_.append("無解" if tol is None else (f"{tol:.2f}"))
        L.append(f"| {tag} | " + " | ".join(vals_) + " |")
    L.append("")
    for tie, title in (("one", "假設一：所有模型共用同一個換算"),
                       ("model", "假設二：每個模型各有自己的換算"),
                       ("free", "假設三：每個模型每一類 token 各自一個換算")):
        r = res[tie]
        if not r["feasible"]:
            tol = r.get("total_tolerance")
            L.append(f"- **{title}**：✗ 與讀數矛盾" + (f"（合計要容許 {tol:.2f} 個百分點）" if tol is not None else "（無解）"))
            continue
        L.append(f"- **{title}**：✓ 可行")
        if tie in ("one", "model"):
            for nm, rng in r["theta"].items():
                L.append(f"    - {name_of(nm) if nm != 'all' else '全部模型'}：{fmt_k(rng)}")
        else:
            for (m, c) in bk.keys:
                if cum[(m, c)] < 1.0:
                    continue
                k_lo, k_hi = theta_to_usd_per_pct(*r["theta"][f"{m}|{c}"])
                L.append(f"    - {name_of(m)}／{CLASS_ZH[c]}（累計 ${cum[(m, c)]:.2f}）：每 1% ≈ "
                         f"${'0' if k_lo == 0.0 else f'{k_lo:.2f}'} ~ ${'∞' if k_hi == INF else f'{k_hi:.2f}'}")
    L.append("")
    L.append("## 帳本沒記到的用量要多大，假設才救得回來（lag 0、lead 60、slack 0.1、無條件捨去）\n")
    L.append("容許量加在每一個讀數上，單位是那個讀數自己的單位：總額度表是週額度的百分點，"
             "Fable 分項表是分項點數（1 點＝週額度的 0.5%）。表上印的是向上取到 0.01 的值，拿同一個值去跑 fit 一定可行。\n")
    latest = last.get("weekly_all") or 0
    for tie, title in (("one", "假設一"), ("model", "假設二"), ("free", "假設三")):
        u = unlogged_needed(bk, ss, tie)
        u2 = unlogged_needed(bk, ss, tie, weekly_only=True)
        a_txt = (f"**{u:.2f} 個百分點**" if u is not None else "20 個百分點以內救不回來")
        b_txt = (f"**{u2:.2f} 個百分點**" if u2 is not None else "放寬到 40 個百分點也救不回來")
        pct = f"，約最新週讀數的 {u / latest * 100:.1f}%" if (u is not None and latest) else ""
        L.append(f"- {title}：所有「用量 ≥ 讀數」都放寬要 {a_txt}{pct}；"
                 f"只放寬總額度表的話，{b_txt}。")
    L.append("")
    L.append("這個參數是把每一條「用量 ≥ 讀數」的限制都放寬同一個常數，形狀不真實，只用來看量級。")
    L.append("")
    L.append("## 讀數取整方式的影響（lag 0、lead 60、slack 0.1、unlogged 0）\n")
    L.append("官方沒有說明百分比怎麼取整。下表是三種取整方式下，各假設要與讀數相容所需的合計容許量（百分點；0＝可行）。"
             "實驗格表的「每 1% 美元」是用兩個跳點之間的差算的，不受取整方式影響。\n")
    L.append("| 取整方式 | 假設一 | 假設二 | 假設三 |")
    L.append("|---|---|---|---|")
    for rd in ("floor", "round", "ceil"):
        rr = res if rd == "floor" else fit_once(bk, ss, 0.0, 60.0, 0.1, 0.0, rd)
        cells_ = []
        for tie in ("one", "model", "free"):
            tol = 0.0 if rr[tie]["feasible"] else rr[tie].get("total_tolerance")
            cells_.append("無解" if tol is None else ("可行" if tol == 0.0 else f"{tol:.2f}"))
        L.append(f"| {ROUNDING_ZH[rd]} | " + " | ".join(cells_) + " |")
    L.append("")
    L.append("## 放寬時間誤差（lag 600、lead 300、slack 0.1、無條件捨去）\n")
    wide = fit_once(bk, ss, 600.0, 300.0, 0.1, 0.0, start="free")
    vals_ = []
    for tie in ("one", "model", "free"):
        tol = 0.0 if wide[tie]["feasible"] else wide[tie].get("total_tolerance")
        vals_.append("無解" if tol is None else (f"{tol:.2f}"))
    L.append("| 假設一 | 假設二 | 假設三 |")
    L.append("|---|---|---|")
    L.append("| " + " | ".join(vals_) + " |")
    L.append("")
    L.append("這一組是很寬的時間容許值，用來看結論會不會因為讀數延遲而翻掉。")
    L.append("")
    L.append("## sweep（lead × slack；lag 0、unlogged 0）\n")
    sw = sweep_rows(bk, ss)
    free0 = [r for r in sw if r["slack"] == 0 and r["res"]["free"]["feasible"]]
    if free0:
        L.append(f"- slack 0 時，假設三可行的最小 lead：**{min(r['lead'] for r in free0)} 秒**")
    else:
        L.append("- slack 0 時，掃描範圍（lead 0–300 秒）內假設三都不可行。")
    for tie, title in (("one", "假設一"), ("model", "假設二")):
        tols = [r["res"][tie].get("total_tolerance") for r in sw if not r["res"][tie]["feasible"]]
        tols = [t for t in tols if t is not None]
        if tols:
            L.append(f"- {title} 需要的最小～最大總容許量：**{min(tols):.2f} ~ {max(tols):.2f}** 個百分點")
        else:
            L.append(f"- {title} 在整個掃描裡都可行（不需要額外容許量）")
    L.append("")
    out = (DATA / "RESULTS.md") if os.environ.get("QUOTA_LAB_DATA") else (ROOT / "RESULTS.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"→ {out}")
    return 0


# ---------------------------------------------------------------- charts
def label_left_of_bar(x_start: float, xmax: float) -> bool:
    """橫條的標籤會落在軸的右邊 40% 就改畫在左邊，免得跑出框外。"""
    return x_start > xmax * 0.60


TIER_TOP = (0.985, 0.67, 0.355)     # 時間軸圖：事件標籤（由上往下寫）的起點高度，軸高的比例
TIER_BOTTOM = (0.02, 0.35, 0.68)    # 讀數對美元圖：事件標籤（由下往上寫）的起點高度


def cmd_charts(a) -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as fm
    have = {f.name for f in fm.fontManager.ttflist}
    for name in ("PingFang TC", "Heiti TC", "Hiragino Sans", "Arial Unicode MS", "Songti SC"):
        if name in have:
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False
    CHARTS.mkdir(parents=True, exist_ok=True)
    bk, ss, ev = Book(), samples(), events()
    if not ss:
        print("還沒有額度讀數")
        return 1
    fb = {m: PALETTE[i % len(PALETTE)] for i, m in enumerate(bk.models)}  # 未知模型的備用色

    def color_of(m: str) -> str:
        return COLOR.get(m) or fb.get(m) or PALETTE[-1]

    t0 = min(bk.ts[0], ss[0]["t"]) if bk.ts else ss[0]["t"]
    hrs = lambda t: (t - t0) / 3600.0  # noqa: E731

    # 圖一：時間軸。上＝額度讀數，下＝各模型累計 API 等值美元
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True, height_ratios=[1, 1.15])
    ax1.step([hrs(r["t"]) for r in ss], [r["weekly_all"] for r in ss], where="post", color="#111827", lw=2.2, label="每週所有模型 %")
    if has_fable(ss):
        ax1.step([hrs(r["t"]) for r in ss], [r.get("scoped_fable") for r in ss], where="post", color=COLOR[FABLE], lw=1.8, ls="--", label="每週 Fable %（上限＝總額度的一半）")
    ax1.set_ylabel("官方額度讀數（%）"); ax1.legend(loc="upper left"); ax1.grid(alpha=.25)
    grid = [t0 + i * 30 for i in range(int((ss[-1]["t"] - t0) / 30) + 2)]
    base = [0.0] * len(grid)
    for m in bk.models:
        ys = [bk.usd_by_model(t)[m] for t in grid]
        if max(ys) < 0.05:
            continue
        top = [x + y for x, y in zip(base, ys)]
        ax2.fill_between([hrs(t) for t in grid], base, top, color=color_of(m), alpha=.75, label=f"{name_of(m)}（${ys[-1]:.2f}）")
        base = top
    ax2.set_ylabel("累計用量（照 API 牌價換算，美元）"); ax2.set_xlabel(f"實驗開始後的小時數（起點 {hhmm(t0)[6:11]}）")
    ax2.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=4); ax2.grid(alpha=.25)
    starts = [e for e in ev if not e["label"].endswith("-end")]  # 只標各階段的起點，避免起訖標籤疊在一起
    label_box = dict(facecolor="white", alpha=.75, edgecolor="none", pad=.6)
    for i, e in enumerate(starts):
        for ax in (ax1, ax2):
            ax.axvline(hrs(e["t"]), color="#9ca3af", lw=.8, ls=":")
        # 相鄰的事件標籤錯開三種高度（y 用軸的比例），時間靠得近也不會疊在一起
        ax2.text(hrs(e["t"]), TIER_TOP[i % 3], " " + e["label"].replace("-start", ""), rotation=90, va="top", ha="left",
                 fontsize=8, color="#374151", bbox=label_box, transform=ax2.get_xaxis_transform())
    fig.suptitle("Claude 訂閱每週額度 × API 等值用量：時間軸", fontsize=15)
    fig.tight_layout(); fig.savefig(CHARTS / "01_timeline.png", dpi=160); plt.close(fig)

    # 圖二：讀數 vs 累計美元（每個讀數點的位置），看斜率是否隨模型改變
    fig, ax = plt.subplots(figsize=(11, 6.5))
    xs = [sum(bk.usd_by_model(r["t"]).values()) for r in ss]
    ax.step(xs, [r["weekly_all"] for r in ss], where="post", color="#111827", lw=2)
    seg = [(e["t"], e["label"].replace("-start", "")) for e in ev if not e["label"].endswith("-end")]
    for i, (ta, lab) in enumerate(seg):
        x = sum(bk.usd_by_model(ta).values())
        ax.axvline(x, color="#9ca3af", lw=.8, ls=":")
        ax.text(x, TIER_BOTTOM[i % 3], " " + lab, rotation=90, va="bottom", ha="left", fontsize=7.5, color="#6b7280",
                bbox=dict(facecolor="white", alpha=.75, edgecolor="none", pad=.6), transform=ax.get_xaxis_transform())
    ax.set_xlabel("累計用量（照 API 牌價換算，美元，全部模型合計）"); ax.set_ylabel("每週所有模型 %")
    ax.set_title("同樣花 1 美元的 API 等值用量，各階段扣額度的速度不同（線越陡＝扣越兇）", fontsize=13)
    ax.grid(alpha=.25); fig.tight_layout(); fig.savefig(CHARTS / "02_meter_vs_usd.png", dpi=160); plt.close(fig)
    print("→", CHARTS / "01_timeline.png"); print("→", CHARTS / "02_meter_vs_usd.png")

    # 圖三：各受控格「每扣 1% 額度＝多少 API 等值美元」的範圍，依工作型態分三欄、同一欄內比模型
    rows = [r for r in cells_rows(bk, ss, ev) if r["controlled"] and r["usd_per_pct_lo"] is not None and r["usd_per_pct_lo"] > 0]  # 下限為 0 的格（跑太快、沒夾住）不畫
    if rows:
        inv = {SHORT.get(m, m): m for m in bk.models}
        order = {name: i for i, name in enumerate(["Haiku 5.5", "Sonnet 5.5", "Opus 5.5", "Fable 5.2"])}

        def kind_of(r: dict) -> str:
            # 先看格名（實驗設計時指定的工作型態），格名看不出來才用成本組成判斷
            name = r["cell"]
            for key, kd in (("reread", "反覆讀快取（快取讀取為主）"), ("write", "寫長文（輸出為主）"), ("digest", "讀檔（快取寫入為主）")):
                if key in name:
                    return kd
            cw = r["share_cw5m"] + r["share_cw1h"]
            best = max((cw, "讀檔（快取寫入為主）"), (r["share_out"], "寫長文（輸出為主）"), (r["share_cr"], "反覆讀快取（快取讀取為主）"))
            return best[1]

        kinds = ["讀檔（快取寫入為主）", "寫長文（輸出為主）", "反覆讀快取（快取讀取為主）"]
        # 畫兩張：一張給研究用（格名與跳點數），一張給影片用（白話標籤、數字照口播的取整：下限往下取、上限往上取）
        for plain in (False, True):
            fig, axes = plt.subplots(1, 3, figsize=(15, 5.6), sharex=True)
            xmax = max(exact(r, "lo") if r.get("upper_unreliable") else (exact(r, "hi_adj") or exact(r, "hi")) for r in rows) * 1.55
            for ax, kd in zip(axes, kinds):
                grp = sorted((r for r in rows if kind_of(r) == kd), key=lambda r: (order.get(r["model"], 9), r["start"]), reverse=True)
                for y, r in enumerate(grp):
                    lo, hi, adj = exact(r, "lo"), exact(r, "hi"), exact(r, "hi_adj")
                    upper = hi if adj is None else adj  # 有扣掉主持對話的上限就用它，和 README 的表同一個約定
                    col = color_of(inv.get(r["model"]) or r["model"])
                    # 只夾到 1 個百分點的格用斜線標記，提醒範圍很寬
                    hatch = "//" if r["span_pct"] == 1 else None
                    if r.get("upper_unreliable"):
                        # 只有下限可用：從下限畫一條淡色、沒有右端的橫條
                        ax.barh(y, xmax - lo, left=lo, height=.55, color=col, alpha=.18, hatch="..")
                        ax.plot([lo, lo], [y - .275, y + .275], color=col, lw=2.5)
                        lab = f"至少 ${math.floor(lo)}（只知道下限）" if plain else f"至少 ${lo:.0f}（上限不可靠）"
                        box = dict(facecolor="white", alpha=.9, edgecolor="none", pad=1.5)
                        if label_left_of_bar(lo + xmax * .02, xmax):  # 標籤會跑出軸外：改畫在橫條左邊
                            ax.text(lo - xmax * .012, y, lab, ha="right", va="center", fontsize=9, color="#111827",
                                    bbox=box, clip_on=True)
                        else:
                            ax.text(lo + xmax * .02, y, lab, va="center", fontsize=9, color="#111827",
                                    bbox=box, clip_on=True)
                        continue
                    ax.barh(y, hi - lo, left=lo, height=.55, color=col, alpha=.85, hatch=hatch)
                    if upper > hi:
                        # 淡色延伸＝扣掉主持對話同時段用量後的上限（橫條的右端與標籤都用這個上限）
                        ax.barh(y, upper - hi, left=hi, height=.55, color=col, alpha=.3, hatch=hatch)
                    right = upper
                    lab = f"${math.floor(lo)}–{math.ceil(upper)}" if plain else f"${lo:.0f}–{upper:.0f}"
                    if label_left_of_bar(right + xmax * .012, xmax):  # 標籤會跑出軸外：改畫在橫條左邊
                        ax.text(lo - xmax * .012, y, lab, ha="right", va="center", fontsize=9, color="#111827", clip_on=True)
                    else:
                        ax.text(right + xmax * .012, y, lab, va="center", fontsize=9, color="#111827", clip_on=True)
                ax.set_yticks(range(len(grp)))
                if plain:
                    seen: dict = {}
                    names = {}
                    for r in sorted(grp, key=lambda r: r["start"]):
                        seen[r["model"]] = seen.get(r["model"], 0) + 1
                        names[r["cell"]] = r["model"] + ("" if seen[r["model"]] == 1 else f"（第 {seen[r['model']]} 次）")
                    ax.set_yticklabels([names[r["cell"]] for r in grp], fontsize=11)
                else:
                    ax.set_yticklabels([f"{r['model']}｜{r['cell']}（{r['jumps_inside']} 個跳點）" for r in grp], fontsize=8.5)
                ax.set_title(kd.split("（")[0] if plain else kd, fontsize=14 if plain else 12)
                ax.set_xlim(0, xmax); ax.grid(axis="x", alpha=.25)
                ax.set_xlabel("扣 1% 額度可以用掉的美元（照 API 牌價）" if plain else "每扣 1% 額度對應的 API 等值美元")
                top = ax.secondary_xaxis("top", functions=(lambda v: v * 100, lambda v: v / 100))
                top.set_xlabel("照比例推到整週（美元，外推）" if plain else "線性外推到 100%（美元，推估）", fontsize=8.5, color="#6b7280")
                top.tick_params(labelsize=8, colors="#6b7280")
            fig.suptitle("扣 1% 週額度，照牌價能用掉多少美元？（橫條越靠右越划算）" if plain
                         else "同一種工作、不同模型：扣 1% 每週額度要用掉多少 API 等值美元（橫條＝格內跳點夾出的範圍）", fontsize=15 if plain else 13)
            fig.text(0.5, 0.935, "淡色＝扣掉發動實驗的那個對話的用量後的上限；斜線＝只量到 1 個百分點，範圍較寬；點狀＝只知道下限" if plain
                     else "淡色延伸＝扣掉發動實驗的那個對話同時段用量後的上限；斜線＝只夾到 1 個百分點；點狀＝只有下限可用",
                     ha="center", va="top", fontsize=8.5, color="#6b7280")
            out3 = CHARTS / ("03_cells_ranges_zh.png" if plain else "03_cells_ranges.png")
            fig.tight_layout(rect=(0, 0, 1, 0.90)); fig.savefig(out3, dpi=160); plt.close(fig)
            print("→", out3)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("phases", help="各階段：用量組成與讀數起訖")
    sub.add_parser("jumps", help="每次跳 1%% 的時間區間與當時累計用量 → data/jumps.csv")
    c = sub.add_parser("cells", help="每一格實驗的組成與「每 1%% 美元」範圍 → data/cells.csv")
    c.add_argument("--lead", type=float, default=60.0, help="本機紀錄檔比伺服器計量晚寫入的秒數上限（夾住區間兩端各放寬這麼多秒，預設 60）")
    sub.add_parser("charts", help="出圖到 charts/")
    sub.add_parser("checks", help="資料衛生檢查（只印 OK／WARN，永遠結束碼 0）")
    sub.add_parser("summary", help="產生 RESULTS.md（自動產生，勿手改）")
    f = sub.add_parser("fit", help="用所有讀數做區間限制，解「每扣 1%% 等於多少 API 美元」")
    f.add_argument("--lag", type=float, default=0.0, help="讀數延遲上限秒數（用量(t-lag) ≤ 讀數+1）")
    f.add_argument("--lead", type=float, default=0.0, help="本機紀錄檔比伺服器計量晚寫入的秒數上限")
    f.add_argument("--slack", type=float, default=0.0, help="每一條限制都對稱放寬的百分點（容納時間對不準）")
    f.add_argument("--unlogged", type=float, default=0.0, help="只放寬「用量 ≥ 讀數」那半邊的百分點（帳本沒記到的用量）")
    f.add_argument("--rounding", choices=sorted(ROUNDING), default="floor", help="假設官方讀數怎麼取整：floor 無條件捨去（預設）、round 四捨五入、ceil 無條件進位")
    f.add_argument("--start", choices=("free", "zero"), default="free",
                   help="起跑時兩個表的讀數真值：free 未知（不超過第一筆讀數加 1 點，預設）、zero 假設剛好是 0")
    s = sub.add_parser("sweep", help="掃 lead × slack，列出三個假設的可行情形 → data/fit/sweep.csv")
    s.set_defaults(fn=cmd_sweep)
    a = p.parse_args()
    return {"phases": cmd_phases, "jumps": cmd_jumps, "fit": cmd_fit, "charts": cmd_charts,
            "cells": cmd_cells, "sweep": cmd_sweep, "checks": cmd_checks, "summary": cmd_summary}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
