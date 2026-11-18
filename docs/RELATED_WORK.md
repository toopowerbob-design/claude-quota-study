# 別人做過的類似實測

這個庫的實驗是獨立設計、獨立完成的：設計與量測在 2026-11-17 到 11-18（受控實驗是 2026-11-17 15:50 到 2026-11-18 01:45 台灣時間，即 UTC+8，約 10 小時，都在同一個週額度窗口內），當時沒有參考下面任何一份資料。做完之後（2026-11-18）才去搜尋英文、日文、簡體中文、繁體中文、韓文等語言的公開資料，發現早就有人量過同一個問題。

所以要先講清楚：**這裡不是第一個量的。** 2026 年以來，至少有十幾份公開的實測，拿訂閱介面的用量百分比去對 API 牌價。這份文件列出找到的資料與完整出處，說明哪些結果和這裡相似、哪些不同、這裡多做了什麼。

文末的「出處」一節列出每一份的作者、標題、日期與連結。標題與作者是 2026-11-18 重新打開原頁抄錄的；日期取自頁面上顯示的日期，頁面沒有顯示的，取自頁面的結構化資料或 GitHub API 回傳的建立與更新時間，這類日期在條目裡逐一標明。GitHub 條目的「更新」指頁面中繼資料的最後更新日，不一定是作者改了內容：議題被機器人關閉、有人留言也會更新。Pasquale Pillitteri 的兩頁只經網頁摘要工具讀取，沒有對照原始網頁。打不開或頁面上沒有寫的，照實標明。下面對每一份的描述是這裡的摘要，內容以原文為準；如果有寫錯，歡迎開 issue 指正。

## 最接近的幾份

| 來源 | 量的是什麼 | 做法 | 和這裡比 |
|---|---|---|---|
| SemiAnalysis，2026-10-05 [1]（中文轉述 [2]） | Anthropic 的 Pro、Max 等方案（公開部分明確寫到的模型是 Opus 5.5 與 Fable 5.1；Sonnet 5.5 只出現在各模型的價值比較處），以及 OpenAI 等多家公司的訂閱 | 受控實驗：一次只量一種 token（一般輸入、快取寫入、快取讀取、輸出），每次請求後讀 5 小時與 7 天用量表，量到範圍收斂才停 | 做法很接近（固定工作、讓成本集中在某一類 token、看額度表怎麼動），而且更早、涵蓋的方案與公司多得多。文章後段在付費牆後，這裡只讀到公開的部分。可見部分沒有 Haiku 5.5 與 Fable 5.2 |
| tksfjt1024，Zenn，2026-10-01 [3] | Max 20x 方案（量測在 2026 年 9 月）；Haiku 4.5、Sonnet 5 與 5.5、Opus 5 與 5.5、Fable 5.1 | 在 Claude Code 與 API 之間放一個轉送程式，記下每次請求的各類 token 與用量讀數；求出六個模型各一個權重，以及輸入、快取寫入、輸出、快取讀取四類 token 的一組係數（係數只在部分模型量過：快取寫入只在非互動的 Opus 5，輸出與快取讀取只在 Opus 5、Opus 5.5、Sonnet 5.5，Haiku 與 Fable 沒量） | 最接近。九月底就已經回答了「各模型的扣法和牌價不同」。把這個公式套到這裡的資料，大部分對得上（見下一節）。沒有 Haiku 5.5 與 Fable 5.2 |
| Atlas ADE 文件頁，2026-10-29 更新 [4]（轉述 [5]） | Ultra（原 Max 20x）一個帳號；以 Opus 為主，Sonnet 與 Fable 5.1 為估計 | 用自己的逐次請求紀錄對照讀數，擬合出 token 換成額度的模型 | 方向一致：Opus 每 1% 換到的牌價，快取讀取（約 34 美元）和快取寫入（五分鐘價約 23 美元、一小時價約 37 美元）都比輸出（約 13 美元）、輸入（約 12 美元）高很多。這些美元數是這裡用 `prices.json` 的 Opus 5.5 牌價，把該頁的 token 數自己換算的（該頁自己標的快取讀取約 35 美元）。輸出那一類的數字比這裡低（見「不一樣、或看起來不一致的地方」一節），該頁寫明輸出的數字是擬合裡最不確定的部分 |
| Wokey，2026-11-16 更新 [6] | Pro、Team、Ultra 等方案多個帳號的真實流量，每週更新 | 觀察：API 閘道服務後面的真實訂閱帳號，用讀數上升的幅度對照流量，照牌價推估一整週 | 2026-11-09 到 11-16 那一週，Ultra 約 1,576 美元，和這裡外推的同一個量級。它經營 API 閘道服務（頁面上說額度用完可以改用它的服務）；量的是混合流量，不是受控實驗 |
| Kanttz，Fei2411/real-api-pricing 第 96 號 issue，2026-10-02 [7] | Max 20x 一個帳號；以 Opus 5.5 為主 | 和這裡一樣兩條資料線：整數的週百分比，對本機對話紀錄乘牌價 | 資料線最像。真實工作混合下，每 1% 約 25 美元（兩個週窗各約 25.78 與 24.84），貼著這裡 Opus「寫長文」範圍的上緣（24.95），低於「反覆讀同一份資料」的範圍（30.44 起） |
| zhuran24，anthropics/claude-code 第 88352 號 issue，2026-08-20 [8] | Max 20x；Fable 5 與 Opus、Sonnet 混用；當時有週額度加量活動 | 本機對話紀錄去重後照牌價加總，除以用量百分比；用「Fable 分項是總額度的一半」交叉驗證 | 帳本做法、去重的陷阱、Fable 上限一半，都和這裡一樣。沒有分模型、分工作的受控實驗 |
| 中島健雄，DevelopersIO，2026-08-20 [9] | Max 20x 與 Max 5x；Opus 與 Fable 5；加量活動期間 | 觀察：週百分比對本機紀錄，三種估法 | 方向一致（不照牌價、Fable 每一美元扣得比 Opus 多）。整週的美元數比這裡外推的高很多，可能和當時的加量活動、以及九月規則改變之前快取讀取幾乎不計有關 |
| bestagent.dev，2026-11-12 [10] | Max 5x；Fable 5.2 對 Opus 5.5 | 受控對照：同一個公開程式庫、同一個提示，用非互動的 `claude -p` 讓兩個模型各跑 3 次，每次前後讀 5 小時用量表 | Fable 合計 +18 點、Opus 合計 +4 點，約 4.5 倍，落在這裡量到的 1.8 到 6.4 倍（同一份工作，Fable 對 Opus 扣的額度）之內。只有一個任務，量的是 5 小時表 |

