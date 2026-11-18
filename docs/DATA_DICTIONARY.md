# 資料欄位說明

時間欄位：`ts` 是 UTC 的 ISO 8601 字串，`t` 是同一時刻的 Unix 秒數。圖表與報表顯示的是台灣時間（UTC+8）。

這個庫的資料只涵蓋受控實驗這一段（週額度 0% 到 51%，2026-11-17 15:50 到 2026-11-18 01:45，台灣時間，約 10 小時，都在同一個每週額度視窗內）。

## data/samples.jsonl（額度讀數，一行一次嘗試）

| 欄位 | 說明 |
|---|---|
| `ok` | 這次有沒有讀到。`false` 時只有 `err` |
| `err` | 失敗原因（例外類別名稱或訊息開頭；被官方限速時帶 `rate_limited`） |
| `iv` | 當時的取樣基準間隔（秒；被限速會自動拉長） |
| `weekly_all` | 「每週所有模型」百分比（整數） |
| `scoped_fable` | 「每週 Fable」百分比（整數；Fable 的上限是總額度的 50%，所以這裡的 1 點等於總額度的 0.5%） |
| `util7d` | 官方回應裡的七天用量（與 `weekly_all` 同值） |
| `util5h` | 5 小時視窗讀數；這個方案沒有，為空 |
| `shares` | 官方用量回應裡 `weekly_scoped_shares` 的原樣內容，是一個清單（這份資料每筆只有一項，就是 Fable 分項）。三個子欄位：`limit_index`（分項的序號，這份資料恆為 1）、`used_percent_of_weekly`（這個分項已用掉總額度的百分之幾，整數）、`allowance_percent_of_weekly`（這個分項最多可以用總額度的百分之幾，這份資料恆為 50） |
| `bd` | 各產品占用量的百分比，四個子欄位 `claude_code`、`chat`、`cowork`、`other`，加起來是 100；2026-11-17 19:36（台灣時間）之後才有。這份資料裡有這個欄位的每一筆都是 `claude_code` 100、其餘 0 |
| `manual` | `true` 表示這筆是人工抄錄 |
| `refresh_try` | `true` 表示這筆之前做過一次登入續期 |

`data/payload_changes.jsonl`（官方用量回應的內容有變化時才記一筆）只由自動取樣程式（`quota_lab.py sample`）寫入，不屬於公開的資料；手抄讀數不會產生它。

## data/ledger.jsonl（用量帳本，一行一次模型呼叫）

| 欄位 | 說明 |
|---|---|
| `model`、`effort` | 模型代號、推理力度 |
| `in` | 一般輸入 token（未命中快取、也未寫入快取的部分） |
| `cw5m`、`cw1h` | 寫入快取的 token，分 5 分鐘與 1 小時兩種存活時間 |
| `cr` | 從快取讀取的 token |
| `out` | 輸出 token（含推理） |
| `think` | 其中屬於推理的 token（已含在 `out` 內，不另計價） |
| `tier`、`speed` | 服務層級、速度模式 |
| `sid` | 對話代號（流水號 `s1`、`s2`…） |
| `side`、`agent` | 是否為子任務，以及子任務代號（流水號 `a0001`、`a0002`…）。`agent` 是空值的列，就是發動實驗的主持對話 |
| `usd` | 這次呼叫照 API 牌價的美元數 |
| `usd_parts` | 依類別拆開：`in`、`out`、`cr`、`cw5m`、`cw1h` |

## data/events.jsonl（實驗事件）

`label`（格名加 `-start` 或 `-end`）、`note`（說明）、`meter`（當時最近一筆讀數）、`meter_age_s`（那筆讀數距當時幾秒）。

## data/jumps.csv（跳點表，`analyze.py jumps` 產生）

每列一次讀數變動（讀數變大的那一刻）。只知道變動發生在「前一筆讀數」與「新讀數」之間，不知道確切時刻。

