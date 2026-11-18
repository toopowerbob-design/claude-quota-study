#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_corpus.py：產生固定亂數種子的合成文字檔，當作「讀檔工作量」的標準砝碼。

用法：
  make_corpus.py [檔案數=240] [每檔位元組數=60000]        產生 corpus/
  make_corpus.py --write-manifest                        產生（或更新）corpus.sha256
  make_corpus.py --verify                                只驗 corpus/ 與 corpus.sha256 是否相符（不重做）

同一個種子每次產出的內容完全相同，所以實驗可以重做。
結束碼：0 成功／相符，1 manifest 不符，2 缺字典檔（/usr/share/dict/words，macOS 內建）、缺 manifest 或 Python 版本太舊。
"""
import sys

if sys.version_info < (3, 10):  # 這一段要在 3.9 也能解析：只用到 sys 與 print
    print("需要 Python 3.10 以上（目前是 " + sys.version.split()[0] + "）；macOS 內建的 python3 是 3.9，請另外安裝較新的版本。")
    raise SystemExit(2)

import hashlib
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DICT = Path("/usr/share/dict/words")
OUT = ROOT / "corpus"
MANIFEST = ROOT / "corpus.sha256"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_words() -> list[str] | None:
    """讀字典檔；讀不到就回 None（呼叫端據此結束碼 2）。"""
    if not DICT.exists():
        print(f"錯誤：找不到 {DICT}（需要 macOS 內建的英文單字表；本工具只在 macOS 驗證過，沒有這個檔就產不出語料）", file=sys.stderr)
        return None
    print(f"字典檔 {DICT} sha256={sha256_file(DICT)}")
    words = []
    for w in open(DICT, encoding="utf-8", errors="replace"):
        w = w.strip()
        if 3 <= len(w) <= 9 and w.isalpha() and w.islower():
            words.append(w)
    return words


def write_manifest() -> int:
    files = sorted(p for p in OUT.glob("*") if p.is_file())
    if not files:
        print(f"錯誤：{OUT} 裡沒有檔案，先產生語料再寫 manifest", file=sys.stderr)
        return 2
    lines, digests = [], []
    for p in files:
        h = sha256_file(p)
        lines.append(f"{h}  {p.name}")
        digests.append(h)
    total = hashlib.sha256("".join(digests).encode("ascii")).hexdigest()
    lines.append(f"TOTAL {total}")
    MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(files)} 個檔案 → {MANIFEST}（TOTAL {total}）")
    return 0


def verify() -> int:
    if not MANIFEST.exists():
        print(f"錯誤：沒有 {MANIFEST}，請先執行 --write-manifest", file=sys.stderr)
        return 2
    want, total = {}, None
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("TOTAL "):
            total = line.split(None, 1)[1]
            continue
        h, name = line.split(None, 1)
        want[name.strip()] = h
    bad = []
    for name, h in want.items():
        p = OUT / name
        if not p.exists():
            bad.append(f"{name}：manifest 有記載，但 corpus/ 裡找不到")
        elif sha256_file(p) != h:
            bad.append(f"{name}：內容與 manifest 不符")
    extra = sorted(p.name for p in OUT.glob("*") if p.is_file() and p.name not in want)
    for name in extra:
        bad.append(f"{name}：corpus/ 有這個檔，manifest 沒有記載")
    if total:
        digests = [want[n] for n in sorted(want)]  # manifest 的順序＝檔名排序
        if hashlib.sha256("".join(digests).encode("ascii")).hexdigest() != total:
            bad.append("TOTAL 與各檔雜湊串接的結果不符")
    elif want:
        bad.append("manifest 沒有 TOTAL 行")
    if bad:
        print(f"語料驗證不符（{len(bad)} 項）：")
        for b in bad:
            print("  - " + b)
        return 1
    print(f"語料驗證相符：{len(want)} 個檔案、TOTAL {total}")
    return 0


def main(argv: list[str]) -> int:
    if "--verify" in argv:
        return verify()
    args = [a for a in argv if not a.startswith("--")]
    n = int(args[0]) if len(args) > 0 else 240
    size = int(args[1]) if len(args) > 1 else 60000
    words = read_words()
    if words is None:
        return 2
    OUT.mkdir(exist_ok=True)
    rng = random.Random(1151117)  # 固定種子：任意整數，改了語料就不同
    for i in range(n):
        lines, total = [], 0
        while total < size:
            line = " ".join(rng.choice(words) for _ in range(rng.randint(10, 16)))
            lines.append(line)
            total += len(line) + 1
        (OUT / f"f_{i:05d}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{n} files x ~{size} bytes -> {OUT}; vocab {len(words)}")
    if "--write-manifest" in argv:
        return write_manifest()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
