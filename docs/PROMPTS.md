# 實驗用的提示詞與參數

這份文件把實驗裡交給模型的提示詞原文列出來，方便對照與重做。提示詞由 `bin/quota_cell_workflow.js` 依參數產生（只有 `stage1-sonnet` 那一格用的是 `bin/legacy_quota_burn_workflow.js`，見下）；下面的 `<…>` 是會被參數換掉的部分。提示詞用英文寫，是為了讓四個模型拿到完全一樣的指令。

每一個子任務（文件裡也叫「代理」）都是一個全新的對話，只拿到下面其中一種提示詞。三種工作的差別，在於成本主要落在哪一類 token。

## 共同的開頭

```text
Controlled usage-measurement job <tag> #<i>. Follow these steps exactly and do nothing else: no subagents, no web access, no skills, no notes, no reading or writing of any file not named here.
```

只有 `stage1-sonnet` 那一格用較早的另一支腳本，第一行的禁止事項措辭不一樣，其餘照下面的步驟：

```text
Controlled usage-measurement job <tag> #<i>. Follow these steps exactly and do nothing else: no subagents, no other files, no file writes, no web access, no skills, no notes.
```

## 一、讀檔（成本以快取寫入為主）

每個子任務讀 12 個固定內容的文字檔（「砝碼」，由 `bin/make_corpus.py` 用固定種子產生的無意義英文單字，一份約 40 萬 token），分 3 輪、每輪 4 個檔同時讀。

```text
Step A. Read the files below with the Read tool, passing allow_large: true on every call. Work in 3 rounds. In each round, issue all of that round's Read calls together in one single message (parallel tool calls). Do not start a round before the previous round's results are back.
Round 1:
- <corpus>/f_00000.txt
- <corpus>/f_00001.txt
- <corpus>/f_00002.txt
- <corpus>/f_00003.txt
Round 2:
- …
Round 3:
- …

Step B. After the last round, run this single Bash command exactly once:
wc -c <the 12 files> | tail -1

Step C. Then answer in about 300 English words describing statistical features you noticed in the text (typical word lengths, any repeated words, line lengths). End with a final line exactly in this form: DONE <tag> #<i> <total bytes reported by wc>
```

## 二、寫長文（成本以輸出為主）

每個子任務寫一部連載小說，每章至少九千個英文單字，一章一個檔。

```text
You are writing an original serialized novel. Premise seed: <tag>-<i>; invent the setting, characters and plot yourself. Write <parts> chapters. For each chapter k from 1 to <parts>, in order:
1. Call the Write tool once to create the file <scratch>/a<i>_ch<k>.md (replace <k> with the chapter number) containing the full text of chapter k. Each chapter must be at least <wordsPer> words of continuous original English prose: no outlines, no summaries, no placeholders, no repeated passages.
2. Write no commentary between chapters; go straight to the next Write call. Exactly one Write call per message; never issue two Write calls in the same message.

After chapter <parts> is written, reply with exactly: DONE <tag> #<i>
```

## 三、反覆讀快取（成本以快取讀取為主）

先照「讀檔」的 Step A 把同樣 12 個檔讀進來，接著連續做很多次很小的工具呼叫。每一次呼叫都會把整份上下文從快取重讀一遍。

```text
Step A.（同讀檔的 Step A）

Step B. Then run the following Bash command <calls> times, strictly one call per message and strictly in sequence, replacing <k> with 1, 2, 3, ... up to <calls>. Never put two tool calls in one message, never skip a number, never stop early, and write no text between calls:
python3 <repo>/bin/quota_lab.py pace 1 --k <k>

Step C. After call number <calls> has returned, reply with exactly: DONE <tag> #<i>
```

`quota_lab.py pace 1` 只是等 1 秒後印一行字，本身不做事；它的用途是讓模型每一輪都得重讀整份上下文。

## 驗收方式

回覆的最後一行必須剛好是指定的結尾標記（讀檔型還要帶 `wc` 回報的位元組總數），否則那個子任務算失敗。這個檢查抓不到「讀得比設計少卻照樣回報完成」的情形：Haiku 讀檔那一格 150 個子任務裡，有 31 個讀得偏少，其中至少 30 個照樣回報完成，是事後從帳本的 token 數發現的。判斷工作量請看帳本，不要只看完成數。

## 每一格實驗的參數

`agents` 是子任務數，`lanes` 是同時跑幾條線，`gap` 是同一條線上兩個子任務之間停幾秒。推理強度一律要求 `xhigh`（Haiku 的帳本實際記成 `low`）。每格結束後等讀數連續 300 秒不變才記結束事件。表裡的「主持對話（發動並記錄這次實驗的那個 Claude Code 對話，用的是 Fable 5.2）」自己的用量也會扣額度，見文末。

