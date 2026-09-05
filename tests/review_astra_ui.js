/* Known UI failures from REVIEW_ASTRA. Requires jsdom, like ui_check.js. */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { JSDOM, VirtualConsole } = require('jsdom');

const root = path.join(__dirname, '..');
const payload = JSON.parse(execFileSync('python', ['-c',
  "import json; from aircrew.tools import Tools,dispatch; print(json.dumps(dispatch(Tools(),'simulate_disruption',{'kind':'closure','station':'BLR','on_date':'2026-09-17','start_utc':'08:00','end_utc':'14:00','with_recovery':False})))"
], { cwd: root, encoding: 'utf8' }));
const dom = new JSDOM(fs.readFileSync(path.join(root, 'web/index.html'), 'utf8'), {
  url: 'http://127.0.0.1/', runScripts: 'dangerously', virtualConsole: new VirtualConsole()
});
const w = dom.window;
let unexpected = 0;
function knownFailure(name, desired, observed) {
  console.log(`${desired ? 'UNEXPECTED PASS' : 'KNOWN FAILURE'} ${name}: ${observed}`);
  if (desired) unexpected++;
}

(async () => {
  await new Promise(resolve => setTimeout(resolve, 20));
  const closure = w.pClosure(payload)[0];
  const reCrew = [...closure.querySelectorAll('.fig')]
    .find(node => node.querySelector('.k').textContent === 'Need re-crew');
  knownFailure('an uncomputed re-crew count is not zero',
    reCrew.querySelector('.v').textContent !== '0', reCrew.textContent);

  w.fetch = async () => ({ ok: true, json: async () => ({
    reply: 'I could not ground every figure in that answer against a computed result, so I am not stating it.',
    corrected: true, grounded: false, tool_calls: [], tool_results: []
  }) });
  w.document.querySelector('#input').value = 'Who should cover?';
  w.document.querySelector('#composer').dispatchEvent(new w.Event('submit', { cancelable: true }));
  await new Promise(resolve => setTimeout(resolve, 20));
  knownFailure('a withheld reply is not labelled as passed',
    !w.document.body.textContent.includes('the reply above passed'),
    'grounded:false still produces "the reply above passed"');

  let sent;
  w.fetch = async (url, options) => {
    sent = JSON.parse(options.body);
    return { ok: true, json: async () => ({ data: { error: 'test stops after capturing request' } }) };
  };
  await w.drill('C-2210', 'P-2291', null);
  knownFailure('a positioned option retains its scenario when drilled',
    sent.arguments.positioned === true, JSON.stringify(sent.arguments));

  dom.window.close();
  process.exitCode = unexpected ? 1 : 0;
})().catch(error => { console.error(error); dom.window.close(); process.exitCode = 1; });