另外幾份和這裡的某一部分有關：

- qvarker 的 issue 與 kler 的測試工具 [11]：qvarker 用同一份固定工作，分別以非互動的 `claude -p` 與互動介面各跑一次，在 5 小時用量表上差約 1.8 倍。kler 的工具用同一種啟動方式、只改入口標籤，比較 5 小時表的差別，並公開了程式與逐次結果。兩者都沒有量每週的表。它們提醒了一件這裡沒有控制的事：用量從哪個入口來，扣法可能不同。
- NTRYourWaifu，Fei2411/real-api-pricing 第 52 號 issue [12]：Pro 方案（兩個帳號合併）上，Opus 5.5 每 1% 週額度約 3.21 美元的牌價，同頁提到 Opus 5 約 4.96 美元。同一個方案裡兩個模型每 1% 換到的牌價不同，和這裡「不是牌價乘固定倍數」的方向一致。
- AI Dive，DEV Community [13]：用非互動方式跑了 182 次（Haiku 5.5、Sonnet 5.5、Opus 5.5 當子任務），整組只讓 5 小時表動了約 2 點、每週的表沒有動。讀數的解析度只有 1 點，分不出模型之間的差別。是這次找到的另一份有量 Haiku 5.5 的資料。
- Pasquale Pillitteri 轉述 Theo 在 X 的貼文 [14]：200 美元的 Ultra 方案每月約可用 15,000 美元牌價的 Opus 5.5，但只有約 3,000 美元的 Fable；Fable 的上限是週額度的一半。這是二手轉述，原始貼文這裡沒有讀到，轉述頁也只經網頁摘要工具讀取。

## 把別人的公式套到這裡的資料

tksfjt1024 [3] 在 2026 年 9 月量出的公式是：

    每次請求的用量單位 ＝ 模型權重 ×（輸入 ＋ 快取寫入 × 1.25 ＋ 輸出 × 5 ＋ 快取讀取 × 0.025）
    模型權重（以 Haiku 4.5 為 1）：Sonnet 5.5 ＝ 1.5、Opus 5.5 ＝ 2.2、Fable 5.1 ＝ 10
    週額度約 11.3 億單位

把這個公式套到這裡每一格實驗的 token 組成，算出「公式預測每 1% 對應多少美元」，再和這裡量到的範圍比。程式是 `bin/prior_formula_check.py`，任何人都可以重跑。

| 格 | 模型 | 公式預測每 1% 美元 | 這裡量到的範圍 | 結果 |
|---|---|---|---|---|
| `o1b-opus-digest` | Opus 5.5 | 21.55 | 13.21 ~ 23.71 | 範圍內 |
| `o2-opus-write` | Opus 5.5 | 20.77 | 18.84 ~ 24.95 | 範圍內 |
| `o3-opus-reread` | Opus 5.5 | 36.95 | 30.44 ~ 50.81 | 範圍內 |
| `stage1-sonnet` | Sonnet 5.5 | 15.79 | 10.49 ~ 19.18 | 範圍內 |
| `s1b-sonnet-digest` | Sonnet 5.5 | 15.80 | 9.45 ~ 20.39 | 範圍內 |
| `s2-sonnet-write` | Sonnet 5.5 | 15.58 | 14.03 ~ 16.11 | 範圍內 |
| `s2b-sonnet-write` | Sonnet 5.5 | 15.27 | 13.52 ~ 14.97 | 高於上限 0.30 |
| `s3-sonnet-reread` | Sonnet 5.5 | 27.03 | 20.94 ~ 41.14 | 範圍內 |
| `s3b-sonnet-reread` | Sonnet 5.5 | 27.03 | 11.86 ~ 35.97 | 範圍內 |
| `f1-fable-digest` | Fable 5.2 | 9.08 | 8.52 ~ 14.19 | 範圍內 |
| `f2-fable-write` | Fable 5.2 | 9.04 | 9.16 ~ 12.32 | 低於下限 0.12 |
| `f3-fable-reread` | Fable 5.2 | 9.03 | 9.11 ~ 14.42 | 低於下限 0.08 |