| 格 | 模型 | 工作 | 參數 |
|---|---|---|---|
| `stage1-sonnet` | Sonnet 5.5 | 讀檔 | 40 個子任務依序，每個 12 個檔，起始檔案編號 `start` 24，提示詞裡的標籤是 `s1-sonnet`（用較早的另一支腳本 `bin/legacy_quota_burn_workflow.js`；步驟與現在的相同，只差上面那個第一行，驗收只檢查回覆含有 `DONE`，詳見 PROTOCOL.md 第 5 節） |
| `s2-sonnet-write` | Sonnet 5.5 | 寫長文 | 10 個子任務並行，各寫 10 章，每章至少 9000 字（用 `bin/quota_cell_workflow.js` 的較早版本，提示詞與現在的腳本產生的相同） |
| `s3-sonnet-reread` | Sonnet 5.5 | 反覆讀快取 | `agents` 6、`lanes` 6、`calls` 250 |
| `o1-opus-digest` | Opus 5.5 | 讀檔 | `agents` 20、`lanes` 5（跑太快，作廢） |
| `o2-opus-write` | Opus 5.5 | 寫長文 | `agents` 14、`lanes` 14、`parts` 5、`wordsPer` 9000 |
| `o3-opus-reread` | Opus 5.5 | 反覆讀快取 | `agents` 6、`lanes` 6、`calls` 250 |
| `o1b-opus-digest` | Opus 5.5 | 讀檔 | `agents` 30、`lanes` 1 |
| `s3b-sonnet-reread` | Sonnet 5.5 | 反覆讀快取 | `agents` 6、`lanes` 6、`calls` 250 |
| `s2b-sonnet-write` | Sonnet 5.5 | 寫長文 | `agents` 16、`lanes` 16、`parts` 6、`wordsPer` 9000（每個子任務的章數比 s2 少，組成不同，不是嚴格重複） |
| `s1b-sonnet-digest` | Sonnet 5.5 | 讀檔 | `agents` 40、`lanes` 1 |
| `h1-haiku-digest` | Haiku 5.5 | 讀檔 | `agents` 150、`lanes` 5 |
| `f1-fable-digest` | Fable 5.2 | 讀檔 | `agents` 6、`lanes` 1、`gap` 200 |
| `f2-fable-write` | Fable 5.2 | 寫長文 | `agents` 3、`lanes` 3、`parts` 4、`wordsPer` 9000 |
| `f3-fable-reread` | Fable 5.2 | 反覆讀快取 | `agents` 3、`lanes` 3、`calls` 200 |
| `h3-haiku-reread` | Haiku 5.5 | 反覆讀快取 | `agents` 12、`lanes` 12、`calls` 250（被主持對話的用量干擾，只有下限） |

表裡的參數是依事件備註，加上主持對話的原始紀錄（子任務收到的提示詞原文，不在本庫）整理的。核對方式：用現在的腳本照表中參數重新產生提示詞，和每個子任務實際收到的提示詞逐行比對；去掉現在腳本才有的檔案路徑單引號後，除了 `stage1-sonnet` 的第一行，表中每一格的每個子任務都完全一致。本資料集所有格的開始事件備註都沒有記參數，現在的 `bin/quota_campaign_workflow.js` 才會把完整參數寫進去。每格實際跑出來的子任務數、每個子任務的中位數用量，以 `data/cells.csv` 的 `agents_n`、`agent_usd_median` 為準。`stage1-sonnet` 開始之前（15:54）另有一次沒有記事件的 2 個 Sonnet 子任務試跑（同一支舊腳本，不屬於任何一格）。

## 可直接貼的參數

下面三個物件各對應一種工作，數值取自這次實驗某一格實際用的參數（括號是格名）。`root` 換成這個庫在你機器上的絕對路徑（結尾要有斜線），`data` 換成你自己的資料夾（絕對路徑，結尾不加斜線，選填），`tag` 換成你自己的格名；沒寫的參數用預設值（`start` 0、`corpusSize` 240、`words` 300）。`gap` 填 0 是不停頓；要讓讀數取樣跟得上單價高的模型時才加大（`f1-fable-digest` 讀檔那一格用 200）。

讀檔（`s1b-sonnet-digest`）：

```json
{"root":"/path/to/claude-quota-study/","data":"/path/to/runs/my-run","kind":"digest","model":"sonnet","effort":"xhigh","agents":40,"lanes":1,"filesPer":12,"batch":4,"gap":0,"settle":300,"tag":"s1b-sonnet-digest"}
```

寫長文（`s2b-sonnet-write`）：

```json
{"root":"/path/to/claude-quota-study/","data":"/path/to/runs/my-run","kind":"write","model":"sonnet","effort":"xhigh","agents":16,"lanes":16,"parts":6,"wordsPer":9000,"gap":0,"settle":300,"tag":"s2b-sonnet-write"}
```

反覆讀快取（`s3b-sonnet-reread`）：

```json
{"root":"/path/to/claude-quota-study/","data":"/path/to/runs/my-run","kind":"reread","model":"sonnet","effort":"xhigh","agents":6,"lanes":6,"filesPer":12,"batch":4,"calls":250,"gap":0,"settle":300,"tag":"s3b-sonnet-reread"}
```

在 Claude Code 裡用 Workflow 工具，傳入腳本路徑 `bin/quota_cell_workflow.js` 和上面其中一個參數物件來執行。跑之前要先把環境變數 `QUOTA_LAB_DATA` 指到你自己的資料夾，而且要在啟動 Claude Code 之前設好，其餘步驟（記開始與結束事件、取樣）見 PROTOCOL.md 第 2 到 5 節。

## 主持對話

主持對話的用量也會扣額度，所以分析時把它和子任務分開算（帳本裡沒有 `agent` 代號的就是主持對話）。受控格進行時主持對話盡量不發話；有發話的格，RESULTS.md 會標出主持對話在夾住區間內的用量，並把它從上限扣掉。
