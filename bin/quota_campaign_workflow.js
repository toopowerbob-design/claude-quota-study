export const meta = {
  name: 'quota-campaign',
  description: '額度換算實驗：依序跑多格受控工作量，每格前後自動記事件並等讀數穩定（主持對話全程不必發話）',
  phases: [
    { title: 'Campaign', detail: '逐格：記開始事件 → 跑工作格（含等讀數穩定）→ 記結束事件', model: 'haiku' },
  ],
}
// 參數：
//   root   本庫的絕對路徑，結尾加斜線（必填）
//   data   這次實驗的資料夾，絕對路徑（選填）；有給就轉給每一格，記事件的命令也會帶 QUOTA_LAB_DATA。
//          不能指向本庫的 data/（已發表的資料集）。
//   cells  陣列，每個元素是 quota_cell_workflow.js 的參數再加 note（人寫的說明，寫進開始事件的備註）。
//          每一格也可以自帶 data，蓋過上面的 data；記事件與工作格都用同一個值。
//   stopOnFail  true＝某一格有代理失敗就不再跑後面的格
// 記事件與等穩定都用最小的模型跑一條 shell 命令；它們的用量很小，但仍會記在帳本裡。
const cfg = args || {}
if (!cfg.root || !String(cfg.root).endsWith('/')) throw new Error('args.root 必填：本庫的絕對路徑，結尾加斜線')
if (!Array.isArray(cfg.cells) || cfg.cells.length === 0) throw new Error('args.cells 必填：至少一格')
const root = cfg.root
const q = s => "'" + String(s).replace(/'/g, "'\\''") + "'"
// 資料夾參數的規則：絕對路徑、不能是本庫的 data/；結尾的斜線去掉一個。
const norm = s => String(s).replace(/\/+$/, '')
function cleanData(value, where) {
  if (value === undefined || value === null || value === '') return ''
  const d = String(value)
  if (!d.startsWith('/')) throw new Error(`${where}.data 必須是絕對路徑（例如 /絕對路徑/實驗名）`)
  if (norm(d) === norm(root + 'data')) {
    throw new Error(`${where}.data 不能指向本庫的 data/（那是已發表的資料集），請用另一個資料夾`)
  }
  return d.endsWith('/') ? d.slice(0, -1) : d
}
const topData = cleanData(cfg.data, 'args')
const labFor = d => (d ? `QUOTA_LAB_DATA=${q(d)} ` : '') + `python3 ${q(root + 'bin/quota_lab.py')}`
const sh = (cmd, label) => agent(
  `Run this single Bash command exactly once with the Bash tool. Then reply with only the command's stdout, nothing else. Do nothing else.\n${cmd}`,
  { label, phase: 'Campaign', model: 'haiku', effort: 'low' })
// mark 成功時印一行 JSON（含 label），同名事件已記過時印含 skipped 的 JSON，拒絕時什麼都不印。
function markOk(reply) {
  const text = String(reply == null ? '' : reply)
  for (const line of text.split('\n')) {
    const s = line.trim()
    const a = s.indexOf('{')
    const b = s.lastIndexOf('}')
    if (a < 0 || b <= a) continue
    try {
      const o = JSON.parse(s.slice(a, b + 1))
      if (o && typeof o === 'object' && !Array.isArray(o) && ('label' in o || 'skipped' in o)) return o
    } catch (e) { /* 不是 JSON，換下一行 */ }
  }
  return null
}

const results = []
for (const c of cfg.cells) {
  const tag = c.tag
  if (!tag) throw new Error('每一格都要有 tag')
  const cellData = cleanData(c.data === undefined || c.data === '' ? topData : c.data, `cells[${tag}]`)
  const cellArgs = { ...c, root, settle: c.settle === undefined ? 300 : c.settle }
  if (cellData) cellArgs.data = cellData
  else delete cellArgs.data
  delete cellArgs.note
  // 開始事件的備註：人寫的說明（如果有）＋實際傳給工作格的參數（不含 root／data／note）
  const shown = { ...cellArgs }
  delete shown.root
  delete shown.data
  const startNote = (c.note ? c.note + '｜' : '') + 'args=' + JSON.stringify(shown)
  const lab = labFor(cellData)
  const startReply = await sh(`${lab} mark ${q(tag + '-start')} --note ${q(startNote)}`, `${tag}:mark-start`)
  if (!markOk(startReply)) {
    // 開始事件沒記成功，後面每一格都會同樣失敗：整輪停下，不燒這一格（也不管 stopOnFail）。
    const reply = String(startReply == null ? '' : startReply).slice(0, 200)
    results.push({ tag, error: 'mark_failed', reply })
    log(`停止：${tag} 的開始事件沒有記成功（mark 的回覆不是含 label 或 skipped 的 JSON）：${reply}`)
    break
  }
  let r = null
  try {
    r = await workflow({ scriptPath: root + 'bin/quota_cell_workflow.js' }, cellArgs)
  } catch (e) {
    r = { tag, error: String(e).slice(0, 200) }
  }
  const brief = r && !r.error
    ? `done=${r.done} failed=${r.failed} failed_ids=${JSON.stringify(r.failed_ids || [])} settle_ok=${r.settle_ok}`
    : `error=${r ? r.error : 'null'}`
  const endReply = await sh(`${lab} mark ${q(tag + '-end')} --note ${q(brief)}`, `${tag}:mark-end`)
  if (!markOk(endReply)) {
    log(`警告：${tag} 的結束事件沒有記成功（mark 的回覆不是含 label 或 skipped 的 JSON），這一格的結束時間不可靠：${String(endReply == null ? '' : endReply).slice(0, 200)}`)
    if (r && typeof r === 'object') r.mark_end_failed = true
  }
  results.push(r)
  log(`${tag}: ${brief}`)
  if (cfg.stopOnFail && r && (r.error || r.failed > 0)) { log(`停止：${tag} 有失敗，後面的格不跑`); break }
}
return { results }