Opus 三格全部落在範圍內，Sonnet 六格有五格，Fable 三格有一格、另外兩格貼著下限（差 0.1 美元左右）。

這個公式也替這裡的幾個發現提供了一個可能的解釋：

- 權重 2.2 對 1.5，所以同樣的 token，Opus 扣的額度約為 Sonnet 的 1.47 倍，不是牌價的 2 倍。這裡在寫長文量到的是 1.1 到 1.7 倍。
- 快取讀取的係數是一般輸入的 0.025 倍。Opus 5.5 的牌價，快取讀取是一般輸入的 0.05 倍，所以額度對快取讀取比牌價寬一倍；Fable 的牌價剛好就是 0.025 倍，所以沒有這個好處。Sonnet 5.5 的牌價比例，原文的表格寫 0.1，本庫的 `prices.json`（2026-11-17 核對官方定價頁）是 0.05；不管哪一個，額度都比牌價寬。這裡量到的情形與此相容：Opus 反覆讀同一份資料時，每 1% 換到的美元明顯較多；Sonnet 只有第一輪看到同樣的現象，第二輪的範圍太寬，分不出來；Fable 三種工作都差不多。這個公式裡快取讀取對快取寫入，每個 token 是 0.025 ÷ 1.25 ＝ 1/50；這裡用 `bin/cache_weight_bounds.py` 反推出來的每 token 範圍（Opus 1/34 到 1/160、Sonnet 1/28 到 1/135、Fable 1/25 到 1/115）都包含這個 1/50，而公式的作者沒有量過 Fable 的這一項。

要注意三件事。第一，這是該作者從實測反推的公式，不是官方公布的規則，原文也說明只在部分模型上量過。第二，套用時有四個假設，都沒有人證實：九月的係數到十一月還適用；Fable 5.2 沿用 Fable 5.1 的權重；兩種快取寫入都算 1.25；這裡的用量全部當互動用量算，沒有乘非互動倍率（這裡的用量是 Claude 桌面版裡的 Claude Code 發動的，不是 `claude -p`；[3] 對每週的表寫了一個非互動倍率 1.25，來源之間對這點意見不一，[11] 的討論串裡有一則留言寫每週的表不受影響）。第三，預測落在範圍內只能說「相容」，不能說這裡證實了那個公式，也不能說那份研究替這裡背書：這裡每一格的範圍本來就寬。Haiku 5.5 在原文裡沒有對應的權重，所以沒有比。

## 相似的地方

1. **訂閱額度不是把 API 帳單乘上一個固定倍數。** [3] 量到各模型的權重和牌價比例不同，各類 token 的係數則只在部分模型上量過；[12] 在同一個方案裡看到兩個 Opus 版本每 1% 換到的牌價不同。
2. **重複讀取同一份資料（快取讀取）比較省額度。** [3] 的係數、[4] 的分類數字、[6] 的每週統計都指向這一點。
3. **Fable 扣得比牌價兇，而且有只能用掉總額度一半的上限。** [3]（Fable 5.1 的權重 10，對 Opus 5.5 的 2.2）、[10]（同一任務約 4.5 倍；整數讀數、每個模型只跑 3 次的小樣本）、[14]（同一方案每月約 15,000 美元的 Opus 對約 3,000 美元的 Fable；單一使用者的早期數字，二手轉述，該文也說沒有人重做過）、[9]（Fable 5）。上限一半這件事，[1]、[8] 等好幾份都比這裡早看到。
4. **Opus 對 Sonnet，扣不到牌價的 2 倍。** [3] 的權重換算約 1.47 倍；[4] 的表格裡，每 1% 的 Sonnet token 數約為 Opus 的 1.7 倍（這裡自己除的；該頁的 Sonnet 欄沒有標版本，而且是估計值）。這裡在寫長文量到 1.1 到 1.7 倍（算法見 `bin/compare_models.py`）。
5. **200 美元方案一整週照牌價的量級。** 別人的數字從每週一千多美元到三千多美元都有（[4]、[6]、[7]、[8]、[9]），差別主要來自時間、全用 Opus 還是混合、快取讀取占多少。這裡外推的數字在中間。