| 欄位 | 說明 |
|---|---|
| `meter` | 哪個讀數變動：`weekly_all`（每週所有模型）或 `scoped_fable`（每週 Fable 分項） |
| `from`、`to` | 變動前、變動後的讀數（整數；可能一次跳不只 1 點）。`scoped_fable` 的單位是分項點數，1 點＝總額度的 0.5% |
| `local_before` | 變動前最後一筆讀數的時間（台灣時間，月-日 時:分:秒）；那一筆的讀數還是 `from` |
| `local_after` | 第一筆讀數是 `to` 的時間（台灣時間，格式同上） |
| `gap_s` | `local_before` 到 `local_after` 相差的秒數（四捨五入到整秒），也就是變動可能發生的時間窗 |
| `usd_<模型>_before`、`usd_<模型>_after` | 該模型從帳本第一筆（2026-11-17 15:44，台灣時間）累計到 `local_before`、`local_after` 這一刻，照牌價的美元數，取到小數三位。`<模型>` 是 `Fable 5.2`、`Opus 5.5`、`Sonnet 5.5`、`Haiku 5.5`，欄名裡有空格。兩欄相減，就是這個時間窗裡該模型用掉的牌價美元 |
| `usd_<模型>_<類別>_after` | 到 `local_after` 為止，該模型各類 token 的累計美元：`in` 一般輸入、`cw5m` 5 分鐘快取寫入、`cw1h` 1 小時快取寫入、`cr` 快取讀取、`out` 輸出。五類加起來約等於 `usd_<模型>_after`（各欄各自四捨五入，會差 0.001 左右） |

## data/cells.csv（實驗格表，`analyze.py cells` 產生）

每列一格實驗（事件 `<名>-start` 到 `<名>-end`）。

| 欄位 | 說明 |
|---|---|
| `cell`、`model` | 格名、主模型 |
| `start`、`end` | 起訖時間（台灣時間） |
| `usd_main`、`usd_other_models` | 主模型、其他模型在格內照牌價的美元數 |
| `share_in`、`share_cw5m`、`share_cw1h`、`share_cr`、`share_out` | 主模型各類 token 的美元數占主模型總額的比例 |
| `meter_from`、`meter_to` | 格內讀數起訖（整數百分比） |
| `jumps_inside` | 格內跳點數（跳點前後兩筆讀數都落在開始與結束事件之間才算） |
| `span_pct` | 格內第一個到最後一個跳點之間的百分點 |
| `lead_s` | 算上限時容許本機紀錄檔落後的秒數（預設 60） |
| `meter_used` | 用哪個讀數夾範圍：`weekly_all`（總額度）或 `scoped_fable`（Fable 分項） |
| `scoped_from`、`scoped_to` | 格開始與結束時的 Fable 分項讀數 |
| `controlled` | 是不是受控格。Fable 格：主持對話同時段的 Fable 美元最多是子任務 Fable 美元的 10%；其他格：主模型占格內非 Fable 美元至少 95% |
| `host_fable_usd_window` | 夾取區間內主持對話的 Fable 美元 |
| `usd_per_pct_lo`、`usd_per_pct_hi` | 「每 1% 對應多少美元」的下限與上限 |
| `usd_per_pct_hi_adj` | 扣掉主持對話的 Fable 用量後的上限。Fable 格不做修正，這一欄與 `usd_per_pct_hi` 相同。`upper_unreliable` 為 `True` 時這一欄是空的（上限算不出來） |
| `upper_unreliable` | `True` 表示主持對話占掉這段讀數差太多，上限不可靠，只引用下限；這時 `usd_per_pct_hi_adj` 是空的，`usd_per_pct_hi` 雖然有值，也不要引用 |
| `straddle_end` | 格尾是否落在最後一個跳點的區間內 |
| `agents_n`、`agents_low_n` | 主模型子任務數（只算美元數至少為中位數 20% 的），以及其中低於中位數 80% 的數量 |
| `agent_usd_median` | 上述子任務照牌價的美元中位數。同一份工作跨模型比較時，用它除以該格的範圍，得到「做一次扣多少百分點」 |

