#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quota_lab.py：Claude 訂閱額度換算實驗室（單檔工具，2026-11-17）

要回答的問題：Claude Code 訂閱的「每週額度 %」，是不是照 API 價格在扣？
做法：兩條獨立的資料線，事後用時間對起來。
  1. 額度讀數（sample）：每 N 秒向官方用量端點問一次目前百分比。不呼叫模型，不花額度。
     讀數程式用的是自備的收集器（lib/live_claude.py，介面見下方說明；資料夾用環境變數 QUOTA_LAB_METER_LIB 指定；
     沒設這個變數時，sample 會說明怎麼設並以結束碼 2 結束，其他子命令都不需要它），
     本工具不印、不存任何登入憑證。
     沒有那支收集器的人，改用 manual 子命令把設定頁「用量」看到的百分比手動記進來。
  2. 用量帳本（ledger）：掃 ~/.claude/projects 底下的對話紀錄檔，把每一次 API 呼叫的
     模型、token 數（輸入／輸出／快取寫入 5 分鐘與 1 小時／快取讀取）抄出來，
     再用 prices.json 的 API 牌價換算成「如果走 API 要付多少美元」。

子命令：
  sample   常駐取樣（背景跑）
  ledger   重建用量帳本並印出各模型小計（只讀對話紀錄檔，不寫任何憑證；隱私：不存專案夾名，只存檔案的相對路徑）
  report   把「額度每跳 1%」的時刻，對上當時累計的 API 等值金額（只在記憶體裡建帳本，不寫檔）
  manual   手動記一筆額度讀數（沒有讀數程式時的替代做法）
  mark     記一筆實驗事件（某階段開始／結束）
  wait     等到額度讀數連續 N 秒沒變（給排程用）
  pace     節拍器：睡 N 秒後印一行（「反覆讀快取」型工作量用）
  status   一行現況

環境變數：
  QUOTA_LAB_METER_LIB   讀數收集器所在資料夾（內含 lib/live_claude.py）；只有 sample 需要，
                        沒設時 sample 以結束碼 2 結束並說明怎麼設
  QUOTA_LAB_PROJECTS    Claude Code 對話紀錄檔的根目錄（優先於 CLAUDE_CONFIG_DIR 與預設值）
  CLAUDE_CONFIG_DIR     有設的話，用它底下的 projects/ 當對話紀錄根目錄
  QUOTA_LAB_DATA        資料目錄（預設＝repo 的 data/），所有輸出檔都放這裡。寫入類命令
                        （ledger、mark、manual、sample）只要實際寫進 repo 自己的 data/ 就拒絕執行
                        （結束碼 4；路徑先解成真實路徑再比，相對路徑、結尾斜線、symlink 都算同一個資料夾），
                        除非該目錄有 .write-ok 檔（資料集擁有者才建立）
  QUOTA_LAB_T0          帳本起算時間（ISO 8601，UTC），預設為本研究的起點
  QUOTA_LAB_TZ_OFFSET_HOURS  列印本地時間用的時區位移（小時，預設 8）
  CLAUDE_CODE_USER_EMAIL 要監測的帳號（sample 的 --email 預設值；不寫進任何輸出檔）

sample 需要的讀數收集器介面（放在 QUOTA_LAB_METER_LIB 底下的 lib/live_claude.py）：
  load_tokens() -> list[str]            目前可用的登入憑證字串（本工具只當不透明字串用，不保存、不列印）
  fetch_one(token) -> (payload: dict, email: str)
                                        用一支憑證打一次用量端點，回傳（原始回應, 該憑證所屬帳號）
  _http_json(url, token) -> dict        只打給定網址並回傳 JSON（sample 已知帳號後用它省一次身分查詢）
  USAGE_URL                             用量端點的網址常數