## 不一樣、或看起來不一致的地方

- [9] 在 2026 年 8 月估的整週美元數（Max 20x 約 3,400 到 3,900 美元）比這裡外推的高很多。更早的資料中，she-llac（claude.ai 網頁版，2026-01，見「其他找到的資料」）寫快取讀取在訂閱內完全免費；[3] 也記錄九月上旬以前的算法不計快取讀取。[9] 本身只是推估快取讀取權重是牌價的 0 到 0.12 倍，原文說明這不是已確認的內部規格。這些都早於 [3] 記錄的 2026 年 9 月中旬那一次沒有預告的計算方式改變，當時也有週額度加量的活動，所以不算正面衝突，但這裡無法驗證。
- 別人量的方案（Pro、Max 5x、Max 20x、Team）、讀的表（5 小時或每週）、模型版本各不相同，數字不能直接互比。
- [4] 對輸出類 token 的估計比這裡「寫長文」那一格低：該頁單算輸出，每 1% 約 13 美元；把該頁各類 token 的數字套到這裡 `o2-opus-write` 那一格的 token 組成（輸出約占牌價的 55%、快取寫入約 43%），換算約每 1% 16.4 美元，仍低於這裡量到的 18.84 至 24.95。
- 這次沒有找到和這裡的結論正面矛盾的研究。

## 這裡多做的

只就這次搜尋到的範圍而言：

- 較新的版本（Haiku 5.5、Fable 5.2）在每週額度上量到可讀範圍的受控量測。別人相近的資料：[13] 跑了 Haiku 5.5，但每週的表整組沒動；[10] 受控比較 Fable 5.2，但只讀 5 小時的表；Fable 5.2 在每週表上另有一次單帳號、非受控的記錄（Fei2411/real-api-pricing 第 226 號 issue，見「其他找到的資料」）。
- 在同一個帳號（每月 200 美元的方案，介面顯示為 Ultra，即原本的 Max 20x）、同一個週額度窗口內，約 10 小時的量測期間，四個模型做同樣的固定工作並排比較（Haiku 沒有做寫長文）。
- 把「讀數怎麼取整」「起跑時的真值是多少」當成未知數，用線性規劃檢驗三層假設會不會和讀數矛盾。
- 公開每週額度的逐次用量帳本與讀數（去識別後），沒有訂閱帳號的人也能重算全部結果。

以下幾件事不是這裡獨有的，別人早就做過：從 0% 開始量、整數讀數對本機帳本、帳本去重、Fable 一半上限。

## 別人有做、這裡沒做的

- 週與週之間的變化（[6]、[8]）。這裡只量了一個星期。
- 互動與非互動、不同入口的差別（[11]；[3] 也量了非互動的倍率）。這裡的用量全部來自 Claude 桌面版裡的 Claude Code。
- 更細的讀數：[3] 讀的是每次請求回傳的用量資訊，比介面上的整數百分比細得多。
- 多個帳號、不同方案（[1]、[6]、[9]）。
- 5 小時用量表（[10]、[11]、[13]）。這裡的方案沒有這個表。

## 其他找到的資料

下面這些也和「訂閱額度對牌價」有關，但方法、年代或方案離這裡比較遠。每一份的描述都是摘要，以原文為準。

- Sam Stull。〈How many tokens is 1% of your Claude Code limit?〉。DEV Community，2026-10-26（頁面只顯示「Oct 26」，年份取自頁面結構化資料）。 https://dev.to/samstull9007/how-many-tokens-is-1-of-your-claude-code-limit-232n
  作者以自己一個帳號（方案頁面未寫明）約兩天（2026-10-23 至 25）的狀態列讀數與本機對話紀錄，對 5 小時額度表做無截距最小平方擬合（61 筆觀察），得出 1% 約等於 16.6 萬個新 token 或約 1,100 萬個快取讀取 token，快取 token 約是新 token 的 1/67。該頁帶有「由 AI 產生」的標籤。
- she-llac（網站名；頁面沒有署名）。〈suspiciously precise floats, or, how I got Claude's real limits〉。2026-01-25。 https://she-llac.com/claude-limits
  作者在 claude.ai 網頁版（一個 Max 5× 帳號）用量回應中讀到未取整的小數，以 Stern-Brocot 搜尋還原分數並取多筆樣本的公倍數，反推出 Pro、Max 5×、Max 20× 的 5 小時與每週額度點數，並導出 Haiku、Sonnet、Opus 的輸入輸出點數費率（比例與 API 牌價相同）與「快取讀取在訂閱內完全免費」的結論。
- Alex Dobrushskiy。〈The True Price of a Claude Max Weekly Limit (in API Dollars)〉。Botfarm，2026-03-20。 https://botfarm.run/blog/claude-max-true-price/
  作者以自家開源調度器 Botfarm 完成的 80 個自動編碼任務之 API 等值成本，對照週用量百分比（從 7 天表重置為 0% 起算）：Max 5x 窗口 21 小時（2026-02-27 至 28）、Max 20x 窗口 46 小時（02-28 至 03-01，週額度用掉 23%、成本 253 美元），得出 Max 20x 每 1% 約 10.99 美元、整週約 1,100 美元，工作量約 95% 為 Opus 4.6，作者說是下限估計。
