export const meta = {
  name: 'llm-social-sim',
  description: 'Multi-round social simulation: LLM (Haiku) people maintain a shared wiki; KB and code reality are deterministic Python',
  phases: [
    { title: 'Init', detail: 'create run state' },
    { title: 'Rounds', detail: 'engine prepares cards, Haiku people decide, engine applies' },
    { title: 'Finish', detail: 'apply last round and write result' },
  ],
}

const { policy, rounds, seed } = args
const DIR = '/home/user/poc/social'
const ENGINE_SCHEMA = {
  type: 'object',
  properties: {
    round: { type: 'number' },
    active: { type: 'number' },
    groups: {
      type: 'array',
      items: {
        type: 'object',
        properties: { path: { type: 'string' }, out: { type: 'string' }, effort: { type: 'string' } },
        required: ['path', 'out', 'effort'],
      },
    },
    prev: { type: 'object' },
  },
  required: ['round', 'groups'],
}
const ROW_SCHEMA = { type: 'object', properties: { round: { type: 'number' }, acc: { type: 'number' }, round_bug_rate: { type: 'number' }, live_wrong: { type: 'number' }, wrong_beliefs: { type: 'number' } }, required: ['round'] }

const engine = (cmd, schema, phaseName) => agent(
  `Run exactly this one shell command with the Bash tool and nothing else:\n\ncd ${DIR} && python3 llm_engine.py ${cmd}\n\n` +
  `Its output contains one line that starts with "RESULT " followed by a JSON object. Return that JSON object through the structured output, copying values exactly. If the command fails, return an object with round=-1.`,
  { model: 'haiku', effort: 'low', schema, label: `engine ${cmd}`, phase: phaseName })

phase('Init')
const init = await agent(`Run exactly this one shell command with the Bash tool and reply with the word OK if it prints a line starting with OK:\n\ncd ${DIR} && python3 llm_engine.py init ${policy} ${seed} ${rounds}`,
  { model: 'haiku', effort: 'low', label: 'engine init', phase: 'Init' })
log(`init: ${String(init).slice(0, 80)}`)

phase('Rounds')
const trace = []
for (let r = 1; r <= rounds; r++) {
  const st = await engine(`step ${policy} ${r}`, ENGINE_SCHEMA, 'Rounds')
  if (!st || st.round !== r) { log(`round ${r}: engine step failed`); break }
  if (st.prev && st.prev.round) trace.push(st.prev)
  const groups = st.groups || []
  log(`round ${r}: ${st.active} active people in ${groups.length} agent calls` + (st.prev && st.prev.acc !== undefined ? ` | prev acc=${st.prev.acc} bug=${st.prev.round_bug_rate}` : ''))
  await parallel(groups.map((g, i) => () => agent(
    `Read the JSON file at ${g.path} with the Read tool and follow the "instructions" inside it exactly for every person in it. ` +
    `Then use the Write tool to write your decisions as valid JSON to ${g.out}. Do nothing else. When the file is written, reply with ok.`,
    { model: 'haiku', effort: g.effort, label: `r${r} ${g.effort} #${i}`, phase: 'Rounds' })))
}
phase('Finish')
const fin = await engine(`finish ${policy} ${rounds}`, ROW_SCHEMA, 'Finish')
trace.push(fin)
return { policy, trace }