憑證出錯時（沒有可用憑證、憑證過期、權限不足），取樣程式另外執行一次官方的
`claude auth status` 命令，讓官方 CLI 自己續期。這是用量端點之外的第二個對外呼叫
（不會呼叫模型），同一個行程五分鐘最多試一次；有執行時在該筆讀數記下 refresh_try: true。
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 10):  # 這一段要在 3.9 也能解析：只用到 sys 與 print
    print("需要 Python 3.10 以上（目前是 " + sys.version.split()[0] + "）；macOS 內建的 python3 是 3.9，請另外安裝較新的版本。")
    raise SystemExit(2)

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("QUOTA_LAB_DATA") or (ROOT / "data"))  # 所有資料檔都在這個目錄底下
# 讀數收集器所在資料夾只從環境變數來，沒有預設值；沒設時只有 sample 會拒絕執行。
DASH = Path(os.environ["QUOTA_LAB_METER_LIB"]) if os.environ.get("QUOTA_LAB_METER_LIB") else None
if os.environ.get("QUOTA_LAB_PROJECTS"):
    PROJECTS = Path(os.environ["QUOTA_LAB_PROJECTS"])
elif os.environ.get("CLAUDE_CONFIG_DIR"):
    PROJECTS = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects"
else:
    PROJECTS = Path.home() / ".claude" / "projects"
SAMPLES = DATA / "samples.jsonl"
PAYLOADS = DATA / "payload_changes.jsonl"
EVENTS = DATA / "events.jsonl"
LEDGER = DATA / "ledger.jsonl"
PIDFILE = DATA / "sampler.pid"
PRICES = ROOT / "prices.json"
DEFAULT_T0 = "2026-11-17T07:41:00Z"  # 本研究第一則對話開始（當時額度 0%／0%）
METER_KEYS = ("weekly_all", "scoped_fable", "util7d", "session", "util5h")
DEFAULT_TZ_OFFSET_HOURS = 8.0


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_iso(s: str) -> float:
    return datetime.fromisoformat(s.strip().replace("Z", "+00:00")).timestamp()


def resolve_since(arg: str | None) -> str:
    """--since 沒給就用 QUOTA_LAB_T0；兩者都沒有時明講用了內建預設值。"""
    if arg:
        return arg
    env = os.environ.get("QUOTA_LAB_T0")
    if env:
        return env
    print(f"未指定 --since 與環境變數 QUOTA_LAB_T0，使用內建預設起算時間 {DEFAULT_T0}（2026-11-17 研究起點）", file=sys.stderr)
    return DEFAULT_T0


def tz_offset_hours() -> float:
    try:
        return float(os.environ.get("QUOTA_LAB_TZ_OFFSET_HOURS") or DEFAULT_TZ_OFFSET_HOURS)
    except ValueError:
        return DEFAULT_TZ_OFFSET_HOURS


def local_hms(t: float) -> str:
    """依 QUOTA_LAB_TZ_OFFSET_HOURS（預設 +8）印本地時間，不看機器本身的時區。"""
    return datetime.fromtimestamp(t + tz_offset_hours() * 3600, timezone.utc).strftime("%m-%d %H:%M:%S")


WRITE_COMMANDS = ("ledger", "mark", "manual", "sample")


def _same_folder(a: Path, b: Path) -> bool:
    """兩個路徑是不是同一個資料夾（相對路徑、結尾斜線、symlink 都先解成真實路徑再比）。"""
    return os.path.realpath(str(a)) == os.path.realpath(str(b))


def refuses_committed_data(cmd: str) -> bool:
    """寫入類命令只要實際會寫進 repo 自己的 data/ 就要求 .write-ok，不管 QUOTA_LAB_DATA 怎麼設。"""
    if cmd not in WRITE_COMMANDS:
        return False
    if not _same_folder(DATA, ROOT / "data"):
        return False
    return not (DATA / ".write-ok").exists()


def err_text(exc: Exception) -> str:
    """錯誤欄位只留例外類別名稱；被官方限速時另加固定字尾，不寫出原始訊息。"""
    return type(exc).__name__ + (":rate_limited" if "rate_limited" in str(exc) else "")


def _without_reset_times(obj: object) -> object:
    """遞迴移除所有名為 resets_at 的鍵（重設時間每次取樣都會微幅變動，不算內容改變）。"""
    if isinstance(obj, dict):
        return {k: _without_reset_times(v) for k, v in obj.items() if k != "resets_at"}
    if isinstance(obj, list):
        return [_without_reset_times(v) for v in obj]
    return obj