- Abhishek Ray。〈I Tried to Reverse Engineer Claude Code's Usage Limits〉。Claude Code Camp，2026-04-01。 https://www.claudecodecamp.com/p/i-tried-to-reverse-engineer-claude-code-s-usage-limits
  作者在 Claude Code 與 API 之間放自寫的 Go 代理（claude-meter），擷取回應標頭的用量比例；以一個 Max 20x 帳號一週約 2.5 萬次請求（約 22.7 億 token，95.2% 為快取讀取）、依 Opus 4.6 公開牌價加權，估出 7 天額度中位數約 1,378 美元（範圍 718 至 2,659，只有 8 個區間）與 5 小時額度中位數約 164 美元（63 個區間）。
- FinOps LLM（網站名；頁面沒有個人署名）。〈How many tokens do you get with Claude Pro and Max? We counted them〉。2026-09-27（2026-11-14 更新）。 https://finopsllm.com/research/claude-pro-max-tokens-limit
  該站以自家 60 天研究中的 Claude Code 日誌，按每筆請求當天的 API 牌價計價、再以 2026 年 8 月 Opus 組成換算成 token：Max 20x 一週約 1,860 美元（約 19 億 token，週上限打到兩次）、Max 5x 一週至少 1,217 美元（下限，沒打到上限）、Max 5x 的 5 小時上限中位數約 3,900 萬 token（約 37 美元，12 次）；Pro 週額度未量。
- amargupta0428。〈[BUG] Weekly + Fable scoped meters deplete 1.7-5x faster since Aug 17 reset on Max 20x; OAuth token carried rateLimitTier default_claude_max_5x〉。GitHub，2026-08-17（2026-10-31 更新；該議題已被 stale bot 標記後關閉為 not planned，這是關閉日）。 https://github.com/anthropics/claude-code/issues/87419
  回報者以一個 Max 20x 帳號，自 2026-08-17 週額度重置後，用本機 JSONL（依 requestId 去重）的快取讀取 token 數對照週表與 Fable 分項表，主張每個 token 耗用額度比前幾週快至少 1.7 倍（標題寫 1.7–5 倍）：上週 28.5 億快取讀取＝100%（每 1% 約 2,850 萬），本週 2.14 億＝13%（每 1% 約 1,650 萬）；另指出 OAuth token 的 rateLimitTier 是 default_claude_max_5x，與組織的 20x 不符。
- ArkNill。〈ArkNill/claude-code-hidden-problem-analysis: Measured analysis of Claude Code cache bugs causing 10-20x token inflation on Max plans〉。GitHub，2026-04-01（2026-05-13 更新；頁面沒顯示建立日，兩個日期取自 GitHub API 的建立與最後推送時間）。 https://github.com/ArkNill/claude-code-hidden-problem-analysis
  作者在 Claude Code 與 API 之間放透明代理（cc-relay），記錄 2026-04-01 至 04-22 約 45,884 次請求（320 個工作階段，自己的 Max 20x 方案、以 Opus 為主），對照回應標頭的 5 小時與 7 天額度；歸納出 11 個已確認的客戶端錯誤（含快取錯誤）；每 1% 的 5 小時額度對應可見輸出約 9K 至 16K token、快取讀取約 150 萬至 210 萬 token。
- iteebz。〈iteebz/ccmeter: measure your actual limits〉。GitHub，2026-03-30（2026-07-14 更新；頁面沒顯示日期，取自 GitHub API 的建立與最後推送時間）。 https://github.com/iteebz/ccmeter
  一個開源（MIT）命令列工具：每 2 分鐘輪詢 Anthropic 用量 API 的 5 小時與 7 天百分比，讀 Claude Code 本機 JSONL 的 token 數，在百分比跳一格時把這段 token 依每 token 單價加權成美元，估出「每 1% 的預算」與整窗預算（README 範例為 Max 20x、Opus：5 小時 363 美元、7 天 1,900 美元）；README 明說 pro 基準值是推算、不是量測。
- Pasquale Pillitteri。〈SemiAnalysis: the $200 AI Plan Is Worth Up to $14,000 in Tokens〉。pasqualepillitteri.it，2026-06-12（只經網頁摘要工具讀取，標題與日期未對照原始網頁）。 https://pasqualepillitteri.it/en/news/4793/semianalysis-token-value-claude-chatgpt-plans
  網站轉述 SemiAnalysis 的實驗：各買一個 Anthropic 與 OpenAI 的付費方案，跑長時間代理式寫程式工作直到週額度用完，再依 API 牌價換算；結果 Claude Max 20x 約每月 8,000 美元、ChatGPT Pro（200 美元）最高約每月 14,000 美元，20 美元方案最高約 700 美元；這是二手轉述，作者沒有自己量。