不是受控的格（`controlled` 為 `False`）不列範圍，三個 `usd_per_pct_*` 欄留空；受控但格內夾不出至少 1 個百分點的格（跳點不足），三個欄也是空的。

## data/fit/（`analyze.py fit` 與 `sweep` 產生）

三個假設在檔案與表裡的名字：`one`＝假設一（所有模型共用同一個換算，完全照牌價比例）、`model`＝假設二（每個模型各有一個換算，模型內各類 token 照牌價比例）、`free`＝假設三（每個模型的每一類 token 各有一個換算）。與 RESULTS.md 的假設一、二、三一一對應。

### `fit_lag<秒>_lead<秒>_slack<百分點>_unlogged<百分點>[_round|_ceil|_start0].json`

一組參數一個檔。檔名的 `_round`、`_ceil` 表示把讀數當成四捨五入或無條件進位（沒有後綴＝無條件捨去）；`_start0` 表示假設兩個讀數從 0 起跑（沒有後綴＝起跑真值未知）。

| 鍵 | 說明 |
|---|---|
| `n_constraints` | 限制條數。每個讀數值只用第一次與最後一次出現的那兩筆讀數，各產生一條限制，另加一條起跑值的限制 |
| `one`、`model`、`free` | 三個假設各自的結果，底下有 `feasible` 與下面兩組鍵之一 |
| `feasible` | `true`＝這個假設與讀數相容（有解）；`false`＝無解 |
| `extra_tolerance`、`total_tolerance` | 只在 `feasible` 為 `false` 時有。`extra_tolerance` 是在已經放寬的 `slack` 之外，還要再放寬多少個百分點才有解；`total_tolerance` ＝ `slack` ＋ `extra_tolerance`，就是 RESULTS.md 的「合計要容許」。單位是每個讀數自己的單位：總額度讀數是週額度的百分點，Fable 分項讀數是分項點數（1 點＝週額度的 0.5%）。連放寬都找不到解時是 `null` |
| `theta` | 只在 `feasible` 為 `true` 時有：各個換算值的可行範圍，每項是 `[下限, 上限]`，單位是每 1 美元牌價扣多少個百分點，倒數就是「每 1% 等於多少美元」；上限是 `null` 表示讀數沒有限制到它。項目的鍵：假設一是 `all`；假設二是模型代號；假設三是 `<模型代號>\|<token 類別>`（類別同帳本：`in`、`cw5m`、`cw1h`、`cr`、`out`） |
| `start_offsets` | 只在 `feasible` 為 `true` 且起跑值未知時有：兩個讀數起跑時真值的可行範圍，`w0` 是總額度讀數（百分點）、`f0` 是 Fable 分項讀數（分項點數） |
| `lag`、`lead`、`slack`、`unlogged`、`rounding`、`start` | 這個檔用的參數。`lag`：讀數比實際用量晚反映的秒數上限。`lead`：本機紀錄檔比伺服器計量晚寫入的秒數上限。`slack`：每一條限制都對稱放寬的百分點。`unlogged`：只放寬「用量不小於讀數」那一半的百分點（帳本沒記到的用量）。`rounding`：假設讀數怎麼取整，`floor` 無條件捨去、`round` 四捨五入、`ceil` 無條件進位。`start`：`free` 起跑值未知（不超過第一筆讀數加 1 點），`zero` 假設剛好是 0 |
| `n_samples`、`last_sample_ts` | 用了幾筆讀數；最後一筆讀數的時間（UTC，ISO 8601） |
| `mult_cr`、`mult_cw1h`、`mult_cw5m` | 倍率掃描：把快取讀取、1 小時快取寫入、5 分鐘快取寫入的牌價各自乘上 0 到 6 倍（每 0.05 一格），其餘照牌價，用假設二（每個模型各有一個換算）檢查。清單裡是讓假設二與讀數相容的倍率；空清單＝沒有任何倍率相容 |