def payload_hash(payload: dict) -> str:
    """官方回應內容的雜湊：先去掉每次都變的時間欄位，其餘照內容算。"""
    stable = _without_reset_times(json.loads(json.dumps(payload)))
    if isinstance(stable, dict):
        bd = stable.get("seven_day_breakdown")
        if isinstance(bd, dict):
            bd.pop("as_of", None)
            bd.pop("window_started_at", None)
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()[:12]


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


# ───────────────────────── 額度讀數 ─────────────────────────

def extract(payload: dict) -> dict:
    out: dict = {}
    sd = payload.get("seven_day")
    out["util7d"] = sd.get("utilization") if isinstance(sd, dict) else None
    fh = payload.get("five_hour")
    out["util5h"] = fh.get("utilization") if isinstance(fh, dict) else None
    lims = []
    for it in payload.get("limits") or []:
        if not isinstance(it, dict):
            continue
        scope = it.get("scope") if isinstance(it.get("scope"), dict) else {}
        model = (scope.get("model") or {}).get("display_name") if isinstance(scope.get("model"), dict) else None
        kind = it.get("kind")
        lims.append({"kind": kind, "model": model, "pct": it.get("percent"),
                     "active": it.get("is_active"), "resets_at": it.get("resets_at")})
        if kind == "weekly_all":
            out["weekly_all"] = it.get("percent")
        elif kind == "weekly_scoped" and model:
            out["scoped_" + str(model).lower()] = it.get("percent")
        elif kind == "session":
            out["session"] = it.get("percent")
    out["limits"] = lims
    shares = payload.get("weekly_scoped_shares")
    if isinstance(shares, list):
        out["shares"] = shares
    skip = {"seven_day", "five_hour", "limits", "weekly_scoped_shares", "extra_usage", "spend",
            "member_dashboard_available", "_plan"}
    out["other_nonnull"] = sorted(k for k, v in payload.items() if v is not None and k not in skip)
    return out


def meter_key(rec: dict) -> tuple:
    return tuple(rec.get(k) for k in METER_KEYS)