- Clarivant（網站名；頁面沒有署名）。〈Claude Max Weekly Limit: What Fixed It (Updated Sep 2026)〉。2026-05-18（2026-09-24 更新）。 https://clarivant.ai/en/insights/claude-credits-multiplier
  作者稽核 24 天（82,973 個回合）Claude Code 對話紀錄（Claude Max 200 方案），以 token 數乘公開牌價得到 API 等值美元，當作週額度消耗的代理指標（沒有讀用量百分比表）；發現 99% 回合預設走 Opus，改用 opusplan 後每回合成本由 0.215 降到 0.090 美元；9 月更新承認舊腳本重複計算且漏掉子代理紀錄，可重跑的整週讀數高估 45% 至 85%；作者說結論的方向沒有變。
- Ted Kwartler, Alan Aqrawi, Arian Abbasi。〈Harness Tokenomics: A Router for the Enterprise Agentic Control Plane〉。arXiv.org，2026-09-24（2026-09-26 更新）。 https://arxiv.org/abs/2609.28919
  論文提出一個路由器（分類器 Jev）依提示類型分派模型，只在不必重建提示快取之處換模型；以 2026-09-21 的 Anthropic 牌價，在模擬 1 萬席企業中省下 13% 至 21% 的模型支出（每年 330 萬至 510 萬美元），並報告在長的工具密集工作階段中最貴模型可能比下一級更便宜（用約 1 萬個公開資料集工作階段重新計價驗證）；摘要沒有提到量測訂閱額度表（只讀了摘要頁，全文未讀）。
- skipbit。〈Claude Code を週19,066回呼んでわかった、トークンを食っていたもの〉。Zenn，2026-08-12（2026-08-15 更新）。 https://zenn.dev/skipbit/articles/2026-08-token-call-count
  作者在 Max 20x 方案（週額度加 50% 活動期間內）以 jq 彙整本機 Claude Code 日誌（三週：2026-07-12 至 08-02，另追加第四週），把週 token 拆成「呼叫次數」與「每次 token」，並以 Opus 5 牌價概算；呼叫次數三週由 19,066 降到 11,314 再到 6,503（-65.9%），總 token 減 65.6%；週額度百分比無法由本機日誌重建（同一週內每 1 點的 token 量可差 1.8 倍）。
- がじぇ。〈Claude Codeの5時間制限をAPI料金換算すると約62ドルだった ※Claude TeamプランのPremiumシートの場合〉。Zenn，2026-05-07。 https://zenn.dev/sato_shogidemo/articles/claude-code-usage-limit-token-estimate
  作者在 Claude Team 方案 Premium 席位以 Claude Code CLI（opus、xhigh，實測 claude-opus-4-7）連續 10 次送出同一個大型固定提示（2026-05-05 14:23–14:31 JST）：5 小時表由 7% 升到 62%（+55 點），換算 5 小時額度 100% 約 644 萬 token、約 62 美元（每 1% 約 0.62 美元）；7 天表只動 +4 點，作者視為很粗的參考值，100% 約 850 美元（範圍 680–1,134），每 1% 約 8.5 美元；快取寫入（1 小時價）約占 API 等值的 98%。作者在文首註明：文章完成隔天 Anthropic 宣布大幅提高限額，這些是提高之前的數字。
- tkurume。〈Claude Max 週間上限まで残り4%。「別枠」の Fable 5 はどこまで動くのか実測してみた〉。Qiita，2026-07-31。 https://qiita.com/tkurume/items/a3a5af52443aac0ccbaf
  作者在個人 Max 5x 方案、只用 Fable 5，約每 7 分鐘記錄 5 小時、全部模型、Fable 三個表，由讀數增量比推得池子大小比約為 5 小時：Fable：全部模型＝1:5:10（Fable 分項約為全部模型的一半，與 Anthropic 公布的「at 50% of limits」一致），並確認「全部模型」到 100% 時 Fable 5 也會停（看似繼續是自動轉用額外用量點數）。
- Fei2411/real-api-pricing 第 226 號 issue（由 cursor[bot] 開立）。〈[2026-11-09] Claude Pro × Fable 5.2：9.75M tokens = 5h +49pp、周 +7pp（本机单账号，未采用）〉。GitHub，2026-11-10。 https://github.com/Fei2411/real-api-pricing/issues/226
  維護者本機一個 Claude Pro 帳號、2026-11-09（PT）一段全部由 Fable 5.2 做的 Claude Code 工作（44 次呼叫、約 975 萬 token、API 牌價約 8.81 美元）：5 小時表 15%→64%（+49 點）、週表 22%→29%（+7 點），反推週池約 125.87 美元（整數讀數，約 117–147）；作者標示僅記錄、未採用。
