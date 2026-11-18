export const meta = {
  name: 'quota-cell',
  description: '額度換算實驗：單一模型、單一工作型態（讀檔／長輸出／反覆讀快取）的受控工作量，結束後等讀數穩定',
  phases: [
    { title: 'Burn', detail: '同一模型的代理跑指定型態的標準工作' },
    { title: 'Settle', detail: '等額度讀數連續數分鐘不再變動' },
  ],
}
const cfg = args || {}
const kind = cfg.kind || 'digest'
const model = cfg.model || 'sonnet'
const effort = cfg.effort || 'xhigh'
const agents = cfg.agents || 2
const lanes = cfg.lanes || 1
const filesPer = cfg.filesPer || 12
const batch = cfg.batch || 4
const corpusSize = cfg.corpusSize || 240
const start = cfg.start || 0
const words = cfg.words || 300
const settle = cfg.settle || 0
const parts = cfg.parts || 10
const wordsPer = cfg.wordsPer || 9000
const calls = cfg.calls || 150
// gap＝同一線上每個代理之間停幾秒（讓單價高的模型慢慢燒，讀數取樣才跟得上）；停頓由最小的模型執行 quota_lab.py pace
const gap = cfg.gap || 0
// data＝這次實驗的資料夾（選填，絕對路徑）。有給時，交給代理的每條 quota_lab.py 命令前面都帶 QUOTA_LAB_DATA
const data = cfg.data || ''
if (data && !String(data).startsWith('/')) throw new Error('args.data 必須是絕對路徑（結尾不加斜線）')
const tag = cfg.tag || (kind + '-' + model)
// root＝本庫在你機器上的絕對路徑（結尾要有斜線）。必填：腳本在流程環境裡無法得知自己的位置
if (!cfg.root || !String(cfg.root).endsWith('/')) throw new Error('args.root 必填：本庫的絕對路徑，結尾加斜線')
const root = cfg.root
const q = s => "'" + String(s).replace(/'/g, "'\\''") + "'"
const lab = (data ? `QUOTA_LAB_DATA=${q(data)} ` : '') + `python3 ${q(root + 'bin/quota_lab.py')}`
const base = root + 'corpus/'
const pad = n => String(n).padStart(5, '0')
const head = i => `Controlled usage-measurement job ${tag} #${i}. Follow these steps exactly and do nothing else: no subagents, no web access, no skills, no notes, no reading or writing of any file not named here.`

function pathsFor(i) {
  const paths = []
  for (let k = 0; k < filesPer; k++) paths.push(base + 'f_' + pad((start + i * filesPer + k) % corpusSize) + '.txt')
  return paths
}
function readSteps(paths) {
  const rounds = []
  for (let s = 0; s < paths.length; s += batch) rounds.push(paths.slice(s, s + batch))
  const lines = [`Step A. Read the files below with the Read tool, passing allow_large: true on every call. Work in ${rounds.length} rounds. In each round, issue all of that round's Read calls together in one single message (parallel tool calls). Do not start a round before the previous round's results are back.`]
  rounds.forEach((r, idx) => { lines.push(`Round ${idx + 1}:`); r.forEach(p => lines.push('- ' + p)) })
  return lines
}
function digestPrompt(i) {
  const paths = pathsFor(i)
  return [head(i), '', ...readSteps(paths), '',
    'Step B. After the last round, run this single Bash command exactly once:',
    'wc -c ' + paths.map(q).join(' ') + ' | tail -1', '',
    `Step C. Then answer in about ${words} English words describing statistical features you noticed in the text (typical word lengths, any repeated words, line lengths). End with a final line exactly in this form: DONE ${tag} #${i} <total bytes reported by wc>`,
  ].join('\n')
}
function writePrompt(i) {
  const dir = root + 'scratch/' + tag + '/'
  return [head(i), '',
    `You are writing an original serialized novel. Premise seed: ${tag}-${i}; invent the setting, characters and plot yourself. Write ${parts} chapters. For each chapter k from 1 to ${parts}, in order:`,
    `1. Call the Write tool once to create the file ${q(dir + 'a' + i + '_ch<k>.md')} (replace <k> with the chapter number) containing the full text of chapter k. Each chapter must be at least ${wordsPer} words of continuous original English prose: no outlines, no summaries, no placeholders, no repeated passages.`,
    '2. Write no commentary between chapters; go straight to the next Write call. Exactly one Write call per message; never issue two Write calls in the same message.', '',
    `After chapter ${parts} is written, reply with exactly: DONE ${tag} #${i}`,
  ].join('\n')
}
function rereadPrompt(i) {
  const paths = pathsFor(i)
  return [head(i), '', ...readSteps(paths), '',
    `Step B. Then run the following Bash command ${calls} times, strictly one call per message and strictly in sequence, replacing <k> with 1, 2, 3, ... up to ${calls}. Never put two tool calls in one message, never skip a number, never stop early, and write no text between calls:`,
    `${lab} pace 1 --k <k>`, '',
    `Step C. After call number ${calls} has returned, reply with exactly: DONE ${tag} #${i}`,
  ].join('\n')
}
const build = kind === 'write' ? writePrompt : kind === 'reread' ? rereadPrompt : digestPrompt
// 驗收：回覆的最後一行必須是指定的結尾標記（讀檔型還要帶 wc 回報的位元組總數）
const esc = x => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
function passed(r, i) {
  if (!r) return false
  const last = String(r).trim().split('\n').pop().trim()
  const pat = kind === 'digest'
    ? new RegExp('^DONE ' + esc(tag) + ' #' + i + ' \\d+$')
    : new RegExp('^DONE ' + esc(tag) + ' #' + i + '$')
  return pat.test(last)
}

phase('Burn')
log(`${tag}: kind=${kind} model=${model} effort=${effort} agents=${agents} lanes=${lanes}`)
const laneResults = await parallel(Array.from({ length: lanes }, (_, lane) => async () => {
  const out = []
  for (let i = lane; i < agents; i += lanes) {
    const r = await agent(build(i), { label: `${tag}-${i}`, phase: 'Burn', model, effort })
    out.push({ i, ok: passed(r, i) })
    if (gap > 0 && i + lanes < agents) {
      await agent(
        `Run this single Bash command exactly once with the Bash tool, passing timeout 600000 (milliseconds). Then reply with only the command's stdout, nothing else. Do nothing else.\n${lab} pace ${gap} --k gap`,
        { label: `${tag}-gap-${i}`, phase: 'Burn', model: 'haiku', effort: 'low' })
    }
  }
  return out
}))
const flat = laneResults.filter(Boolean).flat()
let settled = null
if (settle > 0) {
  phase('Settle')
  settled = await agent(
    `Run this single Bash command exactly once with the Bash tool, passing timeout 600000 (milliseconds). Then reply with only the command's stdout, nothing else. Do nothing else.\n${lab} wait --stable ${settle} --min ${settle} --timeout 570`,
    { label: `${tag}-settle`, phase: 'Settle', model: 'haiku', effort: 'low' })
}
// settle_ok：true＝讀數達到穩定條件才收工；false＝等到逾時仍未穩定（這一格的結束讀數要另外確認）；null＝沒要求等
const settleOk = settled ? /"result":\s*"stable"/.test(String(settled)) : null
if (settleOk === false) log(`警告：${tag} 結束時讀數未達穩定條件：${String(settled).slice(0, 160)}`)
const failedIds = flat.filter(x => !x.ok).map(x => x.i)
if (failedIds.length) log(`警告：${tag} 有 ${failedIds.length} 個代理的結尾標記不符：#${failedIds.join(', #')}`)
return { tag, kind, model, effort, done: flat.filter(x => x.ok).length, failed: failedIds.length, failed_ids: failedIds, settle_ok: settleOk, settled: settled ? String(settled).slice(0, 300) : null }
