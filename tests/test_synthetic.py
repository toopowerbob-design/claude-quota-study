#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合成資料回歸測試：不需要 pytest，直接 `python tests/test_synthetic.py` 執行，成功結束碼 0。

每個測試各自建一個臨時資料夾（放在 repo 的 scratch/ 底下，已 git-ignore），
並用 QUOTA_LAB_DATA 把它指定給子行程，所以不會動到 repo 裡真正的 data/。

測到的行為：
  1. 跳點一次跳好幾趴（2→4）時，cells 的分母要用「讀數差」而不是「跳點個數」；
  2. ledger 掃到 0 筆時要擋住，不能把既有帳本蓋成空的；
  3. 手動讀數（沒有 --fable，樣本裡沒有 scoped_fable）時各命令都不能爆；
  4. 帳本裡有不在 SHORT 名單、但算得出 usd 的模型時，phases 要照樣顯示；
  5. fit/ 底下的 JSON 必須是嚴格 JSON（不能有 Infinity／NaN）。
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
SCRATCH = ROOT / "scratch"
CLASSES = ["in", "cw5m", "cw1h", "cr", "out"]
FAILS: list[str] = []
DONE: list[str] = []


# ───────────────────────── 小工具 ─────────────────────────

def iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


def jline(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(jline(r) for r in rows), encoding="utf-8")


def make_data_dir(name: str) -> Path:
    SCRATCH.mkdir(exist_ok=True)
    d = SCRATCH / f"test_synthetic_{name}"
    if d.exists():
        shutil.rmtree(d)
    (d / "data").mkdir(parents=True)
    return d / "data"


def env_for(data: Path, extra: dict | None = None) -> dict:
    env = dict(os.environ)
    env.pop("QUOTA_LAB_T0", None)
    env["QUOTA_LAB_DATA"] = str(data)
    env.pop("QUOTA_LAB_CHARTS", None)
    env.update(extra or {})
    return env


def run(script: str, args: list[str], data: Path, extra: dict | None = None):
    return subprocess.run([PY, str(ROOT / "bin" / script)] + args, cwd=str(ROOT),
                          env=env_for(data, extra), capture_output=True, text=True, timeout=300)


def sample(t: float, val, fable=None) -> dict:
    r = {"ts": iso(t), "t": round(t, 3), "ok": True, "weekly_all": val, "util7d": float(val)}
    if fable is not None:
        r["scoped_fable"] = fable
    return r


def ledger_row(t: float, model: str, usd, cls: str = "out", agent: str | None = None) -> dict:
    parts = {c: (usd if c == cls else 0.0) for c in CLASSES}
    r = {"ts": iso(t), "t": round(t, 3), "mid": f"msg_{model}_{t}", "model": model,
         "usd": usd, "usd_parts": parts, "in": 0, "out": 0, "think": 0, "cr": 0, "cw5m": 0, "cw1h": 0}
    if agent:
        r["agent"] = agent
    return r


def load_analyze(data: Path):
    """把 analyze.py 當模組載入；DATA 是匯入時讀環境變數，所以每次用新的模組名。"""
    import importlib.util
    name = "analyze_under_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / "analyze.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("載不到 bin/analyze.py")
    mod = importlib.util.module_from_spec(spec)
    old = os.environ.get("QUOTA_LAB_DATA")
    os.environ["QUOTA_LAB_DATA"] = str(data)
    try:
        spec.loader.exec_module(mod)
    finally:
        if old is None:
            os.environ.pop("QUOTA_LAB_DATA", None)
        else:
            os.environ["QUOTA_LAB_DATA"] = old
    return mod


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        DONE.append(name)
        print(f"  ok   - {name}")
    else:
        FAILS.append(f"{name}: {detail}")
        print(f"  FAIL - {name}: {detail}")


# 修好的版本認 QUOTA_LAB_DATA，不會碰 repo 的 data/；
# 但拿同一支測試去跑「還沒修」的版本時，被測程式會忽略 QUOTA_LAB_DATA 直接寫 repo 的 data/，
# 所以測試前後各記一次，跑完把被弄髒的檔案還原（正常情況不會有東西要還原）。
def snapshot_repo_data() -> dict:
    d = ROOT / "data"
    return {str(p.relative_to(ROOT)): p.read_bytes()
            for p in sorted(d.rglob("*")) if p.is_file()} if d.exists() else {}


def restore_repo_data(snap: dict) -> list[str]:
    d = ROOT / "data"
    now = {str(p.relative_to(ROOT)): p.read_bytes()
           for p in sorted(d.rglob("*")) if p.is_file()} if d.exists() else {}
    changed = []
    for k, v in snap.items():
        if now.get(k) != v:
            (ROOT / k).write_bytes(v)
            changed.append(k)
    for k in now:
        if k not in snap:
            (ROOT / k).unlink()
            changed.append(k + "（新增，已刪除）")
    return changed


# ───────────────────────── 測試 ─────────────────────────