- Toy。〈Claude 订阅额度计算公式实测：缓存读取并非免费，一次 cache miss 可耗 20% 限额〉。80AJ，2026-10-15。 https://www.80aj.com/2026/10/15/claude-cache-cost-20-percent/
  80aj 編輯改寫 Linux.do 用戶的實測：在一次額度重置並結合週限刷新後，得出各計費係數 input 1、cache write 1.25、cache read 0.025、output 5（Q = I + 1.25W + 0.025R + 5O），據此重算 Claude Pro 的 5 小時額度約 9.6 美元（尖峰）／13.6 美元（離峰）、週額度約 165 美元；未列模型、期間與實驗細節。
- Toy。〈Claude Max 20x性价比遭质疑：价格翻倍，周额度仅提升约35%〉。80AJ，2026-10-15。 https://www.80aj.com/2026/10/15/claude-max-value-debate/
  80aj 編輯改寫 Linux.do 發文：一位用戶先後用 Max 5x（100 美元／月）與 Max 20x（200 美元／月），借第三方工具 CC Switch 記錄，得 Max 5x 週額度約 1,100 美元、Max 20x 約 1,500 美元（20x 只比 5x 多約 36%，標題寫約 35%）；未寫模型、期間與方法細節。
- 曾左。〈每月 20 美元的 Claude Pro 会员到底能消耗多少 Token 及额度〉。博客园，2026-06-17（2026-07-24 更新；更新日取自頁面結構化資料）。 https://www.cnblogs.com/zengzuo613/p/20595884
  作者在 Claude Pro（月費 20 美元）於 2026-06-03 15:00–19:00 的一個 5 小時視窗，用 ccusage 記錄 token 與美元：5 小時額度 0%→100% 共用 3,925 萬 token、18.36 美元，同時占用 10 點週額度（24%→34%），據此推測 5 小時額度最多約 18 美元／4,000 萬 token（Sonnet、High effort 的前提）、一週約 10 個視窗（180 美元）。
- Stan Wu 吳信典。〈Claude Code 實測：Sonnet 竟比 Haiku 省 44% token——貴的反而更划算〉。2026-04-17。 https://blog.stanwu.org/posts/sonnet-vs-haiku-token-cost/
  作者在 Claude Code 以 /context 指令比較 Sonnet 4.6 與 Haiku 4.5：新工作階段的固定負擔（Haiku 的系統工具定義是 Sonnet 的 2.4 倍，每則訊息多帶約 13.2k token），以及同一個 SSH＋bash 診斷任務的總 context 消耗（Sonnet 19.1k、Haiku 34.1k）；沒有讀任何額度表。
- 지피터스_강지인。〈클로드 사용량 빨리 닳는 이유: 기분 탓 아님! 9월 13일에 50% 프로모션이 끝났어요〉。지피터스 GPTers，2026-09-17（頁面可見處只寫「2 months ago」，日期取自頁面中繼資料，與內文寫的確認日一致）。 https://www.gpters.org/news/post/reasons-why-claude-usageconsumption-9ttQ3gU9wQOeOZB
  作者在個人 Max 20x 帳號於 2026-09-17 讀 /usage 畫面：全部模型週表 43%、Fable 週表 67%（週一 03:00 重置），並以 191 個本機 JSONL 檔統計各週請求數與 token（快取讀取占近 99%），說明 9 月 13 日 50% 加量活動結束後週額度比 8 月低 16.7%（1.25÷1.5）；作者明說不把 token 換成額度百分比，因為快取讀取的權重未公開。
- Александр Майорский（頁面署名顯示為帳號 WebProd）。〈Claude Max против ChatGPT Pro: почему «в 5 раз выгоднее» — не совсем то, чем кажется〉。Хабр，2026-10-07（2026-10-09 更新；頁面可見「7 окт」沒有年份，年份與更新日取自頁面結構化資料）。 https://habr.com/ru/articles/1091556/
  作者引用 SemiAnalysis 的訂閱 API 等值數字（Claude Max 20x 約 11,726 美元、ChatGPT Pro 200 約 2,084 美元），再結合 Artificial Analysis 的基準資料估計各模型完成同一任務耗用的 token（Opus 5.5 輸出約為 GPT-6.1 Sol 的 3.13 倍、每任務成本約 8.3 倍），主張「5 倍划算」要把 token 效率算進去；本人沒有量訂閱額度。

## 搜尋範圍與限制

- 搜尋日期：2026-11-18。範圍：英文部落格與論壇、GitHub issue、日文、簡體中文、繁體中文、韓文，以及俄文等其他語言與學術論文。
- 沒有涵蓋到 Reddit、X、YouTube、Bilibili、知乎、Discord 上的第一手內容，那些地方很可能還有這裡沒看到的實測。
- 有些內容在付費牆後或沒有讀到原文（例如 [1] 的後段、[14] 轉述的原始貼文；arXiv 論文 Kwartler 等只讀了摘要頁，全文未讀），這裡只引用讀得到的部分。[4]、[6] 是會定期更新的頁面，這裡引用的是 2026-11-18 當天看到的內容。
- 如果你做過類似的量測而沒有被列進來，或這裡對你的研究描述有誤，請開 issue，會補上並更正。