def cmd_sample(a) -> int:
    if DASH is None:
        print(
            "sample 需要讀數收集器：請把環境變數 QUOTA_LAB_METER_LIB 設成含 lib/live_claude.py 的資料夾"
            "（介面見本檔開頭的說明）。沒有收集器的人可以改用 manual 子命令手動記錄讀數，不需要收集器。",
            file=sys.stderr,
        )
        return 2
    sys.path.insert(0, str(DASH))
    from lib import live_claude as lc  # 自備的讀數收集器（介面見檔首說明）

    target = a.email
    if not target:
        print("需要 --email（要監測的帳號）", file=sys.stderr)
        return 2
    DATA.mkdir(parents=True, exist_ok=True)
    if not a.once:
        PIDFILE.write_text(str(os.getpid()))
    ident: dict[str, tuple] = {}
    verified: set[str] = set()  # 已經做過一次身分確認的憑證（每個行程每支最多 fetch_one 一次）
    target_hash: str | None = None  # 已鎖定的目標帳號憑證
    base = float(a.interval)   # 目前的基準節奏；被限速就拉長，連續成功再慢慢縮回
    interval = base
    streak = 0
    until = parse_iso(a.until) if a.until else None
    last_hash = None
    last_refresh_try = 0.0
    while True:
        t = time.time()
        if until and t > until:
            break
        rec: dict = {"ts": now_iso(), "t": round(t, 3), "iv": base}
        try:
            tokens = lc.load_tokens()
            if not tokens:
                raise FileNotFoundError("no_valid_token")
            live = {hashlib.sha256(x.encode()).hexdigest()[:10] for x in tokens}
            if target_hash and target_hash not in live:
                target_hash = None  # 目標帳號的憑證消失了（登出／換帳號）→ 重新確認
            got = False
            for tok in tokens:
                h = hashlib.sha256(tok.encode()).hexdigest()[:10]
                if target_hash and h != target_hash:
                    continue  # 已鎖定目標帳號，其他憑證不必再驗
                email, vt = ident.get(h, (None, 0.0))
                if h not in verified:
                    payload, email = lc.fetch_one(tok)  # 每支憑證每行程只做一次身分確認
                    verified.add(h)
                    ident[h] = (email, t)
                elif t - vt > a.verify_every:
                    payload, email = lc.fetch_one(tok)  # 目標帳號每隔 --verify-every 秒重新確認
                    ident[h] = (email, t)
                else:
                    if email != target:
                        continue  # 已確認過不是目標帳號的憑證，不再拿它去問用量
                    payload = lc._http_json(lc.USAGE_URL, tok)
                if email != target:
                    continue
                target_hash = h
                payload = dict(payload)
                plan = payload.pop("_plan", None)
                rec.update(extract(payload))
                if plan:
                    rec["plan"] = plan
                bd_rows = (payload.get("seven_day_breakdown") or {}).get("rows") if isinstance(payload.get("seven_day_breakdown"), dict) else None
                if isinstance(bd_rows, list):
                    rec["bd"] = {r["key"]: r.get("percent") for r in bd_rows if isinstance(r, dict) and r.get("key")}
                rec["ok"] = True
                rec["ph"] = payload_hash(payload)
                if rec["ph"] != last_hash:
                    append_jsonl(PAYLOADS, {"ts": rec["ts"], "t": rec["t"], "ph": rec["ph"], "payload": payload})
                    last_hash = rec["ph"]
                got = True
                break
            if not got:
                rec["ok"] = False
                rec["err"] = "target_account_not_logged_in"
            streak += 1
            if streak >= 12 and base > float(a.interval):
                base = max(float(a.interval), base - 5.0)
                streak = 0
            interval = base
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            rec["ok"] = False
            rec["err"] = err_text(exc)  # 只留例外類別名稱（限速另加字尾），原始訊息不進資料檔
            if "rate_limited" in text:
                # 官方用量端點有限速（2026-11-17 實測：10 秒一次約一分鐘就被擋；30 秒一次仍約六成被擋（101 次被擋 63 次），
                # 被擋的那次也算次數）。被擋就把基準節奏加 15 秒（上限 240），這一次至少停 60 秒
                base = min(base + 15.0, 240.0)
                streak = 0
                interval = max(base, 60.0)
            # 憑證過期時，請官方 CLI 自己續期（不呼叫模型）；五分鐘最多試一次。
            # 判斷看例外本身：讀數收集器的 TokenExpired 繼承 PermissionError；也接受類別名稱正好是 TokenExpired 的例外。
            no_valid_token = "no_valid_token" in text
            expired = isinstance(exc, PermissionError) or type(exc).__name__ == "TokenExpired"
            if (no_valid_token or expired) and t - last_refresh_try > 300:
                last_refresh_try = t
                try:
                    subprocess.run(["claude", "auth", "status"], capture_output=True, timeout=40)
                    rec["refresh_try"] = True
                except Exception:  # noqa: BLE001
                    pass
        append_jsonl(SAMPLES, rec)
        if a.once:
            print(json.dumps({k: v for k, v in rec.items() if k != "limits"}, ensure_ascii=False))
            break
        time.sleep(max(1.0, interval - (time.time() - t)))
    return 0


# ───────────────────────── 用量帳本 ─────────────────────────

def load_prices() -> dict:
    if PRICES.exists():
        return json.loads(PRICES.read_text(encoding="utf-8"))
    return {"models": {}}


def cost_of(rec: dict, prices: dict):
    """回傳 (美元, 各類別美元 dict)；查不到牌價回 (None, {})。單位：每百萬 token 美元。"""
    model = rec["model"]
    ent = None
    best = -1
    for k, v in prices.get("models", {}).items():
        if model.startswith(k) and len(k) > best:
            ent, best = v, len(k)
    if ent is None:
        return None, {}
    p = ent
    prompt = rec["in"] + rec["cr"] + rec["cw5m"] + rec["cw1h"]
    tier = ent.get("long_context")
    if tier and prompt > tier.get("over_prompt_tokens", 10 ** 12):
        p = {**ent, **tier}
    parts = {
        "in": rec["in"] * p["in"] / 1e6,
        "out": rec["out"] * p["out"] / 1e6,
        "cr": rec["cr"] * p["cr"] / 1e6,
        "cw5m": rec["cw5m"] * p["cw5m"] / 1e6,
        "cw1h": rec["cw1h"] * p["cw1h"] / 1e6,
    }
    return sum(parts.values()), parts