def test_skip_jump() -> None:
    """讀數一次跳 2 趴（3 從沒被觀察到）時，分母必須是讀數差。"""
    print("skip-jump：跳點跳過一整趴")
    d = make_data_dir("skipjump")
    base = 1794900000.0
    vals = [0, 0, 1, 1, 2, 2, 4, 4, 5, 5]
    times = [base + i * 10 for i in range(len(vals))]
    write_jsonl(d / "samples.jsonl", [sample(t, v) for t, v in zip(times, vals)])
    # 累計美元：t2=15、t4=25、t6=45、t8=55 → 每 1% 剛好 10 美元（K＝10）
    write_jsonl(d / "ledger.jsonl", [ledger_row(times[1] + 1, "claude-sonnet-5-5", 15.0),
                                     ledger_row(times[3] + 1, "claude-sonnet-5-5", 10.0),
                                     ledger_row(times[5] + 1, "claude-sonnet-5-5", 20.0),
                                     ledger_row(times[7] + 1, "claude-sonnet-5-5", 10.0)])
    write_jsonl(d / "events.jsonl", [{"ts": iso(times[0]), "t": times[0], "label": "skipjump-start", "note": ""},
                                     {"ts": iso(times[-1]), "t": times[-1], "label": "skipjump-end", "note": ""}])

    r = run("analyze.py", ["cells", "--lead", "0"], d)
    check("cells 跑得動", r.returncode == 0, r.stderr[-400:])
    row = None
    p = d / "cells.csv"
    if p.exists():
        rows = list(csv.DictReader(p.open(encoding="utf-8")))
        row = next((x for x in rows if x["cell"] == "skipjump"), None)
    check("cells 有 skipjump 這一格", row is not None, f"rows={p.read_text() if p.exists() else 'no file'}")
    if row:
        lo, hi = row["usd_per_pct_lo"], row["usd_per_pct_hi"]
        check("cells 每 1% 範圍含 10 美元（分母＝4 趴，不是跳點數 3）",
              lo not in ("", None) and hi not in ("", None) and float(lo) <= 10 <= float(hi),
              f"lo={lo} hi={hi}")
        # 下限＝(70 秒的累計 45 美元 − 20 秒的 15 美元) / 4 趴；上限＝(80 秒的 55 美元 − 10 秒的 0 美元) / 4 趴
        check("cells 下限＝7.5 美元（分母用讀數差 4 趴）",
              lo not in ("", None) and abs(float(lo) - 7.5) <= 0.01, f"lo={lo}")
        check("cells 上限＝13.75 美元", hi not in ("", None) and abs(float(hi) - 13.75) <= 0.01, f"hi={hi}")
        check("cells 沒放寬時 lead_s 記 0", row["lead_s"] in ("0", "0.0"), row["lead_s"])

    # 預設 lead 60 秒：下限那端會被放寬到同一個時間點，夾不出正的差，整段範圍不成立
    r = run("analyze.py", ["cells"], d)
    check("cells（預設 lead 60）跑得動", r.returncode == 0, r.stderr[-400:])
    rows = list(csv.DictReader((d / "cells.csv").open(encoding="utf-8"))) if (d / "cells.csv").exists() else []
    row60 = next((x for x in rows if x["cell"] == "skipjump"), None)
    check("cells lead_s 預設 60", row60 is not None and float(row60["lead_s"]) == 60.0,
          "" if row60 is None else row60["lead_s"])
    check("cells 放寬 60 秒後這一格夾不出範圍（下限留空）",
          row60 is not None and row60["usd_per_pct_lo"] == "", "" if row60 is None else row60["usd_per_pct_lo"])

    r = run("analyze.py", ["fit"], d)
    check("fit 跑得動", r.returncode == 0, r.stderr[-400:])
    fp = d / "fit" / "fit_lag0_lead0_slack0_unlogged0.json"
    check("fit 檔在預期路徑", fp.exists(), str(fp))
    if fp.exists():
        res = json.loads(fp.read_text(encoding="utf-8"))
        one = res.get("one") or {}
        check("fit 假設一可行", bool(one.get("feasible")), json.dumps(one, ensure_ascii=False)[:200])
        th = (one.get("theta") or {}).get("all")
        if th:
            lo_th, hi_th = th[0], th[1]
            k_lo, k_hi = 1.0 / hi_th, 1.0 / lo_th
            check("fit 假設一範圍含 10 美元", k_lo <= 10 <= k_hi, f"{k_lo:.2f}~{k_hi:.2f}")
        else:
            check("fit 假設一有 theta", False, "沒有 theta")


def test_empty_ledger_guard() -> None:
    """對話紀錄掃不到東西時，不能把既有帳本蓋掉，結束碼 3。"""
    print("empty-ledger guard：掃到 0 筆要擋住")
    d = make_data_dir("emptyguard")
    keep = '{"mid":"keep-me"}\n'
    (d / "ledger.jsonl").write_text(keep, encoding="utf-8")
    empty = d.parent / "empty_projects"
    empty.mkdir(parents=True, exist_ok=True)
    r = run("quota_lab.py", ["ledger"], d, {"QUOTA_LAB_PROJECTS": str(empty)})
    check("ledger 結束碼 3", r.returncode == 3, f"rc={r.returncode} out={r.stdout[-300:]}")
    check("既有帳本沒有被改", (d / "ledger.jsonl").read_text(encoding="utf-8") == keep,
          (d / "ledger.jsonl").read_text(encoding="utf-8")[:200])
    check("有把警告印到 stderr", "0 筆" in r.stderr, r.stderr[-300:])