## 出處

- [1] Andrew Megalaa, Max Kan, and Dylan Patel。〈Anthropic Subscriptions Offer 5x+ More Value Than OpenAI〉。SemiAnalysis，2026-10-05。 https://newsletter.semianalysis.com/p/anthropic-subscriptions-offer-5x
- [2] Joe。〈實測 Claude $200 訂閱：全跑 Opus 5.5 額度等值 1.17 萬美元 API 費(差58倍)〉。動區動趨 BlockTempo，2026-10-07。（[1] 的中文轉述） https://www.blocktempo.com/anthropic-openai-subscription-limits-semianalysis-opus-sol-api-value-halved/
- [3] tksfjt1024。〈Claude Code の使用量はどう数えられているのか ── Max 20x で実測した重みは API 料金表と違った〉。Zenn，2026-10-01。 https://zenn.dev/tksfjt1024/articles/25c0ab111c277c
- [4] Atlas ADE。〈Plan usage burn data〉。2026-10-29 更新（頁面寫的最後對照即時讀數日期，中繼資料相同；頁面內文另提到之後的日期，所以之後還有改過；頁面沒有標發表日期）。（頁面沒有署名） https://atlas-ade.dev/docs/plan-usage-burn-data/
- [5] Dillip Chowdary。〈What a week of Claude Code holds: the usage meter, per token type〉。Tech Bytes，2026-11-14。（[4] 的轉述） https://techbytes.app/posts/what-a-week-of-claude-code-holds-the-usage-meter
- [6] Wokey。〈Claude Usage Limits (November 2026): Pro ≈ 17.2M Tokens a Week, Measured〉。2026-11-16 更新（頁面沒有標發表日期）。（頁面沒有署名） https://wokey.ai/claude-usage-limits
  - 同一來源的另一頁：Wokey。〈Claude Max & Ultra Limits, Measured: Max 5x vs 20x Weekly (Ultra ≈ $1,576 of API Use)〉。2026-11-16 更新（頁面沒有標發表日期）。（頁面沒有署名） https://wokey.ai/claude-max-limits
- [7] Kanttz。〈[数据 / Data] Anthropic · Claude Max 20x · Opus 5.5 (two full weekly windows and a 36-window 5h fit)〉。GitHub，2026-10-02（2026-10-15 更新）。 https://github.com/Fei2411/real-api-pricing/issues/96
- [8] zhuran24。〈Weekly quota pool: one −14% step between windows, then partial rebound (Max 20x) — earlier −43.5% figure retracted〉。GitHub，2026-08-20（2026-10-28 更新；該議題已被 stale bot 關閉為 not planned，這是關閉日）。 https://github.com/anthropics/claude-code/issues/88352
- [9] 中島健雄。〈Claude Max 20xの週次上限は5xの何倍なのか実測してみた〉。DevelopersIO，2026-08-20。 https://dev.classmethod.jp/articles/claude-max-20x-weekly-limit-not-4x/
- [10] bestagent.dev。〈Fable 5.2 对 Opus 5.5：一个任务 $2 对 $1，5 小时限额 6 点对 1–2 点〉。2026-11-12。（頁面沒有署名） https://bestagent.dev/zh/fable-5-2-vs-opus-5-5-claude-code-limits-2026/
- [11] qvarker。〈[BUG] Headless `claude -p` (entrypoint `sdk-cli`) uses ~1.8× more of the 5-hour window per token than interactive `cli`, same workload〉。GitHub，2026-09-25（2026-11-16 更新）。 https://github.com/anthropics/claude-code/issues/97074
  - kler。〈kler/claude-usage-ab〉（該庫沒有說明文字）。GitHub，2026-10-17（頁面沒顯示日期，取自 GitHub API 的建立時間）。 https://github.com/kler/claude-usage-ab
- [12] NTRYourWaifu。〈[數據] Anthropic · Claude Pro $20 · Opus 5.5〉。GitHub，2026-09-27（2026-10-05 更新，是關閉日）。 https://github.com/Fei2411/real-api-pricing/issues/52
- [13] AI Dive。〈Your Claude Code subagents never touch Haiku 5.5. I measured what routing them there gives back.〉。DEV Community，2026-10-12（頁面可見處只寫「Oct 12」，年份取自頁面結構化資料）。 https://dev.to/aidiveyt/your-claude-code-subagents-never-touch-haiku-55-i-measured-what-routing-them-there-gives-back-53h2
- [14] Pasquale Pillitteri。〈Claude Ultra gives $15,000 of Opus 5.5 but only $3,000 of Fable 5.2〉。pasqualepillitteri.it，2026-11-10（只經網頁摘要工具讀取，標題與日期未對照原始網頁）。（轉述他人在 X 的貼文） https://pasqualepillitteri.it/en/news/31652/claude-ultra-opus-5-5-fable-5-2-usage-limits