def build_ledger(t0: float, only_projects: list[str] | None = None, exclude_sids: list[str] | None = None) -> list[dict]:
    """掃對話紀錄檔重建帳本。隱私：不存專案夾名，只存來源檔在專案夾底下的相對路徑。"""
    excl = set(exclude_sids or [])
    best: dict[str, dict] = {}
    for root, _dirs, files in os.walk(PROJECTS):
        for fn in files:
            if not fn.endswith(".jsonl"):
                continue
            p = os.path.join(root, fn)
            try:
                if os.path.getmtime(p) < t0:
                    continue
            except OSError:
                continue
            rel = os.path.relpath(p, PROJECTS)
            parts = rel.split(os.sep)
            proj = parts[0]  # 專案夾名只拿來比對 --only-project，不寫進紀錄
            if only_projects and not any(s in proj for s in only_projects):
                continue
            file_rel = os.sep.join(parts[1:]) or fn
            try:
                f = open(p, encoding="utf-8", errors="replace")
            except OSError:
                continue
            with f:
                for line in f:
                    if '"usage"' not in line or '"assistant"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("type") != "assistant":
                        continue
                    if d.get("sessionId") and d.get("sessionId") in excl:
                        continue
                    m = d.get("message") if isinstance(d.get("message"), dict) else {}
                    u = m.get("usage")
                    if not isinstance(u, dict):
                        continue
                    model = m.get("model") or ""
                    if not model or model.startswith("<"):
                        continue
                    try:
                        tt = parse_iso(d.get("timestamp") or "")
                    except Exception:
                        continue
                    if tt < t0:
                        continue
                    mid = m.get("id") or d.get("uuid")
                    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
                    c5 = cc.get("ephemeral_5m_input_tokens")
                    c1 = cc.get("ephemeral_1h_input_tokens")
                    if c5 is None and c1 is None:
                        c5, c1 = u.get("cache_creation_input_tokens") or 0, 0
                    rec = {
                        "ts": d.get("timestamp"), "t": round(tt, 3), "mid": mid, "req": d.get("requestId"),
                        "model": model, "effort": d.get("effort"),
                        "in": u.get("input_tokens") or 0, "out": u.get("output_tokens") or 0,
                        "think": (u.get("output_tokens_details") or {}).get("thinking_tokens") or 0,
                        "cr": u.get("cache_read_input_tokens") or 0, "cw5m": c5 or 0, "cw1h": c1 or 0,
                        "tier": u.get("service_tier"), "speed": u.get("speed"),
                        "sid": d.get("sessionId"), "side": bool(d.get("isSidechain")),
                        "agent": d.get("agentId"), "entry": d.get("entrypoint"),
                        "file": file_rel,
                    }
                    old = best.get(mid)
                    if old is None or (rec["out"], rec["t"]) >= (old["out"], old["t"]):
                        best[mid] = rec
    recs = sorted(best.values(), key=lambda r: r["t"])
    prices = load_prices()
    for r in recs:
        usd, parts = cost_of(r, prices)
        r["usd"] = None if usd is None else round(usd, 6)
        r["usd_parts"] = {k: round(v, 6) for k, v in parts.items()}
    return recs