def test_manual_without_fable() -> None:
    """手動讀數沒有 scoped_fable：所有分析命令都要活著。"""
    print("manual 讀數（沒有 --fable）")
    d = make_data_dir("manual")
    for n in (0, 1, 3):
        r = run("quota_lab.py", ["manual", "--all", str(n)], d)
        check(f"manual --all {n} 成功", r.returncode == 0, r.stderr[-200:])
    ss = [json.loads(x) for x in (d / "samples.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    check("手動讀數沒有 scoped_fable", all("scoped_fable" not in x for x in ss), str(ss[:1]))
    now = time.time()
    write_jsonl(d / "ledger.jsonl", [ledger_row(now - 150, "claude-sonnet-5-5", 2.5),
                                     ledger_row(now - 100, "claude-opus-5-5", 3.5)])
    write_jsonl(d / "events.jsonl", [{"ts": iso(now - 200), "t": now - 200, "label": "m1-start", "note": ""},
                                     {"ts": iso(now - 10), "t": now - 10, "label": "m1-end", "note": ""}])
    for cmd in ("phases", "jumps", "cells", "fit", "charts", "checks", "summary"):
        r = run("analyze.py", [cmd], d, {"QUOTA_LAB_CHARTS": str(d.parent / "charts")})
        check(f"{cmd} 結束碼 0（沒有 scoped_fable）", r.returncode == 0,
              (r.stderr or r.stdout)[-400:])
    check("summary 產出 RESULTS.md", (d / "RESULTS.md").exists() and (d / "RESULTS.md").stat().st_size > 0,
          str(d / "RESULTS.md"))


def test_unknown_model() -> None:
    """帳本裡有不在 SHORT 名單、但有 usd 的模型：phases 要顯示，checks 不能亂警告。"""
    print("unknown model id")
    d = make_data_dir("unknownmodel")
    base = 1794900000.0
    write_jsonl(d / "samples.jsonl", [sample(base + i * 60, i, fable=0) for i in range(3)])
    write_jsonl(d / "events.jsonl", [{"ts": iso(base), "t": base, "label": "u1-start", "note": ""}])
    write_jsonl(d / "ledger.jsonl", [ledger_row(base + 30, "claude-opus-4-5", 1.5),
                                     ledger_row(base + 90, "claude-sonnet-5-5", 2.0)])
    r = run("analyze.py", ["phases"], d)
    check("phases 結束碼 0", r.returncode == 0, r.stderr[-300:])
    check("phases 顯示不在名單的模型", "claude-opus-4-5" in r.stdout, r.stdout[-400:])
    check("phases 沒有誤報缺價警告", "缺少 usd" not in r.stderr, r.stderr[-300:])
    r = run("analyze.py", ["checks"], d)
    check("checks 結束碼 0", r.returncode == 0, r.stderr[-300:])
    false_warn = [x for x in r.stdout.splitlines()
                  if x.startswith("WARN") and ("污染" in x or "缺牌價" in x)]
    check("checks 沒有假的污染／缺價警告", not false_warn, str(false_warn))


def test_strict_json() -> None:
    """fit/ 底下的 JSON 必須是嚴格 JSON（允許 null，不准 Infinity／NaN）。"""
    print("strict JSON")
    d = make_data_dir("strictjson")
    base = 1794900000.0
    write_jsonl(d / "samples.jsonl", [sample(base + i * 60, i) for i in range(0, 4)])
    write_jsonl(d / "ledger.jsonl", [ledger_row(base + 30, "claude-sonnet-5-5", 20.0),
                                     ledger_row(base + 90, "claude-opus-5-5", 5.0)])

    def boom(x):
        raise ValueError(f"strict JSON 不該出現 {x!r}")

    for args in (["fit"], ["fit", "--unlogged", "0.1"], ["fit", "--lead", "60", "--slack", "0.1"]):
        r = run("analyze.py", args, d)
        check(f"{' '.join(args)} 結束碼 0", r.returncode == 0, r.stderr[-300:])
    files = sorted(p for p in (d / "fit").glob("*.json")) if (d / "fit").exists() else []
    check("fit/ 底下有 JSON 檔", bool(files), str(d / "fit"))
    for p in files:
        try:
            json.loads(p.read_text(encoding="utf-8"), parse_constant=boom)
            check(f"{p.name} 是嚴格 JSON", True)
        except Exception as exc:  # noqa: BLE001
            check(f"{p.name} 是嚴格 JSON", False, str(exc))


def test_fable_cell() -> None:
    """Fable 格：分項跳 1 點＝總額度的 0.5%，範圍要用分項點數×0.5 當分母。"""
    print("fable-cell：分項讀數與 0.5 換算")
    d = make_data_dir("fablecell")
    base = 1794900000.0
    vals = [0, 1, 1, 2, 2, 3, 3, 4, 4]
    times = [base + i * 60 for i in range(len(vals))]
    write_jsonl(d / "samples.jsonl", [sample(t, 10, fable=v) for t, v in zip(times, vals)])
    # 每次跳完才花錢：每 0.5 點 10 美元 → 每 1 點 20 美元；最後一跳的中間再補 5 美元，
    # 這樣下限（看最後一跳的舊讀數時間）與上限（看最後一跳的新讀數時間）才會分開
    write_jsonl(d / "ledger.jsonl", [ledger_row(times[1] + 0.5, "claude-fable-5-2", 10.0, agent="fa"),
                                     ledger_row(times[3] + 0.5, "claude-fable-5-2", 10.0, agent="fa"),
                                     ledger_row(times[5] + 0.5, "claude-fable-5-2", 10.0, agent="fa"),
                                     ledger_row(times[6] + 40, "claude-fable-5-2", 5.0, agent="fa")])
    write_jsonl(d / "events.jsonl", [{"ts": iso(base - 10), "t": base - 10, "label": "fc-start", "note": ""},
                                     {"ts": iso(base + 600), "t": base + 600, "label": "fc-end", "note": ""}])
    r = run("analyze.py", ["cells", "--lead", "0"], d)
    check("fable 格 cells 跑得動", r.returncode == 0, r.stderr[-400:])
    rows = list(csv.DictReader((d / "cells.csv").open(encoding="utf-8"))) if (d / "cells.csv").exists() else []
    row = next((x for x in rows if x["cell"] == "fc"), None)
    check("fable 格有列出來", row is not None, str([x["cell"] for x in rows]))
    if row:
        check("fable 格用 scoped_fable", row["meter_used"] == "scoped_fable", row["meter_used"])
        check("fable 格受控", row["controlled"] == "True", row["controlled"])
        check("fable 格 span 1.5 趴", abs(float(row["span_pct"]) - 1.5) < 1e-6, row["span_pct"])
        lo, hi = float(row["usd_per_pct_lo"]), float(row["usd_per_pct_hi"])
        # 下限＝(360 秒的 30 美元 − 60 秒的 0 美元) / 1.5 點；上限＝(420 秒的 35 美元 − 0 秒的 0 美元) / 1.5 點
        check("fable 格下限＝每點 20 美元", abs(lo - 20) <= 0.05, f"lo={lo}")
        check("fable 格上限＝每點 23.33 美元（含最後一跳區間裡的 5 美元）",
              abs(hi - 23.33) <= 0.05, f"hi={hi}")
        check("fable 格讀數寫分項", row["scoped_from"] == "0" and row["scoped_to"] == "4",
              f"{row['scoped_from']}→{row['scoped_to']}")
        check("fable 格也填主持對話美元（照實列，不修正）",
              row["host_fable_usd_window"] not in ("", None) and float(row["host_fable_usd_window"]) == 0.0,
              row["host_fable_usd_window"])


def test_host_fable_correction() -> None:
    """總額度格的上限要扣掉同時段主持對話（沒有 agent）的 Fable 用量。"""
    print("host-fable：主持對話同時段用量與上限修正")
    d = make_data_dir("hostfable")
    base = 1794900000.0
    weekly = [0, 1, 1, 2, 2, 2, 2, 2, 3, 3, 4, 4, 5, 5]
    fable = [0, 1, 1, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3]
    times = [base + i * 60 for i in range(len(weekly))]
    write_jsonl(d / "samples.jsonl", [sample(t, w, fable=f) for t, w, f in zip(times, weekly, fable)])
    rows = [
        # 受控 Fable 格 fc：分項 0→1→2→3（共 1.5 點），每點 20 美元
        ledger_row(times[1] + 0.5, "claude-fable-5-2", 15.0, agent="fa"),
        ledger_row(times[3] + 0.5, "claude-fable-5-2", 15.0, agent="fa"),
        ledger_row(times[5] + 0.5, "claude-fable-5-2", 15.0, agent="fa"),
        # 非 Fable 格 hn：Sonnet 子代理，加上同時段主持對話的 Fable（無 agent）
        ledger_row(times[8] + 20, "claude-sonnet-5-5", 10.0, agent="ha"),
        ledger_row(times[10] + 20, "claude-sonnet-5-5", 10.0, agent="ha"),
        ledger_row(times[8] + 30, "claude-fable-5-2", 3.0),
        ledger_row(times[10] + 30, "claude-fable-5-2", 3.0),
        # 窗框兩端各一筆：窗框起點從 first.t0 挪到 first.t1、終點從放寬後的尾端挪到 last.t0，
        # 這兩種錯都會讓下面的「主持對話同時段用量」少算
        ledger_row(times[7] + 30, "claude-fable-5-2", 4.0),
        ledger_row(times[11] + 40, "claude-fable-5-2", 2.0),
    ]
    write_jsonl(d / "ledger.jsonl", rows)
    write_jsonl(d / "events.jsonl", [{"ts": iso(base - 10), "t": base - 10, "label": "fc-start", "note": ""},
                                     {"ts": iso(times[5] + 5), "t": times[5] + 5, "label": "fc-end", "note": ""},
                                     {"ts": iso(times[5] + 5), "t": times[5] + 5, "label": "hn-start", "note": ""},
                                     {"ts": iso(times[-1] + 60), "t": times[-1] + 60, "label": "hn-end", "note": ""}])
    r = run("analyze.py", ["cells", "--lead", "0"], d)
    check("host 修正 cells 跑得動", r.returncode == 0, r.stderr[-400:])
    rows2 = list(csv.DictReader((d / "cells.csv").open(encoding="utf-8"))) if (d / "cells.csv").exists() else []
    fc = next((x for x in rows2 if x["cell"] == "fc"), None)
    hn = next((x for x in rows2 if x["cell"] == "hn"), None)
    check("host 修正有兩格", fc is not None and hn is not None, str([x["cell"] for x in rows2]))
    if fc:
        check("fable 格 host 用量記 0", abs(float(fc["host_fable_usd_window"])) < 1e-9, fc["host_fable_usd_window"])
        check("fable 格 adj 等於原始上限", fc["usd_per_pct_hi_adj"] == fc["usd_per_pct_hi"],
              f"hi={fc['usd_per_pct_hi']} adj={fc['usd_per_pct_hi_adj']}")
        check("fable 格每點 30 美元", abs(float(fc["usd_per_pct_lo"]) - 30) < 0.2, fc["usd_per_pct_lo"])
    if hn:
        check("主持對話同時段用量 12 美元（窗框兩端各有一筆）",
              abs(float(hn["host_fable_usd_window"]) - 12.0) < 1e-6, hn["host_fable_usd_window"])
        check("hn 夾住 2 趴", abs(float(hn["span_pct"]) - 2) < 1e-6, hn["span_pct"])
        hi, adj = float(hn["usd_per_pct_hi"]), float(hn["usd_per_pct_hi_adj"])
        check("扣掉主持對話後上限變高", adj > hi, f"hi={hi} adj={adj}")
        # 20 / (2 − 12/30) = 12.5：窗框少算 4 美元會變 11.11，少算 2 美元會變 12.0
        check("修正後上限約 12.5 美元", abs(adj - 12.5) < 0.05, str(adj))


def test_host_fable_upper_unreliable() -> None:
    """主持對話的 Fable 用量大到可能占掉讀數差的一半以上時，只留下限，上限標為不可靠。"""
    print("host-fable：主持對話用量太大時只留下限")
    d = make_data_dir("hostfable_big")
    base = 1794900000.0
    weekly = [0, 1, 1, 2, 2, 2, 2, 2, 3, 3, 4, 4, 5, 5]
    fable = [0, 1, 1, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3]
    times = [base + i * 60 for i in range(len(weekly))]
    write_jsonl(d / "samples.jsonl", [sample(t, w, fable=f) for t, w, f in zip(times, weekly, fable)])
    rows = [
        ledger_row(times[1] + 0.5, "claude-fable-5-2", 15.0, agent="fa"),
        ledger_row(times[3] + 0.5, "claude-fable-5-2", 15.0, agent="fa"),
        ledger_row(times[5] + 0.5, "claude-fable-5-2", 15.0, agent="fa"),
        ledger_row(times[8] + 20, "claude-sonnet-5-5", 10.0, agent="ha"),
        ledger_row(times[10] + 20, "claude-sonnet-5-5", 10.0, agent="ha"),
        # 主持對話在同一段用了 50 美元：以每點 30 美元算約 1.67 趴，夾住的 2 趴只剩不到 0.5 趴
        ledger_row(times[8] + 30, "claude-fable-5-2", 25.0),
        ledger_row(times[10] + 30, "claude-fable-5-2", 25.0),
    ]
    write_jsonl(d / "ledger.jsonl", rows)
    write_jsonl(d / "events.jsonl", [{"ts": iso(base - 10), "t": base - 10, "label": "fc-start", "note": ""},
                                     {"ts": iso(times[5] + 5), "t": times[5] + 5, "label": "fc-end", "note": ""},
                                     {"ts": iso(times[5] + 5), "t": times[5] + 5, "label": "hn-start", "note": ""},
                                     {"ts": iso(times[-1] + 60), "t": times[-1] + 60, "label": "hn-end", "note": ""}])
    r = run("analyze.py", ["cells", "--lead", "0"], d)
    check("上限不可靠 cells 跑得動", r.returncode == 0, r.stderr[-400:])
    rows2 = list(csv.DictReader((d / "cells.csv").open(encoding="utf-8"))) if (d / "cells.csv").exists() else []
    hn = next((x for x in rows2 if x["cell"] == "hn"), None)
    check("上限不可靠：找得到 hn 格", hn is not None, str([x["cell"] for x in rows2]))
    if hn:
        check("上限不可靠：旗標為真", hn["upper_unreliable"] == "True", hn.get("upper_unreliable"))
        check("上限不可靠：修正後上限留空", hn["usd_per_pct_hi_adj"] == "", hn["usd_per_pct_hi_adj"])
        check("上限不可靠：下限仍在", float(hn["usd_per_pct_lo"]) > 0, hn["usd_per_pct_lo"])
        check("上限不可靠：畫面寫「以上」而不是範圍", "以上" in r.stdout and "上限不可靠" in r.stdout, r.stdout[-300:])


def test_rounding_modes() -> None:
    """讀數四捨五入時，fit 假設二要在 round 可行；用 floor 解釋會不夠。"""
    print("rounding：四捨五入與無條件捨去")
    d = make_data_dir("rounding")
    base = 1794900000.0
    times = [base + i * 60 for i in range(8)]
    write_jsonl(d / "samples.jsonl", [sample(t, i + 1) for i, t in enumerate(times)])
    rows = [ledger_row(base - 1, "claude-sonnet-5-5", 6.0)]
    rows += [ledger_row(t - 1, "claude-sonnet-5-5", 10.0) for t in times[1:]]
    write_jsonl(d / "ledger.jsonl", rows)
    for rd in ("round", "floor"):
        r = run("analyze.py", ["fit", "--rounding", rd, "--start", "zero"], d)
        check(f"fit --rounding {rd} 跑得動", r.returncode == 0, r.stderr[-300:])
    r = run("analyze.py", ["fit", "--rounding", "floor"], d)
    check("fit --rounding floor（起跑值未知）跑得動", r.returncode == 0, r.stderr[-300:])
    fr = d / "fit" / "fit_lag0_lead0_slack0_unlogged0_round_start0.json"
    ff = d / "fit" / "fit_lag0_lead0_slack0_unlogged0_start0.json"
    ff2 = d / "fit" / "fit_lag0_lead0_slack0_unlogged0.json"
    check("round 的 fit 檔存在（假設起跑值為 0）", fr.exists(), str(fr))
    check("floor 的 fit 檔存在（假設起跑值為 0）", ff.exists(), str(ff))
    check("起跑值未知的 fit 檔名不加後綴", ff2.exists() and not (d / "fit" / "fit_lag0_lead0_slack0_unlogged0_free.json").exists(),
          str(ff2))
    if fr.exists() and ff.exists():
        rr = json.loads(fr.read_text(encoding="utf-8"))["model"]
        rf = json.loads(ff.read_text(encoding="utf-8"))["model"]
        check("四捨五入時假設二可行", bool(rr["feasible"]), json.dumps(rr, ensure_ascii=False)[:200])
        check("無條件捨去時假設二不可行或更緊",
              (not rf["feasible"]) and (rf.get("total_tolerance") or 0) > 0,
              json.dumps(rf, ensure_ascii=False)[:200])
    if ff2.exists():
        rf2 = json.loads(ff2.read_text(encoding="utf-8"))["model"]
        check("起跑值未知時，無條件捨去的缺口由起跑值吸收（可行）",
              bool(rf2["feasible"]), json.dumps(rf2, ensure_ascii=False)[:200])


def test_rounding_ceil() -> None:
    """讀數無條件進位時，只有 ceil 的位移（1.0）說得通；用 0.5 會前後矛盾。"""
    print("rounding：無條件進位")
    d = make_data_dir("rounding_ceil")
    base = 1794900000.0
    times = [base + i * 60 for i in range(6)]
    # 真值＝0.1、1.1、…、5.1，無條件進位後讀數 1..6
    write_jsonl(d / "samples.jsonl", [sample(t, i + 1) for i, t in enumerate(times)])
    rows = [ledger_row(base - 1, "claude-sonnet-5-5", 1.0)]
    rows += [ledger_row(t - 1, "claude-sonnet-5-5", 10.0) for t in times[1:]]
    write_jsonl(d / "ledger.jsonl", rows)
    for rd in ("ceil", "floor"):
        r = run("analyze.py", ["fit", "--rounding", rd, "--start", "zero"], d)
        check(f"fit --rounding {rd}（起跑值為 0）跑得動", r.returncode == 0, r.stderr[-300:])
    fc = d / "fit" / "fit_lag0_lead0_slack0_unlogged0_ceil_start0.json"
    ff = d / "fit" / "fit_lag0_lead0_slack0_unlogged0_start0.json"
    check("ceil 的 fit 檔存在", fc.exists(), str(fc))
    check("floor 的 fit 檔存在（ceil 測試）", ff.exists(), str(ff))
    if fc.exists() and ff.exists():
        rc = json.loads(fc.read_text(encoding="utf-8"))["model"]
        rf = json.loads(ff.read_text(encoding="utf-8"))["model"]
        check("無條件進位時假設二可行", bool(rc["feasible"]), json.dumps(rc, ensure_ascii=False)[:200])
        check("用無條件捨去解釋會不可行",
              not rf["feasible"], json.dumps(rf, ensure_ascii=False)[:200])


def test_agent_uniformity() -> None:
    """十個子代理、三個只花一半：agents_n 10、agents_low_n 3；零頭（不到中位數 20%）不算。"""
    print("agents：子任務偏低計數")
    d = make_data_dir("agents")
    base = 1794900000.0
    times = [base + i * 60 for i in range(4)]
    write_jsonl(d / "samples.jsonl", [sample(t, v) for t, v in zip(times, [0, 1, 2, 3])])
    rows = []
    for i in range(10):
        usd = 5.0 if i >= 7 else 10.0
        rows.append(ledger_row(base + 30 + i * 10, "claude-sonnet-5-5", usd, agent=f"a{i}"))
    # 收尾用的零頭：1.5 美元不到中位數 10 美元的 20%，不該算一個子任務
    rows.append(ledger_row(base + 150, "claude-sonnet-5-5", 1.5, agent="tail"))
    write_jsonl(d / "ledger.jsonl", rows)
    write_jsonl(d / "events.jsonl", [{"ts": iso(base), "t": base, "label": "ag-start", "note": ""},
                                     {"ts": iso(base + 180), "t": base + 180, "label": "ag-end", "note": ""}])
    r = run("analyze.py", ["cells", "--lead", "0"], d)
    check("agents cells 跑得動", r.returncode == 0, r.stderr[-400:])
    rows2 = list(csv.DictReader((d / "cells.csv").open(encoding="utf-8"))) if (d / "cells.csv").exists() else []
    row = next((x for x in rows2 if x["cell"] == "ag"), None)
    check("agents 格有列出來", row is not None, str([x["cell"] for x in rows2]))
    if row:
        check("子代理數 10（零頭不算）", row["agents_n"] == "10", row["agents_n"])
        check("偏低的子代理 3", row["agents_low_n"] == "3", row["agents_low_n"])
        check("子任務欄印成 10（3）", "10（3）" in r.stdout, r.stdout[-400:])
        check("子任務中位數 10 美元（零頭不算進去）", abs(float(row["agent_usd_median"]) - 10.0) < 1e-6, str(row.get("agent_usd_median")))


def test_unlogged_bisection() -> None:
    """帳本固定少記 1.495 趴（再扣掉 slack 0.1）時，二分要找得到約 1.4，而且印出的值要可行。"""
    print("unlogged：二分搜尋救回假設二所需用量")
    d = make_data_dir("unlogged")
    base = 1794900000.0
    n = 200
    times = [base + i * 300 for i in range(n + 1)]
    # 真值每 300 秒多 1 趴；讀數是 floor(真值 + 1.495)，所以從 2 爬到 201
    write_jsonl(d / "samples.jsonl", [sample(t, i + 2) for i, t in enumerate(times)])
    rows = [ledger_row(base - 1, "claude-sonnet-5-5", 3.03, agent="x"),
            ledger_row(base - 1, "claude-opus-5-5", 2.02, agent="x")]
    for t in times[1:]:
        rows.append(ledger_row(t - 1, "claude-sonnet-5-5", 6.0, agent="x"))
        rows.append(ledger_row(t - 1, "claude-opus-5-5", 4.0, agent="x"))
    write_jsonl(d / "ledger.jsonl", rows)
    mod = load_analyze(d)
    bk = mod.Book()
    ss = mod.samples()
    got = mod.unlogged_needed(bk, ss, "model", start="zero")
    check("unlogged 二分有結果", got is not None, str(got))
    check("unlogged ≈ 1.40（隱藏 1.495 扣掉 slack 0.1）",
          got is not None and abs(got - 1.4) <= 0.02, str(got))
    check("unlogged 印出的值有向上取到 0.01",
          got is not None and abs(got * 100 - round(got * 100)) < 1e-6, str(got))
    got_w = mod.unlogged_needed(bk, ss, "model", weekly_only=True, start="zero")
    check("unlogged（只放寬總額度）沒有分項表時一樣",
          got_w is not None and got is not None and abs(got_w - got) <= 0.02, str(got_w))
    if got is None:
        return
    # 印出的值拿去跑 fit 要可行；少 0.01 就不行（四捨五入要無條件進位，不能往下取）
    r = run("analyze.py", ["fit", "--start", "zero", "--lag", "0", "--lead", "60", "--slack", "0.1",
                           "--unlogged", f"{got:.2f}"], d)
    check("fit 帶入印出的 unlogged 跑得動", r.returncode == 0, r.stderr[-300:])
    fj = d / "fit" / f"fit_lag0_lead60_slack0.1_unlogged{got:g}_start0.json"
    check("帶入印出的 unlogged 後假設二可行", fj.exists() and bool(json.loads(fj.read_text(encoding="utf-8"))["model"]["feasible"]),
          "" if not fj.exists() else fj.read_text(encoding="utf-8")[:200])
    low = round(got - 0.01, 2)
    r = run("analyze.py", ["fit", "--start", "zero", "--lag", "0", "--lead", "60", "--slack", "0.1",
                           "--unlogged", f"{low:.2f}"], d)
    check("少 0.01 就跑不動（fit 會把缺口算成不可行）", r.returncode == 0, r.stderr[-300:])
    fl = d / "fit" / f"fit_lag0_lead60_slack0.1_unlogged{low:g}_start0.json"
    check("unlogged 少 0.01 時假設二不可行",
          fl.exists() and not json.loads(fl.read_text(encoding="utf-8"))["model"]["feasible"],
          "" if not fl.exists() else fl.read_text(encoding="utf-8")[:200])


def test_straddle_end() -> None:
    """結束事件當下剛好有一跳（前後跨過結束時間）時，那一跳不算，但要標示出來。"""
    print("straddle_end：結束時剛好有一跳")
    d = make_data_dir("straddle")
    base = 1794900000.0
    times = [base + i * 60 for i in range(6)]
    write_jsonl(d / "samples.jsonl", [sample(t, v) for t, v in zip(times, [0, 1, 2, 3, 4, 4])])
    write_jsonl(d / "ledger.jsonl", [ledger_row(times[0] + 30, "claude-sonnet-5-5", 10.0),
                                     ledger_row(times[1] + 30, "claude-sonnet-5-5", 10.0),
                                     ledger_row(times[2] + 30, "claude-sonnet-5-5", 10.0),
                                     # 下一格的用量：落在跨過結束事件那一跳之後，不能算進這一格
                                     ledger_row(times[3] + 60, "claude-sonnet-5-5", 10.0)])
    write_jsonl(d / "events.jsonl", [{"ts": iso(base - 1), "t": base - 1, "label": "st-start", "note": ""},
                                     {"ts": iso(base + 180), "t": base + 180, "label": "st-end", "note": ""},
                                     {"ts": iso(base + 180), "t": base + 180, "label": "nx-start", "note": ""},
                                     {"ts": iso(base + 300), "t": base + 300, "label": "nx-end", "note": ""}])
    r = run("analyze.py", ["cells", "--lead", "0"], d)
    check("straddle cells 跑得動", r.returncode == 0, r.stderr[-400:])
    rows = list(csv.DictReader((d / "cells.csv").open(encoding="utf-8"))) if (d / "cells.csv").exists() else []
    row = next((x for x in rows if x["cell"] == "st"), None)
    check("straddle 格有列出來", row is not None, str([x["cell"] for x in rows]))
    if row:
        # 0→1、1→2、2→3 在格內；3→4 跨過結束時間（180 秒）不算
        check("straddle：格內只算 3 跳", row["jumps_inside"] == "3", row["jumps_inside"])
        check("straddle：旗標為真", row["straddle_end"] == "True", str(row.get("straddle_end")))
        check("straddle：分母 2 趴", abs(float(row["span_pct"]) - 2) <= 1e-6, str(row["span_pct"]))
        check("straddle：下限 5 美元", abs(float(row["usd_per_pct_lo"]) - 5) <= 0.01, row["usd_per_pct_lo"])
        check("straddle：上限 15 美元", abs(float(row["usd_per_pct_hi"]) - 15) <= 0.01, row["usd_per_pct_hi"])
        check("straddle：畫面註明未計入", "（結束時剛好有一跳，未計入）" in r.stdout, r.stdout[-400:])


def test_display_rounds_once() -> None:
    """一位小數的顯示從沒取整的原值取整（下限 9.449 顯示 9.4，不是 9.45 再進位成 9.5）；cells.csv 仍存到 0.01。
    RESULTS.md 的時間要有年份與時區。"""
    print("顯示：只取整一次，RESULTS.md 的時間有年份與時區")
    d = make_data_dir("round_once")
    base = 1794900000.0
    times = [base + i * 180 for i in range(6)]
    write_jsonl(d / "samples.jsonl", [sample(t, v) for t, v in zip(times, [0, 1, 2, 3, 3, 3])])
    # 跳點間隔 180 秒（預設 lead 60 也夾得出範圍）；跳點 0→1、1→2、2→3 在格內，分母 2 趴；下限＝中間那筆 18.898 ÷ 2 ＝ 9.449，上限＝(5 + 18.898 + 6) ÷ 2 ＝ 14.949
    write_jsonl(d / "ledger.jsonl", [ledger_row(times[0] + 90, "claude-sonnet-5-5", 5.0),
                                     ledger_row(times[1] + 90, "claude-sonnet-5-5", 18.898),
                                     ledger_row(times[2] + 90, "claude-sonnet-5-5", 6.0)])
    write_jsonl(d / "events.jsonl", [{"ts": iso(base - 1), "t": base - 1, "label": "rd-start", "note": ""},
                                     {"ts": iso(base + 900), "t": base + 900, "label": "rd-end", "note": ""}])
    extra = {"QUOTA_LAB_TZ_OFFSET_HOURS": "8"}
    r = run("analyze.py", ["cells", "--lead", "0"], d, extra)
    check("round_once cells 跑得動", r.returncode == 0, r.stderr[-400:])
    rows = list(csv.DictReader((d / "cells.csv").open(encoding="utf-8"))) if (d / "cells.csv").exists() else []
    row = next((x for x in rows if x["cell"] == "rd"), None)
    check("round_once 格有列出來", row is not None, str([x["cell"] for x in rows]))
    if row:
        check("round_once：cells.csv 下限仍存 9.45", row["usd_per_pct_lo"] == "9.45", row["usd_per_pct_lo"])
        check("round_once：cells.csv 上限仍存 14.95", row["usd_per_pct_hi"] == "14.95", row["usd_per_pct_hi"])
        check("round_once：畫面是 $9.4 ~ $14.9（不是 9.5 ~ 15.0）", "$9.4 ~ $14.9" in r.stdout, r.stdout[-500:])
    r = run("analyze.py", ["summary"], d, extra)
    check("round_once summary 跑得動", r.returncode == 0, r.stderr[-400:])
    text = (d / "RESULTS.md").read_text(encoding="utf-8") if (d / "RESULTS.md").exists() else ""
    check("round_once：RESULTS.md 表格是 $9.4 ~ $14.9", "$9.4 ~ $14.9" in text, text[-600:])
    check("round_once：資料截至有年份與時區", "資料截至 2026-11-17 15:35:00（台灣時間，UTC+8）" in text,
          "".join(x for x in text.splitlines(True) if "資料截至" in x))
    check("round_once：表格上方註明年份與時區", "2026 年的台灣時間（UTC+8）" in text, text[:900])


def test_start_strict() -> None:
    """第一筆讀數在開始事件之前的跳點（哪怕只早一點點）不算在格內。"""
    print("start：跨過開始事件的那一跳不算")
    d = make_data_dir("startstrict")
    base = 1794900000.0
    times = [base + i * 60 for i in range(6)]
    write_jsonl(d / "samples.jsonl", [sample(t, v) for t, v in zip(times, [0, 1, 1, 2, 3, 3])])
    write_jsonl(d / "ledger.jsonl", [ledger_row(times[0] + 30, "claude-sonnet-5-5", 10.0),
                                     ledger_row(times[2] + 30, "claude-sonnet-5-5", 10.0),
                                     ledger_row(times[3] + 30, "claude-sonnet-5-5", 10.0)])
    # 開始事件比第一筆讀數晚 0.5 秒：0→1 那一跳的第一筆讀數在格外
    write_jsonl(d / "events.jsonl", [{"ts": iso(base + 0.5), "t": base + 0.5, "label": "ss-start", "note": ""},
                                     {"ts": iso(base + 330), "t": base + 330, "label": "ss-end", "note": ""}])
    r = run("analyze.py", ["cells", "--lead", "0"], d)
    check("start cells 跑得動", r.returncode == 0, r.stderr[-400:])
    rows = list(csv.DictReader((d / "cells.csv").open(encoding="utf-8"))) if (d / "cells.csv").exists() else []
    row = next((x for x in rows if x["cell"] == "ss"), None)
    check("start 格有列出來", row is not None, str([x["cell"] for x in rows]))
    if row:
        check("start：格內只算 2 跳（1→2、2→3）", row["jumps_inside"] == "2", row["jumps_inside"])
        check("start：分母 1 趴", abs(float(row["span_pct"]) - 1) <= 1e-6, str(row["span_pct"]))


def test_kf_choice() -> None:
    """主持對話修正用的每點美元：取受控 Fable 格裡最小的下限，不看混合格、不取上限。"""
    print("kf：主持對話修正的每點美元怎麼挑")
    d = make_data_dir("kf")
    base = 1794900000.0
    weekly = [0, 1, 1, 2, 2, 2, 2, 2, 3, 3, 4, 4, 5, 5, 5, 5, 5, 5, 5, 5, 6, 7, 8, 8, 8]
    fable = [0, 1, 1, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 4, 5, 6, 7, 7, 8, 9, 10, 11, 12]
    times = [base + i * 60 for i in range(len(weekly))]
    write_jsonl(d / "samples.jsonl", [sample(t, w, fable=f) for t, w, f in zip(times, weekly, fable)])
    rows = [
        # 受控 Fable 格 fc1：每點 55 美元（上限 165）
        ledger_row(times[0] + 30, "claude-fable-5-2", 55.0, agent="f1"),
        ledger_row(times[2] + 30, "claude-fable-5-2", 55.0, agent="f1"),
        ledger_row(times[4] + 30, "claude-fable-5-2", 55.0, agent="f1"),
        # 受控 Fable 格 fc2：每點 15 美元（上限 35）
        ledger_row(times[16] + 30, "claude-fable-5-2", 15.0, agent="f2"),
        ledger_row(times[17] + 45, "claude-fable-5-2", 20.0, agent="f2"),
        # 混合（不受控）Fable 格 fc3：每點 1 美元，主持對話同時段也在用
        ledger_row(times[21] + 30, "claude-fable-5-2", 0.5, agent="f3"),
        ledger_row(times[22] + 30, "claude-fable-5-2", 1.0, agent="f3"),
        ledger_row(times[23] + 30, "claude-fable-5-2", 0.5, agent="f3"),
        ledger_row(times[21] + 40, "claude-fable-5-2", 1.0),
        ledger_row(times[23] + 40, "claude-fable-5-2", 1.0),
        # 總額度格 hn2：Sonnet 子代理花在兩個跳點之間，主持對話同時段有 1 美元
        ledger_row(times[20] + 15, "claude-sonnet-5-5", 10.0, agent="h2"),
        ledger_row(times[20] + 25, "claude-sonnet-5-5", 10.0, agent="h2"),
    ]
    write_jsonl(d / "ledger.jsonl", rows)
    write_jsonl(d / "events.jsonl", [{"ts": iso(base - 10), "t": base - 10, "label": "fc1-start", "note": ""},
                                     {"ts": iso(base + 305), "t": base + 305, "label": "fc1-end", "note": ""},
                                     {"ts": iso(base + 885), "t": base + 885, "label": "fc2-start", "note": ""},
                                     {"ts": iso(base + 1095), "t": base + 1095, "label": "fc2-end", "note": ""},
                                     {"ts": iso(base + 1125), "t": base + 1125, "label": "hn2-start", "note": ""},
                                     {"ts": iso(base + 1320), "t": base + 1320, "label": "hn2-end", "note": ""},
                                     {"ts": iso(base + 1245), "t": base + 1245, "label": "fc3-start", "note": ""},
                                     {"ts": iso(base + 1455), "t": base + 1455, "label": "fc3-end", "note": ""}])
    r = run("analyze.py", ["cells", "--lead", "0"], d)
    check("kf cells 跑得動", r.returncode == 0, r.stderr[-400:])
    rows2 = list(csv.DictReader((d / "cells.csv").open(encoding="utf-8"))) if (d / "cells.csv").exists() else []
    got = {x["cell"]: x for x in rows2}
    check("kf：三格 Fable 加一格總額度都在", all(k in got for k in ("fc1", "fc2", "fc3", "hn2")), str(list(got)))
    if all(k in got for k in ("fc1", "fc2", "fc3", "hn2")):
        check("kf：fc1 每點 55 美元", abs(float(got["fc1"]["usd_per_pct_lo"]) - 55) <= 0.01, got["fc1"]["usd_per_pct_lo"])
        check("kf：fc2 每點 15 美元", abs(float(got["fc2"]["usd_per_pct_lo"]) - 15) <= 0.01, got["fc2"]["usd_per_pct_lo"])
        check("kf：fc3 是混合格，資料檔不給範圍", got["fc3"]["usd_per_pct_lo"] == "" and got["fc3"]["usd_per_pct_hi"] == "",
              str((got["fc3"]["usd_per_pct_lo"], got["fc3"]["usd_per_pct_hi"])))
        check("kf：fc3 標成不受控", got["fc3"]["controlled"] == "False", got["fc3"]["controlled"])
        # 用最小的下限 15：20 / (2 − 1/15) = 10.34
        # 取最大下限 55 → 10.09；取上限 4 → 11.43；不看受控（1）→ 20
        check("kf：hn2 修正後上限 10.34（用受控格裡最小的下限）",
              abs(float(got["hn2"]["usd_per_pct_hi_adj"]) - 10.34) <= 0.05,
              f"adj={got['hn2']['usd_per_pct_hi_adj']} lo={got['hn2']['usd_per_pct_lo']} hi={got['hn2']['usd_per_pct_hi']}")


def test_start_offsets() -> None:
    """起跑時讀數已經有 0.8 點：起跑值未知時可行，假設為 0 要放寬約 0.8。"""
    print("start：量表的起跑值未知")
    d = make_data_dir("startoff")
    base = 1794900000.0
    times = [base + i * 60 for i in range(8)]
    # 真值＝1.9、2.9、…、8.9，無條件捨去後讀數 1..8；帳本只從 0.05 美元開始記，
    # 所以有 1.85 點是帳本開始之前就已經在量表上的
    write_jsonl(d / "samples.jsonl", [sample(t, i + 1) for i, t in enumerate(times)])
    rows = [ledger_row(base - 1, "claude-sonnet-5-5", 0.05)]
    rows += [ledger_row(t - 1, "claude-sonnet-5-5", 1.0) for t in times[1:]]
    write_jsonl(d / "ledger.jsonl", rows)
    for st in ("free", "zero"):
        r = run("analyze.py", ["fit", "--start", st], d)
        check(f"fit --start {st} 跑得動", r.returncode == 0, r.stderr[-300:])
    ff = d / "fit" / "fit_lag0_lead0_slack0_unlogged0.json"
    fz = d / "fit" / "fit_lag0_lead0_slack0_unlogged0_start0.json"
    check("起跑值未知的 fit 檔存在", ff.exists(), str(ff))
    check("起跑值為 0 的 fit 檔存在", fz.exists(), str(fz))
    if ff.exists() and fz.exists():
        rf = json.loads(ff.read_text(encoding="utf-8"))["model"]
        rz = json.loads(fz.read_text(encoding="utf-8"))["model"]
        check("起跑值未知時可行（0.8 點由 w0 吸收）", bool(rf["feasible"]), json.dumps(rf, ensure_ascii=False)[:300])
        check("假設起跑值為 0 時要放寬（約 0.8 趴）",
              (not rz["feasible"]) and (rz.get("total_tolerance") or 0) > 0.5,
              json.dumps(rz, ensure_ascii=False)[:300])
        so = rf.get("start_offsets") or {}
        check("起跑值未知時回報 w0 的範圍", "w0" in so, json.dumps(so, ensure_ascii=False)[:200])
        if "w0" in so:
            lo0, hi0 = so["w0"]
            check("w0 落在 0 到第一個讀數加 1 之間", 0 <= lo0 <= hi0 <= 2.0001, str(so["w0"]))


def test_host_only_rate() -> None:
    """主持對話單獨在 Fable 分項上的每 1% 美元：只取第一個 Fable 格開始之前的跳點。"""
    print("host_only_rate：主持對話單獨的每 1% 美元")
    d = make_data_dir("hostonly")
    base = 1794900000.0
    times = [base + i * 60 for i in range(6)]
    write_jsonl(d / "samples.jsonl", [sample(t, 0, fable=i) for i, t in enumerate(times)])
    write_jsonl(d / "ledger.jsonl", [ledger_row(base + 30, "claude-fable-5-2", 10.0),
                                     ledger_row(base + 90, "claude-fable-5-2", 10.0),
                                     ledger_row(base + 150, "claude-fable-5-2", 10.0),
                                     ledger_row(base + 250, "claude-fable-5-2", 5.0, agent="hx")])
    mod = load_analyze(d)
    bk = mod.Book()
    ss = mod.samples()
    ev = [{"ts": iso(base + 240), "t": base + 240, "label": "hx-start", "note": ""},
          {"ts": iso(base + 400), "t": base + 400, "label": "hx-end", "note": ""}]
    got = mod.host_only_rate(bk, ss, ev)
    check("主持對話單獨：算得出來", got is not None, str(got))
    if got:
        # 分項 1→4 共 1.5 點；美元 (180 秒的 30 − 60 秒的 10) = 20 到 (240 秒的 30 − 0 秒的 0)
        check("主持對話單獨：下限 13.33", abs(got[0] - 20 / 1.5) <= 0.01, str(got))
        check("主持對話單獨：上限 20", abs(got[1] - 30 / 1.5) <= 0.01, str(got))
    got2 = mod.host_only_rate(bk, ss, [])
    check("沒有 Fable 格時用全部跳點", got2 is not None and abs(got2[0] - 10) <= 0.01 and abs(got2[1] - 15) <= 0.01,
          str(got2))
    # 只有一跳時夾不出範圍
    d2 = make_data_dir("hostonly1")
    write_jsonl(d2 / "samples.jsonl", [sample(base, 0, fable=0), sample(base + 60, 0, fable=1)])
    write_jsonl(d2 / "ledger.jsonl", [ledger_row(base + 30, "claude-fable-5-2", 10.0)])
    mod2 = load_analyze(d2)
    check("不到兩跳時回 None", mod2.host_only_rate(mod2.Book(), mod2.samples(), []) is None,
          str(mod2.host_only_rate(mod2.Book(), mod2.samples(), [])))


def main() -> int:
    print(f"python = {PY}")
    snap = snapshot_repo_data()
    try:
        for fn in (test_skip_jump, test_empty_ledger_guard, test_manual_without_fable,
                   test_unknown_model, test_strict_json, test_fable_cell, test_start_strict, test_host_fable_correction, test_host_fable_upper_unreliable,
                   test_rounding_modes, test_rounding_ceil, test_agent_uniformity, test_unlogged_bisection,
                   test_straddle_end, test_display_rounds_once, test_kf_choice, test_start_offsets, test_host_only_rate):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                import traceback
                FAILS.append(f"{fn.__name__} 拋出例外：{exc}")
                print(f"  FAIL - {fn.__name__} 拋出例外：{exc}")
                traceback.print_exc()
    finally:
        changed = restore_repo_data(snap)
        if changed:
            print("注意：被測程式動到了 repo 的 data/，測試已還原：" + "、".join(changed))
    print(f"\n{len(DONE)} 項通過、{len(FAILS)} 項失敗")
    if FAILS:
        for f in FAILS:
            print("  FAIL " + f)
        print("WP1_SYNTHETIC_FAIL")
        return 1
    print("WP1_SYNTHETIC_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
