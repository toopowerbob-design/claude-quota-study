export const meta = {
  name: 'quota-burn',
  description: '額度換算實驗：單一模型跑標準化讀檔工作量（固定砝碼檔案），結束後等讀數穩定',
  phases: [
    { title: 'Burn', detail: '同一模型的代理依序讀固定檔案、寫短摘要' },
    { title: 'Settle', detail: '等額度讀數連續數分鐘不再變動' },
  ],
}
const cfg = args || {}
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
const tag = cfg.tag || ('burn-' + model)
// root＝本庫在你機器上的絕對路徑（結尾要有斜線）。必填：腳本在流程環境裡無法得知自己的位置
if (!cfg.root || !String(cfg.root).endsWith('/')) throw new Error('args.root 必填：本庫的絕對路徑，結尾加斜線')
const root = cfg.root
const base = root + 'corpus/'
const pad = n => String(n).padStart(5, '0')

function buildPrompt(i) {
  const paths = []
  for (let k = 0; k < filesPer; k++) paths.push(base + 'f_' + pad((start + i * filesPer + k) % corpusSize) + '.txt')
  const rounds = []
  for (let s = 0; s < paths.length; s += batch) rounds.push(paths.slice(s, s + batch))
  const lines = []
  lines.push(`Controlled usage-measurement job ${tag} #${i}. Follow these steps exactly and do nothing else: no subagents, no other files, no file writes, no web access, no skills, no notes.`)
  lines.push('')
  lines.push(`Step A. Read the files below with the Read tool, passing allow_large: true on every call. Work in ${rounds.length} rounds. In each round, issue all of that round's Read calls together in one single message (parallel tool calls). Do not start a round before the previous round's results are back.`)
  rounds.forEach((r, idx) => {
    lines.push(`Round ${idx + 1}:`)
    r.forEach(p => lines.push('- ' + p))
  })
  lines.push('')
  lines.push('Step B. After the last round, run this single Bash command exactly once:')
  lines.push('wc -c ' + paths.join(' ') + ' | tail -1')
  lines.push('')
  lines.push(`Step C. Then answer in about ${words} English words describing statistical features you noticed in the text (typical word lengths, any repeated words, line lengths). End with a final line exactly in this form: DONE ${tag} #${i} <total bytes reported by wc>`)
  return lines.join('\n')
}

phase('Burn')
log(`${tag}: model=${model} effort=${effort} agents=${agents} lanes=${lanes} filesPer=${filesPer} batch=${batch}`)
const laneResults = await parallel(Array.from({ length: lanes }, (_, lane) => async () => {
  const out = []
  for (let i = lane; i < agents; i += lanes) {
    const r = await agent(buildPrompt(i), { label: `${tag}-${i}`, phase: 'Burn', model, effort })
    out.push({ i, ok: !!r && String(r).includes('DONE') })
  }
  return out
}))
const flat = laneResults.filter(Boolean).flat()
let settled = null
if (settle > 0) {
  phase('Settle')
  settled = await agent(
    `Run this single Bash command exactly once with the Bash tool, passing timeout 600000 (milliseconds). Then reply with only the command's stdout, nothing else. Do nothing else.\npython3 ${root}bin/quota_lab.py wait --stable ${settle} --min ${settle} --timeout 570`,
    { label: `${tag}-settle`, phase: 'Settle', model: 'haiku', effort: 'low' })
}
return { tag, model, effort, done: flat.filter(x => x.ok).length, failed: flat.filter(x => !x.ok).length, settled: settled ? String(settled).slice(0, 300) : null }
