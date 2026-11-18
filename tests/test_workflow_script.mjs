// 流程腳本（bin/quota_cell_workflow.js、bin/quota_campaign_workflow.js）的離線測試：用假的 agent()／parallel()／workflow()
// 把腳本跑一遍，檢查提示詞內容、結尾標記驗收、讀數穩定旗標、root 必填、data 轉送、記事件順序與 mark 回覆檢查。不呼叫任何模型。
// 用法：node tests/test_workflow_script.mjs
import { readFileSync, mkdtempSync, writeFileSync, chmodSync, rmSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { tmpdir } from 'node:os'
import { execFileSync } from 'node:child_process'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, '..', 'bin', 'quota_cell_workflow.js'), 'utf8').replace('export const meta', 'const meta')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const body = new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', src)

async function run(args, reply, settleReply = '{"result": "stable", "waited_s": 300}') {
  const logs = []
  const prompts = []
  const settlePrompts = []
  const calls = []
  const agent = async (prompt, opts) => {
    calls.push(opts)
    if (opts.phase === 'Settle') { settlePrompts.push(prompt); return settleReply }
    prompts.push(prompt)
    return reply(prompt)
  }
  const parallel = async thunks => Promise.all(thunks.map(t => t().catch(() => null)))
  const out = await body(args, agent, parallel, () => {}, m => logs.push(m))
  return { out, logs, prompts, settlePrompts, calls }
}
const idOf = p => (p.match(/#(\d+)\./) || [])[1]
let failed = 0
const check = (name, cond) => { console.log((cond ? 'ok   ' : 'FAIL ') + name); if (!cond) failed++ }

// 用假的直譯器跑一行 shell 命令，回傳每個參數一行（含 ENV: 開頭的一行），用來確認引號真的讓每個值只是一個參數。
function shellArgs(line, tool = 'python3') {
  const dir = mkdtempSync(join(tmpdir(), 'wf-quote-'))
  try {
    const fake = join(dir, tool)
    const body = tool === 'python3'
      ? '#!/bin/sh\nprintf \'ENV:%s\\n\' "${QUOTA_LAB_DATA-}"\nfor a in "$@"; do printf \'ARG:%s\\n\' "$a"; done\n'
      : '#!/bin/sh\nfor a in "$@"; do printf \'ARG:%s\\n\' "$a"; done\n'
    writeFileSync(fake, body)
    chmodSync(fake, 0o755)
    const out = execFileSync('/bin/bash', ['-c', line], {
      encoding: 'utf8',
      env: { ...process.env, PATH: dir + ':' + process.env.PATH },
    })
    return out.trim().split('\n')
  } catch (e) {
    return null
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}
const cmdOf = p => p.split('\n')[1]

// 1. 讀檔型：檔案清單、份數、結尾標記帶位元組數
{
  const r = await run({ kind: 'digest', model: 'sonnet', agents: 3, lanes: 1, filesPer: 12, batch: 4, tag: 'd1', root: '/tmp/repo/' },
    p => `stats\nDONE d1 #${idOf(p)} 720000`)
  check('digest: 3 個代理都通過', r.out.done === 3 && r.out.failed === 0)
  check('digest: 每份 12 個檔、路徑在 root/corpus 下', r.prompts.every(p => (p.match(/\/tmp\/repo\/corpus\/f_\d{5}\.txt/g) || []).length === 24))
  check('digest: 沒要求等穩定時 settle_ok 為 null', r.out.settle_ok === null)
  check('digest: 模型與力度照參數傳給代理', r.calls.every(c => c.model === 'sonnet' && c.effort === 'xhigh'))
}
// 2. 寫長文型：含糊回覆不算通過；編號不符不算通過
{
  const r = await run({ kind: 'write', model: 'opus', agents: 3, lanes: 3, parts: 5, wordsPer: 9000, tag: 'w1', root: '/tmp/repo/', settle: 300 },
    p => (idOf(p) === '1' ? 'It is mostly DONE w1' : `DONE w1 #${idOf(p) === '2' ? '12' : idOf(p)}`))
  check('write: 只有 #0 通過', r.out.done === 1 && JSON.stringify(r.out.failed_ids) === '[1,2]')
  check('write: 輸出檔路徑在 root/scratch/<tag> 下', r.prompts.every(p => p.includes('/tmp/repo/scratch/w1/')))
  check('write: 讀數穩定時 settle_ok 為 true', r.out.settle_ok === true)
}
// 3. 等穩定逾時要被標出來
{
  const r = await run({ kind: 'reread', model: 'sonnet', agents: 1, lanes: 1, calls: 5, tag: 'r1', root: '/tmp/repo/', settle: 300 },
    p => `DONE r1 #${idOf(p)}`, '{"result": "timeout", "waited_s": 570}')
  check('reread: 逾時時 settle_ok 為 false 且有警告', r.out.settle_ok === false && r.logs.some(l => l.includes('未達穩定')))
  check('reread: 節拍器命令指向 root/bin/quota_lab.py', r.prompts[0].includes("python3 '/tmp/repo/bin/quota_lab.py' pace 1 --k <k>"))
}
// 3b. gap：同一線上兩個代理之間插入停頓（最後一個之後不停）
{
  const r = await run({ kind: 'digest', model: 'fable', agents: 3, lanes: 1, filesPer: 12, batch: 4, gap: 200, tag: 'g1', root: '/tmp/repo/' },
    p => (p.includes('pace 200') ? 'ok gap' : `x\nDONE g1 #${idOf(p)} 1`))
  const gaps = r.calls.filter(c => String(c.label).includes('-gap-'))
  check('gap: 3 個代理之間有 2 次停頓，且用最小模型', gaps.length === 2 && gaps.every(c => c.model === 'haiku'))
  check('gap: 工作代理仍全部通過', r.out.done === 3)
}
// 4. root 必填
{
  let threw = false
  try { await run({ kind: 'digest' }, () => '') } catch { threw = true }
  check('沒給 root 會直接報錯', threw)
}

// 5. data：交給代理的每條 quota_lab.py 命令都要帶 QUOTA_LAB_DATA（cell 腳本），等穩定命令也要帶
{
  const r = await run({ kind: 'reread', model: 'sonnet', agents: 2, lanes: 1, calls: 5, gap: 30, tag: 'e1', root: '/tmp/repo/', data: '/tmp/labdata', settle: 300 },
    p => (p.includes('pace 30') ? 'ok gap' : `DONE e1 #${idOf(p)}`))
  const cmds = r.prompts.join('\n').split('\n').filter(l => l.includes('quota_lab.py'))
  check('cell: 有給 data 時每條命令都先設 QUOTA_LAB_DATA', cmds.length >= 3 && cmds.every(l => l.startsWith("QUOTA_LAB_DATA='/tmp/labdata' ")))
  check('cell: 等穩定的命令也帶 QUOTA_LAB_DATA', r.settlePrompts.length === 1 && cmdOf(r.settlePrompts[0]).startsWith("QUOTA_LAB_DATA='/tmp/labdata' "))
  const r2 = await run({ kind: 'reread', model: 'sonnet', agents: 1, lanes: 1, calls: 5, tag: 'e2', root: '/tmp/repo/' },
    p => `DONE e2 #${idOf(p)}`)
  check('cell: 沒給 data 時命令與原本相同', r2.prompts[0].includes("python3 '/tmp/repo/bin/quota_lab.py' pace 1 --k <k>"))
}

// 5b. 引號：tag、data 有單引號時，每個值仍是一個參數
{
  const r = await run({ kind: 'reread', model: 'sonnet', agents: 1, lanes: 1, calls: 5, tag: "r'1", root: '/tmp/repo/', data: "/tmp/lab'data", settle: 300 },
    p => `DONE r'1 #${idOf(p)}`)
  const line = r.prompts[0].split('\n').find(l => l.includes('pace 1 --k <k>')).replace('<k>', '1')
  const got = shellArgs(line)
  check('cell: data 有單引號時命令不中斷，環境變數是完整的一個值',
    got !== null && got[0] === "ENV:/tmp/lab'data")
  check('cell: 腳本路徑、pace、--k 各自只是一個參數',
    got !== null && got.slice(1).join('|') === "ARG:/tmp/repo/bin/quota_lab.py|ARG:pace|ARG:1|ARG:--k|ARG:1")
}

// 5c. root 有空白時，命令不能被拆開（含 wc 的每個檔案參數）
{
  const r = await run({ kind: 'digest', model: 'sonnet', agents: 1, lanes: 1, filesPer: 12, batch: 4, tag: 'd2', root: '/tmp/re po/' },
    p => `DONE d2 #${idOf(p)} 720000`)
  const wcLine = r.prompts[0].split('\n').find(l => l.startsWith('wc -c ')).split(' | tail')[0]
  const got = shellArgs(wcLine, 'wc')
  check('root 有空白時，wc 的每個檔案路徑仍是一個參數',
    got !== null && got.length === 13 && got[0] === 'ARG:-c' && got.slice(1).every(l => l.startsWith("ARG:/tmp/re po/corpus/f_")))
  const r2 = await run({ kind: 'reread', model: 'sonnet', agents: 1, lanes: 1, calls: 5, tag: 'r2', root: '/tmp/re po/' },
    p => `DONE r2 #${idOf(p)}`)
  const line = r2.prompts[0].split('\n').find(l => l.includes('pace 1 --k <k>')).replace('<k>', '1')
  const got2 = shellArgs(line)
  check('root 有空白時，quota_lab.py 的路徑仍是一個參數',
    got2 !== null && got2[1] === 'ARG:/tmp/re po/bin/quota_lab.py')
}

// 6. 活動腳本：記事件順序、備註內容、data 轉送、stopOnFail、mark 回覆檢查、失敗的 catch
const campaignSrc = readFileSync(join(here, '..', 'bin', 'quota_campaign_workflow.js'), 'utf8').replace('export const meta', 'const meta')
const campaignBody = new AsyncFunction('args', 'agent', 'workflow', 'phase', 'log', campaignSrc)
const noteOf = p => p.slice(p.indexOf('--note ') + 7).trim()
const MARK_OK = '{"label":"ok"}'

async function runCampaign(args, cellResult = () => ({ done: 2, failed: 0, failed_ids: [], settle_ok: true }), opts = {}) {
  const seq = []
  const prompts = []
  const cells = []
  const logs = []
  const agent = async (prompt, o) => {
    const label = String(o.label)
    seq.push(label)
    prompts.push(prompt)
    if (label.endsWith(':mark-start')) return typeof opts.startReply === 'function' ? opts.startReply(label) : (opts.startReply ?? MARK_OK)
    if (label.endsWith(':mark-end')) return typeof opts.endReply === 'function' ? opts.endReply(label) : (opts.endReply ?? MARK_OK)
    return 'ok'
  }
  const workflow = async (spec, cellArgs) => {
    seq.push(cellArgs.tag)
    cells.push(cellArgs)
    if (opts.cellThrows) throw new Error('boom')
    return cellResult(cellArgs)
  }
  const out = await campaignBody(args, agent, workflow, () => {}, m => logs.push(m))
  return { out, seq, prompts, cells, logs }
}

{
  const r = await runCampaign({ root: '/tmp/repo/', cells: [{ tag: 'c1', kind: 'digest', agents: 1 }, { tag: 'c2', kind: 'digest', agents: 1 }] })
  check('campaign: 兩格都是 開始事件 → 工作格 → 結束事件',
    r.seq.join(',') === 'c1:mark-start,c1,c1:mark-end,c2:mark-start,c2,c2:mark-end')
  const start = noteOf(r.prompts[0])
  check('campaign: 開始備註有 args= 且沒有 root 路徑', start.includes('args=') && !start.includes('/tmp/repo'))
  check('campaign: 沒寫說明時備註只有 args=', start.startsWith("'args="))
  const human = await runCampaign({ root: '/tmp/repo/', cells: [{ tag: 'c1', note: '第一格說明' }] })
  check('campaign: 有人寫的說明時放在 args= 前面', noteOf(human.prompts[0]).includes("'第一格說明｜args="))
  check('campaign: 結束備註有 failed_ids=', r.prompts[1].includes('failed_ids='))
}

{
  const r = await runCampaign({ root: '/tmp/repo/', data: '/tmp/labdata', cells: [{ tag: 'c1' }] })
  const cmds = r.prompts.join('\n').split('\n').filter(l => l.includes('quota_lab.py'))
  check('campaign: 有給 data 時記事件命令先設 QUOTA_LAB_DATA', cmds.length === 2 && cmds.every(l => l.startsWith("QUOTA_LAB_DATA='/tmp/labdata' ")))
  check('campaign: data 轉給工作格，且不寫進開始備註', r.cells[0].data === '/tmp/labdata' && !noteOf(r.prompts[0]).includes('labdata'))
}

// 6b. 每格自帶 data：蓋過頂層、結尾斜線去掉一個；不合法就拒絕
{
  const r = await runCampaign({ root: '/tmp/repo/', data: '/tmp/top', cells: [{ tag: 'c1' }, { tag: 'c2', data: '/tmp/per/' }] })
  check('campaign: 沒自帶 data 的格用頂層 data', r.cells[0].data === '/tmp/top')
  check('campaign: 自帶 data 的格蓋過頂層，且去掉結尾斜線', r.cells[1].data === '/tmp/per')
  const lines = r.prompts.join('\n').split('\n').filter(l => l.includes('quota_lab.py'))
  check('campaign: 每一格記事件用自己那個 data',
    lines[0].startsWith("QUOTA_LAB_DATA='/tmp/top' ") && lines[1].startsWith("QUOTA_LAB_DATA='/tmp/top' ") &&
    lines[2].startsWith("QUOTA_LAB_DATA='/tmp/per' ") && lines[3].startsWith("QUOTA_LAB_DATA='/tmp/per' "))
  for (const bad of ['/tmp/repo/data', '/tmp/repo/data/', 'rel/data']) {
    let threw = false
    try { await runCampaign({ root: '/tmp/repo/', data: bad, cells: [{ tag: 'c1' }] }) } catch { threw = true }
    check(`campaign: data=${bad} 會直接報錯`, threw)
  }
  let threwCell = false
  try { await runCampaign({ root: '/tmp/repo/', cells: [{ tag: 'c1', data: '/tmp/repo/data/' }] }) } catch { threwCell = true }
  check('campaign: 某一格自帶 repo 的 data 也會報錯', threwCell)
}

// 6c. mark 開始事件沒記成功：不跑工作格，整輪停止
{
  const r = await runCampaign({ root: '/tmp/repo/', cells: [{ tag: 'c1' }, { tag: 'c2' }] }, undefined, { startReply: '' })
  check('campaign: 開始 mark 沒有 JSON 回覆時，工作格不跑', r.cells.length === 0)
  check('campaign: 開始 mark 失敗記成 mark_failed 並帶回覆', r.out.results.length === 1 && r.out.results[0].error === 'mark_failed' && r.out.results[0].reply === '')
  check('campaign: 開始 mark 失敗就整輪停止（第二格也不記事件）', r.seq.join(',') === 'c1:mark-start')
  check('campaign: 開始 mark 失敗有大聲記一行', r.logs.some(l => l.includes('停止') && l.includes('c1')))
}

// 6d. 結束 mark 沒記成功：工作格結果仍在，但標 mark_end_failed
{
  const r = await runCampaign({ root: '/tmp/repo/', cells: [{ tag: 'c1' }] }, undefined, { endReply: 'not json' })
  check('campaign: 結束 mark 沒有 JSON 回覆時，工作格結果仍留著', r.cells.length === 1 && r.out.results.length === 1)
  check('campaign: 結束 mark 失敗標 mark_end_failed 並有警告',
    r.out.results[0].mark_end_failed === true && r.logs.some(l => l.includes('結束事件') && l.includes('c1')))
}

// 6e. 工作格丟例外：catch 要留下錯誤，結束備註要寫 error=（不能當成成功）
{
  const r = await runCampaign({ root: '/tmp/repo/', cells: [{ tag: 'c1' }] }, undefined, { cellThrows: true })
  check('campaign: 工作格丟例外時記下錯誤', r.out.results.length === 1 && String(r.out.results[0].error).includes('boom'))
  const end = noteOf(r.prompts[1])
  check('campaign: 失敗的結束備註寫 error=Error: boom，不寫 done=', end.includes('error=Error: boom') && !end.includes('done='))
}

// 6f. 引號：tag、note、data 有單引號時，每個值仍是一個參數
{
  const tag = "c'1"
  const note = `it's "fine"`
  const data = "/tmp/lab'data"
  const r = await runCampaign({ root: '/tmp/repo/', data, cells: [{ tag, note }] })
  const line = cmdOf(r.prompts[0])
  const got = shellArgs(line)
  check('campaign: data 有單引號時環境變數是完整的一個值', got !== null && got[0] === `ENV:${data}`)
  check('campaign: tag 與 note 有單引號時各自只是一個參數',
    got !== null && got[1] === 'ARG:/tmp/repo/bin/quota_lab.py' && got[2] === 'ARG:mark' &&
    got[3] === `ARG:${tag}-start` && got[4] === 'ARG:--note' && got[5].startsWith(`ARG:${note}｜args=`))
}

{
  const r = await runCampaign({ root: '/tmp/repo/', stopOnFail: true, cells: [{ tag: 'c1' }, { tag: 'c2' }] },
    c => (c.tag === 'c1' ? { done: 1, failed: 1, failed_ids: [0], settle_ok: true } : { done: 1, failed: 0, failed_ids: [], settle_ok: true }))
  check('campaign: stopOnFail 在第一格失敗後就停', r.cells.length === 1 && r.out.results.length === 1)
}

if (failed) { console.error(`${failed} 項失敗`); process.exit(1) }
console.log('全部通過')
