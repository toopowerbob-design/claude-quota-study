#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quota_lab.py 的離線測試（只用標準函式庫）：寫入防護、payload 雜湊、錯誤欄位、續期觸發、帳本欄位。

用法：python tests/test_quota_lab.py
每個檢查印一行 ok／FAIL；有任何 FAIL 時結束碼 1。全部通過時最後一行是 QUOTA_LAB_TESTS_OK。
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "bin" / "quota_lab.py"
PRICES = HERE.parent / "prices.json"
PY = sys.executable
fails = 0


def check(name: str, cond: bool) -> None:
    global fails
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fails += 1


def clean_env(**extra: str) -> dict:
    """把 QUOTA_LAB_* 全部拿掉再放進指定的值，避免繼承執行者的設定。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("QUOTA_LAB_")}
    env.update(extra)
    return env


def run_lab(repo: Path, argv: list[str], cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(repo / "bin" / "quota_lab.py"), *argv],
                          cwd=str(cwd or repo), env=env or clean_env(), capture_output=True, text=True)


def make_repo(tmp: Path, name: str = "repo") -> tuple[Path, Path]:
    repo = tmp / name
    (repo / "bin").mkdir(parents=True)
    (repo / "data").mkdir()
    shutil.copy2(SRC, repo / "bin" / "quota_lab.py")
    if PRICES.exists():
        shutil.copy2(PRICES, repo / "prices.json")
    return repo, repo / "data"


def last_row(path: Path) -> dict:
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else {}


def load_module(path: Path, name: str, data_dir: Path):
    """從指定檔案匯入模組（每次用新名字，模組層常數才會照當時的環境變數重算）。"""
    os.environ["QUOTA_LAB_DATA"] = str(data_dir)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 1. 寫入防護：比真實路徑，不管 QUOTA_LAB_DATA 怎麼寫 ─────────────────────
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    repo, data = make_repo(tmp)
    (repo / "noprojects").mkdir()
    guard_env = lambda **kw: clean_env(QUOTA_LAB_PROJECTS=str(repo / "noprojects"), **kw)  # noqa: E731

    variants = [
        ("沒設變數", None),
        ("空字串算沒設", ""),
        ("相對路徑 data", "data"),
        ("相對路徑 ./data", "./data"),
        ("絕對路徑", str(data)),
        ("絕對路徑加結尾斜線", str(data) + "/"),
    ]
    for name, val in variants:
        env = guard_env() if val is None else guard_env(QUOTA_LAB_DATA=val)
        r = run_lab(repo, ["mark", "x", "--note", "y"], env=env)
        check(f"guard: {name} 的 mark 結束碼 4", r.returncode == 4)
    link = tmp / "data-link"
    os.symlink(data, link)
    r = run_lab(repo, ["mark", "x", "--note", "y"], env=guard_env(QUOTA_LAB_DATA=str(link)))
    check("guard: 指向 data 的 symlink 的 mark 結束碼 4", r.returncode == 4)
    check("guard: 被拒絕時沒有寫出 events.jsonl", not (data / "events.jsonl").exists())

    r = run_lab(repo, ["status"], env=guard_env(QUOTA_LAB_DATA=str(data)))
    check("guard: status 不會因防護而結束碼 4", r.returncode != 4)
    r = run_lab(repo, ["report"], env=guard_env(QUOTA_LAB_DATA=str(data)))
    check("guard: report 不會因防護而結束碼 4", r.returncode != 4)
    r = run_lab(repo, ["report"], env=guard_env())
    check("report: 重建帳本 0 筆時結束碼 3 且不印表", r.returncode == 3 and "額度讀數" not in r.stdout)

    (data / ".write-ok").write_text("")
    r = run_lab(repo, ["mark", "with-ok", "--note", "y"], env=guard_env())
    rows = [json.loads(l) for l in (data / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()] if (data / "events.jsonl").exists() else []
    check("guard: 有 .write-ok 時 mark 結束碼 0 並寫入 events.jsonl",
          r.returncode == 0 and len(rows) == 1 and rows[0].get("label") == "with-ok")

    other = tmp / "other-data"
    other.mkdir()
    r = run_lab(repo, ["mark", "other", "--note", "y"], env=guard_env(QUOTA_LAB_DATA=str(other)))
    check("guard: 指到別的資料夾時不必 .write-ok 也能寫",
          r.returncode == 0 and (other / "events.jsonl").exists())

# ── 2. payload_hash：重設時間不影響，百分比有變就不同 ─────────────────────
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    repo, data = make_repo(tmp)
    mod = load_module(repo / "bin" / "quota_lab.py", "ql_hash", data)
    base = {
        "seven_day": {"utilization": 40, "resets_at": "2026-11-18T00:00:00Z"},
        "five_hour": {"utilization": 5, "resets_at": "2026-11-17T12:00:00Z"},
        "limits": [{"kind": "weekly_all", "percent": 40, "resets_at": "2026-11-18T00:00:00Z"}],
        "seven_day_breakdown": {
            "rows": [{"key": "opus", "percent": 12, "resets_at": "2026-11-18T00:00:00Z"}],
            "as_of": "2026-11-17T10:00:00Z",
            "window_started_at": "2026-11-10T00:00:00Z",
        },
    }
    moved = json.loads(json.dumps(base))
    moved["seven_day"]["resets_at"] = "2026-11-19T01:02:03Z"
    moved["five_hour"]["resets_at"] = "2026-11-17T13:00:00Z"
    moved["limits"][0]["resets_at"] = "2026-11-20T00:00:00Z"
    moved["seven_day_breakdown"]["rows"][0]["resets_at"] = "2026-11-21T00:00:00Z"
    moved["seven_day_breakdown"]["as_of"] = "2026-11-17T11:11:11Z"
    moved["seven_day_breakdown"]["window_started_at"] = "2026-11-09T00:00:00Z"
    check("payload_hash: 只改 resets_at／as_of 時雜湊相同", mod.payload_hash(base) == mod.payload_hash(moved))
    changed = json.loads(json.dumps(base))
    changed["seven_day_breakdown"]["rows"][0]["percent"] = 13
    check("payload_hash: 百分比有變時雜湊不同", mod.payload_hash(base) != mod.payload_hash(changed))

# ── 3. 錯誤欄位只留類別名，限速另加字尾 ───────────────────────────────────
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    repo, data = make_repo(tmp)
    mod = load_module(repo / "bin" / "quota_lab.py", "ql_err", data)
    check("err: RuntimeError 只留類別名與 rate_limited",
          mod.err_text(RuntimeError("token abc123 at /home/x rate_limited")) == "RuntimeError:rate_limited")
    check("err: ValueError 只留類別名", mod.err_text(ValueError("secret")) == "ValueError")

# ── 4. 憑證續期觸發：看例外本身，不看訊息 ─────────────────────────────────
def meter_case(tmp: Path, name: str, exc_src: str) -> tuple[dict, list]:
    lib = tmp / ("meter-" + name) / "lib"
    lib.mkdir(parents=True)
    (lib / "live_claude.py").write_text(
        "USAGE_URL = 'https://example.invalid/usage'\n"
        "class TokenExpired(PermissionError):\n"
        "    pass\n"
        "def load_tokens():\n"
        "    return ['tok-not-real']\n"
        "def fetch_one(token):\n"
        f"    raise {exc_src}\n"
        "def _http_json(url, token):\n"
        "    raise AssertionError('不該打到用量端點')\n",
        encoding="utf-8")
    data = tmp / ("data-" + name)
    data.mkdir()
    repo, _ = make_repo(tmp, "repo-" + name)
    old = dict(os.environ)
    try:
        os.environ["QUOTA_LAB_METER_LIB"] = str(tmp / ("meter-" + name))
        mod = load_module(repo / "bin" / "quota_lab.py", "ql_sample_" + name, data)
    finally:
        os.environ.clear()
        os.environ.update(old)
    calls: list = []
    real_run = subprocess.run

    def fake_run(argv, **kw):
        calls.append(list(argv))
        class R:
            returncode = 0
        return R()

    subprocess.run = fake_run
    try:
        for k in [x for x in sys.modules if x == "lib" or x.startswith("lib.")]:
            del sys.modules[k]  # 每個案例用自己那個假的收集器，不要沿用上一個案例的快取
        with contextlib.redirect_stdout(io.StringIO()):
            mod.cmd_sample(argparse.Namespace(email="me" + chr(64) + "example.invalid", once=True,
                                              interval=1.0, verify_every=600.0, until=""))
    finally:
        subprocess.run = real_run
    return last_row(data / "samples.jsonl"), calls


with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    row, calls = meter_case(tmp, "expired_class", "TokenExpired('token 過期')")
    check("sample: TokenExpired（PermissionError 子類）觸發一次 claude auth status",
          calls == [["claude", "auth", "status"]] and row.get("refresh_try") is True)
    row, calls = meter_case(tmp, "permission", "PermissionError('auth')")
    check("sample: PermissionError 觸發一次 claude auth status",
          calls == [["claude", "auth", "status"]] and row.get("refresh_try") is True)
    row, calls = meter_case(tmp, "rate", "RuntimeError('rate_limited')")
    check("sample: 限速不觸發 claude auth status",
          calls == [] and "refresh_try" not in row)

# ── 4b. sample 沒設讀數收集器：說明怎麼設，結束碼 2 ─────────────────────────
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    repo, _ = make_repo(tmp)
    outside = tmp / "data-out"
    outside.mkdir()
    r = run_lab(repo, ["sample", "--email", "me" + chr(64) + "example.invalid", "--once"],
                env=clean_env(QUOTA_LAB_DATA=str(outside)))
    check("sample: 沒設 QUOTA_LAB_METER_LIB 時結束碼 2", r.returncode == 2)
    check("sample: 訊息提到 QUOTA_LAB_METER_LIB 與 manual",
          "QUOTA_LAB_METER_LIB" in r.stderr and "manual" in r.stderr)

# ── 5. 帳本列沒有專案夾名，只留相對檔名 ───────────────────────────────────
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    repo, _ = make_repo(tmp)
    projects = tmp / "projects"
    sess = projects / "proj-a" / "sess.jsonl"
    sess.parent.mkdir(parents=True)
    lines = []
    for i, (mid, out) in enumerate([("m1", 50), ("m2", 70)], start=1):
        lines.append(json.dumps({
            "type": "assistant",
            "timestamp": f"2026-11-17T0{i}:00:00.000Z",
            "sessionId": "s1",
            "requestId": f"r{i}",
            "message": {
                "id": mid,
                "model": "claude-sonnet-4-5-20250929",
                "usage": {
                    "input_tokens": 100 * i,
                    "output_tokens": out,
                    "cache_read_input_tokens": 1000 * i,
                    "cache_creation": {"ephemeral_5m_input_tokens": 2000 * i,
                                       "ephemeral_1h_input_tokens": 0},
                },
            },
        }))
    sess.write_text("\n".join(lines) + "\n", encoding="utf-8")
    labdata = tmp / "labdata"
    r = run_lab(repo, ["ledger", "--since", "2026-11-17T00:00:00Z"],
                env=clean_env(QUOTA_LAB_PROJECTS=str(projects), QUOTA_LAB_DATA=str(labdata)))
    rows = [json.loads(l) for l in (labdata / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()] \
        if (labdata / "ledger.jsonl").exists() else []
    check("ledger: 兩筆呼叫都寫進帳本", r.returncode == 0 and len(rows) == 2)
    check("ledger: 每一列都沒有 proj 鍵", all("proj" not in row for row in rows))
    check("ledger: 只留相對檔名 sess.jsonl", all(row.get("file") == "sess.jsonl" for row in rows))
    check("ledger: 呼叫編號與模型有留下", [row.get("mid") for row in rows] == ["m1", "m2"]
          and all(row.get("model") == "claude-sonnet-4-5-20250929" for row in rows))

if fails:
    print(f"{fails} 項失敗")
    sys.exit(1)
print("QUOTA_LAB_TESTS_OK")