def write_ledger(recs: list[dict]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp.replace(LEDGER)


def totals(recs: list[dict], t_from: float = 0.0, t_to: float = 9e18) -> dict:
    agg: dict[str, dict] = {}
    for r in recs:
        if not (t_from < r["t"] <= t_to):
            continue
        a = agg.setdefault(r["model"], {"n": 0, "in": 0, "out": 0, "think": 0, "cr": 0, "cw5m": 0, "cw1h": 0,
                                         "usd": 0.0, "unpriced": 0})
        a["n"] += 1
        for k in ("in", "out", "think", "cr", "cw5m", "cw1h"):
            a[k] += r[k]
        if r.get("usd") is None:
            a["unpriced"] += 1
        else:
            a["usd"] += r["usd"]
    return agg


def print_totals(agg: dict) -> None:
    print(f"{'model':<22}{'calls':>6}{'input':>9}{'cache_w5m':>11}{'cache_w1h':>11}{'cache_read':>12}{'output':>9}{'(think)':>9}{'API$':>10}")
    tot = 0.0
    for m, a in sorted(agg.items()):
        tot += a["usd"]
        flag = " ?" if a["unpriced"] else ""
        print(f"{m:<22}{a['n']:>6}{a['in']:>9}{a['cw5m']:>11}{a['cw1h']:>11}{a['cr']:>12}{a['out']:>9}{a['think']:>9}{a['usd']:>10.4f}{flag}")
    print(f"{'TOTAL':<22}{'':>67}{tot:>10.4f}")


def cmd_ledger(a) -> int:
    since = resolve_since(a.since)
    t0 = parse_iso(since)
    recs = build_ledger(t0, a.only_project, a.exclude_sid)
    if not recs:
        print(f"警告：掃描 {PROJECTS} 得到 0 筆呼叫紀錄（起算 {since}）。"
              "為了不讓既有帳本被空檔蓋掉，這次不動帳本檔；確定要用空帳本請加 --force。", file=sys.stderr)
        if not a.force:
            return 3
        write_ledger(recs)
        print(f"帳本 0 筆呼叫（--force，已寫成空檔）→ {LEDGER}")
        return 0
    write_ledger(recs)
    print(f"帳本 {len(recs)} 筆呼叫（自 {since}）→ {LEDGER}")
    print_totals(totals(recs))
    return 0


# ───────────────────────── 對帳報表 ─────────────────────────

def ok_samples() -> list[dict]:
    return [s for s in read_jsonl(SAMPLES) if s.get("ok")]


def cmd_report(a) -> int:
    since = resolve_since(a.since)
    t0 = parse_iso(since)
    recs = build_ledger(t0)  # 只在記憶體裡建，不寫帳本檔
    if not recs:
        print("警告：這個命令讀的是你這台機器的 Claude Code 對話紀錄，不是 repo 裡的 data/ledger.jsonl；"
              "目前重建出 0 筆呼叫，所以不印報表（全為 0 的數字沒有意義）。"
              "要分析已提交的資料請用 bin/analyze.py。", file=sys.stderr)
        return 3
    samples = ok_samples()
    if not samples:
        print("還沒有額度讀數")
        return 1
    models = sorted({r["model"] for r in recs})

    def cum(t: float) -> tuple[float, dict]:
        by = {m: 0.0 for m in models}
        for r in recs:
            if r["t"] > t:
                break
            if r.get("usd") is not None:
                by[r["model"]] += r["usd"]
        return sum(by.values()), by

    print("額度讀數每次變動的時刻，與當時累計 API 等值美元（before＝變動前最後一次讀數時，after＝變動後第一次讀數時）")
    hdr = f"{'local time':<15}{'all%':>5}{'fable%':>7}{'util7d':>8}{'cum$ before':>13}{'cum$ after':>12}  " + "  ".join(m.replace('claude-', '') for m in models)
    print(hdr)
    prev = None
    for s in samples:
        if prev is None or meter_key(s) != meter_key(prev):
            cb, _ = cum(prev["t"]) if prev else (0.0, {})
            ca, by = cum(s["t"])
            print(f"{local_hms(s['t']):<15}{str(s.get('weekly_all')):>5}{str(s.get('scoped_fable')):>7}{str(s.get('util7d')):>8}{cb:>13.3f}{ca:>12.3f}  "
                  + "  ".join(f"{by[m]:.3f}" for m in models))
        prev = s
    last = samples[-1]
    ca, by = cum(time.time())
    print(f"現在：all={last.get('weekly_all')}% fable={last.get('scoped_fable')}% util7d={last.get('util7d')}（讀數時間 {local_hms(last['t'])}）累計 API 等值 ${ca:.3f}")
    print_totals(totals(recs))
    return 0


def cmd_manual(a) -> int:
    """沒有讀數程式時的替代：把設定頁「用量」看到的百分比手動記一筆（整數，照畫面抄）。"""
    rec = {"ts": now_iso(), "t": round(time.time(), 3), "ok": True, "manual": True,
           "weekly_all": a.all, "util7d": float(a.all), "util5h": None, "session": None}
    if a.fable is not None:
        rec["scoped_fable"] = a.fable
    append_jsonl(SAMPLES, rec)
    print(json.dumps(rec, ensure_ascii=False))
    return 0


def cmd_mark(a) -> int:
    # 同名事件在短時間內重複記（例如排程重送同一條命令）只算第一筆
    for old in reversed(read_jsonl(EVENTS)):
        if time.time() - old.get("t", 0) > a.dedupe_seconds:
            break
        if old.get("label") == a.label:
            print(json.dumps({"skipped": "duplicate", **old}, ensure_ascii=False))
            return 0
    rec = {"ts": now_iso(), "t": round(time.time(), 3), "label": a.label, "note": a.note or ""}
    s = ok_samples()
    if s:
        rec["meter"] = {k: s[-1].get(k) for k in METER_KEYS}
        rec["meter_age_s"] = round(time.time() - s[-1]["t"], 1)
    append_jsonl(EVENTS, rec)
    print(json.dumps(rec, ensure_ascii=False))
    return 0


def cmd_wait(a) -> int:
    start = time.time()
    why = "timeout"
    while time.time() - start < a.timeout:
        s = ok_samples()
        if s:
            last = s[-1]
            change_t = s[0]["t"]
            for i in range(len(s) - 1, 0, -1):
                if meter_key(s[i]) != meter_key(s[i - 1]):
                    change_t = s[i]["t"]
                    break
            # 手動記的讀數沒有自動取樣的保鮮度可言，直接當它夠新
            fresh = bool(last.get("manual")) or time.time() - last["t"] < a.fresh
            if fresh and time.time() - start >= a.min and last["t"] - change_t >= a.stable:
                why = "stable"
                break
        time.sleep(5)
    s = ok_samples()
    last = s[-1] if s else {}
    print(json.dumps({"result": why, "waited_s": round(time.time() - start), "meter": {k: last.get(k) for k in METER_KEYS},
                      "meter_time": local_hms(last["t"]) if last else None}, ensure_ascii=False))
    return 0


def cmd_pace(a) -> int:
    """給受控工作量用的節拍器：睡 N 秒後印一行。"""
    time.sleep(max(0.0, a.seconds))
    print(f"ok {a.k}")
    return 0


def cmd_status(a) -> int:
    s = read_jsonl(SAMPLES)
    oks = [x for x in s if x.get("ok")]
    alive = False
    if PIDFILE.exists():
        try:
            os.kill(int(PIDFILE.read_text().strip()), 0)
            alive = True
        except Exception:
            alive = False
    last = oks[-1] if oks else {}
    errs = [x for x in s[-20:] if not x.get("ok")]
    print(json.dumps({"sampler_alive": alive, "samples": len(s), "ok": len(oks),
                      "last_ok": local_hms(last["t"]) if last else None,
                      "age_s": round(time.time() - last["t"]) if last else None,
                      "meter": {k: last.get(k) for k in METER_KEYS},
                      "recent_errs": [e.get("err") for e in errs][-3:]}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Claude 訂閱額度換算實驗室")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample", help="常駐取樣：每隔幾秒問一次官方用量端點，寫進 samples.jsonl")
    p.add_argument("--email", default=os.environ.get("CLAUDE_CODE_USER_EMAIL", ""),
                   help="要監測的帳號（預設讀環境變數 CLAUDE_CODE_USER_EMAIL）")
    p.add_argument("--interval", type=float, default=30.0, help="基準取樣間隔秒數（預設 30；被限速會自動拉長）")
    p.add_argument("--verify-every", type=float, default=600.0, dest="verify_every",
                   help="隔幾秒對目標帳號的憑證重新做一次身分確認（預設 600）")
    p.add_argument("--until", default="", help="取樣到這個 UTC 時間為止（ISO 8601）；預設空＝一直跑到被殺掉")
    p.add_argument("--once", action="store_true", help="只取樣一次就結束並印出那一筆（不寫 pid 檔）")
    p.set_defaults(fn=cmd_sample)
    p = sub.add_parser("ledger", help="重建用量帳本（掃對話紀錄檔，印各模型小計）")
    p.add_argument("--since", default=None, help="只算這個 UTC 時間之後的呼叫（ISO 8601；預設讀 QUOTA_LAB_T0，都沒有時用內建研究起點）")
    p.add_argument("--only-project", action="append", default=None, metavar="SUBSTR",
                   help="只掃專案夾名含這個字串的紀錄檔（可重複給，多個是「或」）")
    p.add_argument("--exclude-sid", action="append", default=None, metavar="SID",
                   help="排除這個 sessionId 的呼叫紀錄（可重複給）")
    p.add_argument("--force", action="store_true", help="掃到 0 筆時仍把帳本寫成空檔（預設是不動既有帳本並結束碼 3）")
    p.set_defaults(fn=cmd_ledger)
    p = sub.add_parser("report", help="把每次額度跳動對上當時累計 API 等值美元（不寫檔）")
    p.add_argument("--since", default=None, help="帳本起算時間（ISO 8601；預設讀 QUOTA_LAB_T0，都沒有時用內建研究起點）")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("manual", help="手動記一筆額度讀數（沒有讀數程式時的替代做法）")
    p.add_argument("--all", type=int, required=True, help="「每週所有模型」的百分比")
    p.add_argument("--fable", type=int, default=None, help="「每週 Fable」的百分比（沒有就不填）")
    p.set_defaults(fn=cmd_manual)
    p = sub.add_parser("mark", help="記一筆實驗事件（某階段開始／結束），寫進 events.jsonl")
    p.add_argument("label", help="事件名稱（實驗格建議用 <名>-start／<名>-end）")
    p.add_argument("--note", default="", help="附註文字")
    p.add_argument("--dedupe-seconds", type=float, default=60.0, dest="dedupe_seconds",
                   help="這麼多秒內已有同名事件就不再記（預設 60；設 0 關閉）")
    p.set_defaults(fn=cmd_mark)
    p = sub.add_parser("wait", help="等到額度讀數連續穩定一段時間（給排程用）")
    p.add_argument("--stable", type=float, default=180.0, help="讀數要連續幾秒沒變才算穩定（預設 180）")
    p.add_argument("--min", type=float, default=0.0, help="至少等幾秒才可能結束（預設 0）")
    p.add_argument("--timeout", type=float, default=1200.0, help="最多等幾秒（預設 1200）")
    p.add_argument("--fresh", type=float, default=300.0, help="最新讀數距現在幾秒內才算新鮮（預設 300；手動讀數不受此限）")
    p.set_defaults(fn=cmd_wait)
    p = sub.add_parser("pace", help="節拍器：睡 N 秒後印一行（「反覆讀快取」型工作量用）")
    p.add_argument("seconds", type=float, help="要睡的秒數")
    p.add_argument("--k", default="", help="印出來的字串（方便對照第幾拍）")
    p.set_defaults(fn=cmd_pace)
    p = sub.add_parser("status", help="一行現況：取樣程序是否活著、最新讀數與最近的錯誤")
    p.set_defaults(fn=cmd_status)
    a = ap.parse_args()
    if refuses_committed_data(a.cmd):
        print("這個 data/ 放的是已發表的資料集，寫入類命令不在這裡跑。"
              "請把環境變數 QUOTA_LAB_DATA 設成你自己的資料夾（見 PROTOCOL 第 2 節）；"
              "如果你就是這份資料集的擁有者，請先建立 data/.write-ok。", file=sys.stderr)
        return 4
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