這份資料發布的 5 個檔，三個假設都是 `feasible: false`，所以只有 `extra_tolerance`、`total_tolerance`，沒有 `theta` 與 `start_offsets`，三個 `mult_` 清單也都是空的。`theta` 與 `start_offsets` 的寫法是從 `analyze.py` 讀出來的，這份資料裡沒有實例。

### `sweep.csv`

把時間容許值（`lead`）與誤差容許值（`slack`）掃一遍的長表（`lag` 與 `unlogged` 固定為 0，讀數取整用預設的無條件捨去，起跑值未知）。每組 `lead`、`slack` 先有三列（`one`、`model`、`free` 各一列）；`free` 有解時，後面再接每個「模型 × token 類別」一列（累計不到 1 美元的類別不列）。

| 欄位 | 說明 |
|---|---|
| `lead` | 本機紀錄檔比伺服器計量晚寫入的秒數上限（0、30、60、90、120、180、240、300） |
| `slack` | 每一條限制都放寬的百分點（0、0.1、0.25） |
| `hypothesis` | `one`、`model`、`free`，對應假設一、二、三 |
| `feasible` | 這個假設在這組參數下與讀數是否相容（`True` 或 `False`） |
| `total_tolerance` | 不相容時，合計要容許的百分點（同上面 JSON 的 `total_tolerance`）；相容時是空的 |
| `model`、`token_class` | 只在「`free` 有解後面接的列」才有值：模型代號與 token 類別（`cw5m`、`cw1h`、`cr`、`out`；`in` 因累計不到 1 美元不出現）。其他列是空的 |
| `usd_per_pct_lo`、`usd_per_pct_hi` | 只在上面那種列才有值：這個模型、這一類 token 每 1% 對應的牌價美元的可行範圍。`usd_per_pct_hi` 是空的表示沒有上界（讀數沒有限制到它） |

RESULTS.md 的「放寬時間誤差」與「帳本沒記到的用量」兩節，是 `analyze.py summary` 執行時當場算的，沒有存成 JSON；放寬時間誤差那一組可以用 `analyze.py fit --lag 600 --lead 300 --slack 0.1` 重現。

## RESULTS.md（`analyze.py summary` 產生）

對外引用的數字來自這份檔案與 `bin/compare_models.py` 的輸出（跨模型的比較倍數，用 `data/cells.csv` 與 `prices.json` 算出）。兩者都由資料產生，內容只由資料決定。快取讀取相對快取寫入的換算範圍（README 結論第 5 點、`docs/KNOWN_LIMITATIONS.md`）來自 `bin/cache_weight_bounds.py` 的輸出，它讀 `data/cells.csv` 與 `prices.json`，輸出不存成檔案。

## corpus.sha256

測試檔每一個檔的 sha256 與總雜湊，供 `make_corpus.py --verify` 比對。

## prices.json

`models.<模型代號>` 下的 `in`、`out`、`cr`、`cw5m`、`cw1h` 是每百萬 token 的美元牌價；`long_context` 是超過門檻時整筆請求改用的價格。`_source` 是來源網址與核對日期。

## 公開前做過的處理

- 帳本拿掉了訊息代號、請求代號、來源檔名與進入點；對話與子任務代號依首次出現的順序換成流水號。列的順序不變。
- 讀數只留上表的欄位；官方用量回應的原始內容沒有放進來。
- 事件的說明文字改寫成公開版，時間與格名不變。
- 事件檔拿掉了四筆不是量測的註記事件（兩則決定的紀錄，以及一批預定但取消、沒有跑的工作的開始與取消紀錄），另有一筆註記事件的標籤改名為 `other-work-note`（它記的是某段時間有與實驗無關的背景文字工作）。這五筆都不是實驗格的開始或結束，不影響任何結果。
- 這些處理不改任何數值：用這裡的資料算出來的實驗格表，與原始資料算出來的相同。
