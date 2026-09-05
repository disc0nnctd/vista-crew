/* The workspace and the chat pane, exercised against a real engine payload.

   Run with:  node tests/ui_check.js
   Needs jsdom (`npm i jsdom`); it is the only dependency in the project and it
   is a test-only one, so the check skips rather than fails when it is absent.

   The fixture is genuine resolve_cover output. Regenerate it with:
     python -c "import json; from aircrew.tools import Tools,dispatch,renumber;        e=dispatch(Tools(),'resolve_cover',{'pairing_id':'P-2291','vacated_by':'C-1042'});        renumber([e]); json.dump(e,open('tests/fixture_resolve_cover.json','w'),indent=1,default=str)"

   The boundary fixtures come from a running server:
     curl -s localhost:8765/api/tools  -o tests/fixture_tools.json
     curl -s localhost:8765/api/prompt -o tests/fixture_prompt.json
*/
const fs = require('fs');
const path = require('path');
let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch { console.log('SKIP  jsdom is not installed (npm i jsdom)'); process.exit(0); }

const ROOT = path.join(__dirname, '..');
const HTML = path.join(ROOT, 'web/index.html');
const src = fs.readFileSync(HTML, 'utf8');
const dom = new JSDOM(src, { url: 'http://127.0.0.1:8768/', runScripts: 'dangerously' });
const { window } = dom;

let fails = 0;
const check = (name, ok, detail) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) { fails++; if (detail) console.log('        ' + String(detail).slice(0, 200)); }
};

setTimeout(async () => {
  const w = window;
  const payload = JSON.parse(fs.readFileSync(
    path.join(__dirname, 'fixture_resolve_cover.json'), 'utf8'));
  const step = { tool_results: [payload], tool_calls: [{ name: 'resolve_cover', arguments: {} }] };

  // 1. markdown
  const md = w.renderAnswer('## Recovery plan\nAssign **C-3310** at *once*.\n- cost `18,500`\n- delay 0h');
  check('bold renders as <strong>', md.querySelectorAll('strong').length === 1, md.innerHTML);
  check('heading renders as <h4>', md.querySelectorAll('h4').length === 1);
  check('list renders 2 items', md.querySelectorAll('li').length === 2);
  check('code renders', md.querySelectorAll('code').length === 1);
  check('no raw asterisks survive', !md.textContent.includes('**'), md.textContent);
  const evil = w.renderAnswer('<img src=x onerror=alert(1)> **safe**');
  check('html in the model reply is escaped', evil.querySelectorAll('img').length === 0, evil.innerHTML);

  // 2. the whole-line bold heading this model actually emits
  const h = w.renderAnswer('**Recovery recommendation:**\nAssign C-3310.');
  check('a whole-line bold becomes a heading', h.querySelector('h4') !== null, h.innerHTML);

  // 3. show workspace
  const steps = w.drawableSteps(step);
  check('resolve_cover counts as drawable', steps.length === 1);
  check('a bare lookup is not drawable',
        w.drawableSteps({ tool_results: [{ data: { x: 1 } }], tool_calls: [{ name: 'nope' }] }).length === 0);

  const m1 = w.say('Advisor', w.renderAnswer('**first** answer'));
  w.showSteps(steps, 'q1', m1);
  const b1 = w.evidenceButton(steps, 'q1', m1);
  check('button is offered', !!b1 && b1.textContent === 'Show in workspace');
  check('turn is marked as showing', m1.classList.contains('showing'));
  const panelsAfter1 = w.document.querySelector('#panels').textContent;
  check('workspace drew the ranked cover', panelsAfter1.includes('Ranked cover'), panelsAfter1.slice(0, 120));

  // a second turn takes the canvas
  const m2 = w.say('Advisor', w.renderAnswer('second answer, no panels'));
  w.markShown(m2);
  check('showing moves to turn 2', m2.classList.contains('showing') && !m1.classList.contains('showing'));

  // clicking turn 1's button brings it back
  b1.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  check('turn 1 workspace comes back', w.document.querySelector('#panels').textContent.includes('Ranked cover'));
  check('showing returns to turn 1', m1.classList.contains('showing') && !m2.classList.contains('showing'));

  // 4. back button
  check('no back bar on a top-level view', w.document.querySelector('.backbar') === null);
  w.pushPanels([w.panel('Why C-5837')], 'Why C-5837');
  const bar = w.document.querySelector('.backbar');
  check('drill-down shows a back bar', bar !== null);
  check('crumb names the drill-down', bar && bar.textContent.includes('Why C-5837'), bar && bar.textContent);
  bar.querySelector('.backbtn').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
  check('back returns to the plan', w.document.querySelector('#panels').textContent.includes('Ranked cover'));
  check('back bar is gone at the root', w.document.querySelector('.backbar') === null);

  // 5. a new answer clears the trail
  w.pushPanels([w.panel('Why C-5837')], 'Why C-5837');
  w.setPanels([w.panel('Fresh answer')], 'q2');
  check('a new answer clears the back trail', w.document.querySelector('.backbar') === null);


  // 6. rule tags and tooltips
  const tag = w.ruleTag('RULE-DUTY-02', 'bad');
  check('rule tag shows the id', tag.textContent === 'RULE-DUTY-02');
  check('rule tag carries the gloss', /60 duty hours/.test(tag.dataset.tooltip), tag.dataset.tooltip);
  check('rule tag is reachable by keyboard', tag.tabIndex === 0);
  w.document.body.appendChild(tag);
  tag.dispatchEvent(new w.MouseEvent('mouseenter'));
  const tip = w.document.querySelector('#rule-tooltip');
  check('hovering opens one tooltip', !!tip && /60 duty hours/.test(tip.textContent));
  tag.dispatchEvent(new w.MouseEvent('mouseleave'));
  check('leaving closes it', w.document.querySelector('#rule-tooltip') === null);
  check('an unknown token gets no tooltip', !w.ruleTag('NOT-A-RULE').dataset.tooltip);

  const reason = w.reasonWithRules(w.document.createElement('span'), 'RULE-DUTY-02: 61.33h exceeds 60h; RULE-REST-04: 9h rest', 'bad');
  check('both rules in a reason become tags', reason.querySelectorAll('.rule-tag').length === 2);
  check('the numbers around them survive', /61.33h exceeds 60h/.test(reason.textContent), reason.textContent);
  check('two findings render on two lines', reason.querySelectorAll('.reason-line').length === 2);

  // 7. excess folds by default
  w.setPanels(w.panelsFor('resolve_cover', {}, payload), 'q');
  // scope to the exclusions panel: the plan panel folds its claims too
  const exclPanel = [...w.document.querySelectorAll('#panels .panel')]
    .find(p => /ruled out/i.test(p.querySelector('h3').textContent));
  check('there is an exclusions panel', !!exclPanel);
  // One row per person, the way v1 showed it: the controller argues about
  // people, not about rule groups.
  const exclFold = exclPanel.querySelector('details.more');
  check('exclusions fold into one list', !!exclFold);
  check('the list starts shut', exclFold && !exclFold.open);
  check('the summary says how many were ruled out',
        /19 ruled out/.test(exclFold.querySelector('summary').textContent),
        exclFold.querySelector('summary').textContent);
  check('the summary still shows the shape of the rejection',
        /rest/.test(exclFold.querySelector('summary').textContent),
        exclFold.querySelector('summary').textContent);
  const rows = exclFold.querySelectorAll('.excl-list li');
  check('every excluded candidate has its own row', rows.length === 19, rows.length + ' rows');
  check('each row names the person', /C-\d{4}/.test(rows[0].textContent), rows[0].textContent);
  check('each row carries its rule as a tag', !!rows[0].querySelector('.rule-tag'));

  // a wide lookup folds past the first rows
  const many = {summary:'142 crew match', claims:[], data:{count:142,
    crew: Array.from({length: 142}, (_, i) => ({crew_id:'C-'+(1000+i), rank:'Captain', base:'BLR'}))}};
  const nodes = w.panelsFor('lookup', {entity:'crew'}, many);
  const host = w.document.createElement('div'); nodes.forEach(n => host.appendChild(n));
  check('a 142-row lookup shows only the first 8', host.querySelector('tbody').children.length === 8,
        host.querySelector('tbody').children.length + ' rows');
  const fold = host.querySelector('details.more');
  check('the rest is folded and counted', !!fold && /142 in total/.test(fold.textContent));
  check('the folded table is not open by default', fold && !fold.open);


  // 7b. the workspace draws the decision, not the last thing that ran
  const lookupEnv = {summary:'1 crew', claims:[], data:{count:1, crew:[{crew_id:'C-3310'}]}};
  const checkEnv  = {summary:'legal', claims:[], data:{crew_id:'C-3310', pairing_id:'P-2291',
                     rules:{legal:true, findings:[], rules_checked:[]},
                     callable:{ok:true, reachability_minutes:45}}};
  const mixed = w.drawableSteps({
    tool_results: [payload, checkEnv, lookupEnv],
    tool_calls: [{name:'resolve_cover', arguments:{}},
                 {name:'check_assignment', arguments:{}},
                 {name:'lookup', arguments:{entity:'crew'}}]});
  check('all three steps are drawable', mixed.length === 3, mixed.length + ' drawable');
  // a payload a panel cannot render must cost the panel, never the turn
  const broken = w.drawableSteps({tool_results:[{summary:'x', data:{crew_id:'C-1'}}],
                                  tool_calls:[{name:'check_assignment', arguments:{}}]});
  check('a malformed payload is skipped, not thrown', broken.length === 0);
  const picked = w.mostDecisive(mixed);
  check('the plan wins over a later check and a later lookup',
        picked.call.name === 'resolve_cover', picked.call.name);
  const m3 = w.say('Advisor', w.renderAnswer('answer'));
  w.showSteps(mixed, 'q', m3);
  check('so the workspace shows the ranked cover',
        w.document.querySelector('#panels').textContent.includes('Ranked cover'));

  // 7c. a turn that computes nothing must not leave the last one's evidence up
  w.setPanels(w.panelsFor('resolve_cover', {}, payload), 'q1');
  w.clearPanels('No engine result for this question', 'came from the conversation');
  const after = w.document.querySelector('#panels').textContent;
  check('a panel-less turn clears the workspace', !after.includes('Ranked cover'), after.slice(0, 90));
  check('and says so rather than going blank', /No engine result/.test(after));
  check('clearing drops the back trail', w.document.querySelector('.backbar') === null);

  // 7d. exclusion wording
  check('negative rest is shown as an overlap',
        w.humanise('RULE-REST-04: only -6.75h rest before COVER on 2026-09-15 (rest conflict)')
          .includes('overlaps COVER by 6.75h'),
        w.humanise('RULE-REST-04: only -6.75h rest before COVER on 2026-09-15 (rest conflict)'));
  check('a real rest gap is left alone',
        w.humanise('RULE-REST-04: only 10.75h rest before P-2204 on 2026-09-17')
          .includes('only 10.75h rest'));

  // With one row per person the rule belongs on the row, since there is no
  // group heading above it to carry it.
  w.setPanels(w.panelsFor('resolve_cover', {}, payload), 'q1');
  const list = [...w.document.querySelectorAll('#panels .panel')]
    .find(p => /ruled out/i.test(p.querySelector('h3').textContent))
    .querySelector('details.more');
  const rows2 = [...list.querySelectorAll('.excl-list li')];
  const labelled = rows2.filter(li => li.querySelector('.rule-tag, .tag')).length;
  check('every row says what stopped that person', labelled === 19,
        labelled + ' of 19 labelled');
  // the on-call window is not a rule breach, so it must not wear a rule id
  const oncall = rows2.find(li => /on-call window/.test(li.textContent));
  check('the on-call window row is not badged as a rule breach',
        !!oncall && !oncall.querySelector('.rule-tag') && !!oncall.querySelector('.tag'),
        oncall && oncall.textContent.slice(0, 70));

  // 8. the boundary and the flow
  const tools = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixture_tools.json'), 'utf8'));
  const prompt = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixture_prompt.json'), 'utf8'));
  w.getJSON = async (p) => p === '/api/tools' ? tools : prompt;

  await w.showBoundary();
  const card = w.document.querySelector('.modal-card');
  check('boundary opens a modal', !!card);
  check('it names the model', /gpt-5.6-luna/.test(card.textContent));
  check('it shows the whole system prompt',
        card.querySelector('pre').textContent.length === prompt.system_prompt.length);
  check('it lists every tool', card.querySelectorAll('.toolist li').length === tools.length,
        card.querySelectorAll('.toolist li').length + ' listed');
  check('required args are starred', /pairing_id\*/.test(card.textContent));
  check('tools carry a tier', card.querySelectorAll('.toolist .tier').length === tools.length);
  card.querySelector('header .ghost').dispatchEvent(new w.MouseEvent('click', {bubbles:true}));
  check('close removes it', w.document.querySelector('.modal') === null);

  w.showFlow();
  const flow = w.document.querySelector('.modal-card');
  check('flow opens a modal', !!flow);
  const svg = flow.querySelector('svg');
  check('the flow is one inline svg, no library', !!svg);
  check('it has a labelled boundary', /THE MODEL DECIDES/.test(svg.textContent) &&
        /PYTHON COMPUTES/.test(svg.textContent));
  check('it shows the gate', /Claim gate/.test(svg.textContent));
  check('it shows the withheld path', /withheld/.test(svg.textContent));
  check('it names the loop bound', /up to 8 rounds/.test(svg.textContent));
  check('every box is drawn', svg.querySelectorAll('rect').length === 8,
        svg.querySelectorAll('rect').length + ' boxes');
  check('the legend explains the colours', flow.querySelectorAll('.flow-legend span').length === 4);
  // Escape closes, because a modal that traps you is worse than no modal.
  w.document.dispatchEvent(new w.KeyboardEvent('keydown', {key:'Escape'}));
  check('escape closes it', w.document.querySelector('.modal') === null);

  // Everything below is a fix from the two outside reviews of the 38-question
  // run. Each block names the question the reviewer was looking at, because a
  // check that outlives the reason for it is a check nobody dares delete.
  const draw = (name, args, env) => {
    const host = w.document.createElement('div');
    w.panelsFor(name, args, env).forEach(n => host.appendChild(n));
    return host;
  };

  // 9. Q23: the tool with no panel, and the workspace that called it nothing
  const restEnv = {
    summary: 'Released 2026-09-16T15:30:00Z, earliest next report 2026-09-17T03:30:00Z after 12h rest.',
    claims: [{id: 'c1', text: 'earliest next report is 2026-09-17T03:30:00Z'}], missing: [],
    data: {released_utc: '2026-09-16T15:30:00Z', min_rest_hours: 12,
           earliest_report_utc: '2026-09-17T03:30:00Z', rule: 'RULE-REST-04',
           rule_text: 'Minimum 12 consecutive hours rest between duties.'}};
  const rest = draw('earliest_next_report', {}, restEnv);
  check('earliest_next_report draws a panel', rest.querySelectorAll('.panel').length === 1);
  check('titled Earliest next report', /Earliest next report/i.test(rest.querySelector('h3').textContent),
        rest.querySelector('h3').textContent);
  check('with released, rest required and earliest report',
        rest.querySelectorAll('.headline .fig').length === 3 &&
        /Released/.test(rest.textContent) && /Rest required/.test(rest.textContent));
  check('the figure to act on is the amber one',
        /03:30Z/.test(rest.querySelector('.fig .v.accent').textContent),
        rest.querySelector('.fig .v.accent').textContent);
  check('the rest rule travels with its caveat',
        !!rest.querySelector('.note .rule-tag') && /minimum only/.test(rest.textContent));
  check('so Q23 now has a drawable step',
        w.drawableSteps({tool_results: [restEnv],
                         tool_calls: [{name: 'earliest_next_report', arguments: {}}]}).length === 1);

  // and when a tool did run but nothing could draw it, the workspace says that
  const drew = w.emptyState({tool_results: [restEnv], tool_calls: [{name: 'x'}]});
  check('a computed turn that drew nothing says computed, not drawn',
        /Computed, not drawn/.test(drew[0]) && /figure is in the reply/.test(drew[1]), drew[0]);
  check('and never claims the conversation answered it',
        !/from the conversation/.test(drew.join(' ')));
  const nothing = w.emptyState({});
  check('a turn where no tool ran keeps the copy that is true of it',
        /No engine result/.test(nothing[0]) && /from the conversation/.test(nothing[1]));
  w.clearPanels(...drew);
  check('the panel title stops saying nothing was computed',
        /No panel for this result/.test(w.document.querySelector('#panels').textContent) &&
        !/Nothing computed/.test(w.document.querySelector('#panels').textContent));

  // 10. Q01: the column the question asked for is an object
  w.setAnswerText('');
  const reserves = {summary: '2 reserves on call', claims: [], missing: [], data: {count: 2, reserves: [
    {crew_id: 'C-3310', name: 'D. Reddy', rank: 'Captain', base: 'BLR', ratings: ['A320'],
     window: {start: '06:00', end: '18:00'}, reachability_minutes: 45},
    {crew_id: 'C-3312', name: 'P. Sharma', rank: 'First Officer', base: 'BLR', ratings: ['A320'],
     window: {start: '00:00', end: '12:00'}, reachability_minutes: 60}]}};
  const res = draw('lookup', {entity: 'reserves'}, reserves);
  check('an on-call window survives as a column',
        [...res.querySelectorAll('th')].some(t => /window/i.test(t.textContent)),
        [...res.querySelectorAll('th')].map(t => t.textContent).join('|'));
  check('and renders as a window, not [object Object]',
        /06:00–18:00Z/.test(res.textContent) && !/object Object/.test(res.textContent));
  check('a start_utc/end_utc window renders the same way',
        w.windowText({start_utc: '03:00', end_utc: '15:00'}) === '03:00–15:00Z');
  check('any other nested payload is still dropped from the table',
        w.windowText({drivers: ['a'], score: 3}) === null);

  // 11. Q21: a red badge on a legal answer
  const week = {crew_id: 'C-2210', name: 'S. Kapoor', min_rest_hours: 12, legal: false, duties: [
    {date: '2026-09-15', pairing_id: 'P-2291', aircraft: 'VT-DXC',
     report_utc: '2026-09-15T06:00:00Z', release_utc: '2026-09-15T15:30:00Z', sectors: 3,
     flights: ['DX412-2026-09-15', 'DX413-2026-09-15', 'DX588-2026-09-15'],
     fdp_hours: 9.5, fdp_limit: 12.5, proposed: true,
     rest_before_hours: null, rest_before_ok: null, overlaps_previous_by_hours: null}],
    breaches: [{rule: 'RULE-BASE-07', text: 'Reserve callout from own base only.',
                limit: 'BLR', actual: 'DEL', excess: null,
                context: {kind: 'base', base: 'DEL', station: 'BLR', needs_positioning: true},
                message: 'RULE-BASE-07: based DEL, pairing departs BLR'}]};
  const legalCheck = {summary: 'legal', claims: [], missing: [], data: {
    crew_id: 'C-2210', pairing_id: 'P-2291', base: 'DEL',
    rules: {legal: true, issues: [], breaches: [], rules_checked: []},
    callable: {is_reserve: true, callable: true, reason: null, reachability_minutes: 60},
    timeline: week}};
  const legal = draw('check_assignment', {}, legalCheck);
  check('a legal verdict carries no red rule badge anywhere on it',
        !legal.querySelector('.tag.illegal'), legal.textContent.slice(0, 90));
  const costTag = legal.querySelector('.tag.warn');
  check('the positioning rule is an amber cost note',
        !!costTag && /^cost: RULE-BASE-07/.test(costTag.textContent), costTag && costTag.textContent);
  check('and says what the cost actually is',
        /deadhead positioning from DEL/.test(legal.textContent) &&
        !/pairing departs BLR/.test(legal.textContent));
  const stoppedCheck = {summary: 'illegal', claims: [], missing: [], data: {
    ...legalCheck.data,
    rules: {legal: false, issues: ['RULE-BASE-07: based DEL, pairing departs BLR'],
            breaches: [{rule: 'RULE-BASE-07'}], rules_checked: []}}};
  check('a rule the check did hold against them stays red',
        !!draw('check_assignment', {}, stoppedCheck).querySelector('.tag.illegal'));
  check('and a timeline with no check around it is unchanged',
        !!draw('duty_timeline', {}, {data: week}).querySelector('.tag.illegal'));

  // 12. Q18/Q21/Q28/Q34/Q02: raw flight keys in the timeline row
  const rail = draw('duty_timeline', {}, {data: week});
  check('the timeline prints DX412, not DX412-2026-09-15',
        /DX412, DX413, DX588/.test(rail.textContent) && !/DX412-2026/.test(rail.textContent),
        rail.querySelector('.day .m').textContent);

  // Q19/Q35: the same key in the station-closure flight column
  const closure = {summary: '', claims: [], missing: [], data: {
    station: 'BLR', date: '2026-09-17', window_utc: '08:00–14:00Z', reopen_plus_30: '14:30Z',
    affected_flight_ids: ['DX412-2026-09-17'], passengers_at_risk: 162,
    flights_needing_recrew: ['DX412-2026-09-17'],
    per_flight_assessment: [{min_delay_hours: 2, crew_fdp_after_delay: 13.5, fdp_limit: 12,
      flight_id: 'DX412-2026-09-17', pairing_id: 'P-2291', action: 'exceeds FDP — re-crew'}]}};
  const shut = draw('simulate_disruption', {kind: 'closure'}, closure);
  check('the closure flight column drops the date too',
        /DX412/.test(shut.textContent) && !/DX412-2026/.test(shut.textContent));

  // 13. Q33: the WHY column was cut mid-sentence
  const delay = {summary: '', claims: [], missing: [], data: {
    aircraft: 'VT-DXA', date: '2026-09-16', mode: 'technical', delay_hours: 1.5,
    fdp_after_delay: 12.75, fdp_before: 11.25, fdp_limit: 12, sectors: 4, breach: true,
    breach_detail: 'RULE-FDP-01: delayed duty runs 12.75h vs 12.0h limit',
    options: [{rank: 1, cost_inr: 75000, action: 'Keep them on DX401–DX403',
               reasoning: 'Delayed 3-leg duty FDP 9.5h vs 12.5h limit — legal. ' +
                          'Reserve set covers DX404 within its on-call window.'}]}};
  const why = draw('simulate_disruption', {kind: 'delay'}, delay).querySelector('td.wrap');
  check('the why column wraps rather than truncating',
        !!why && /Reserve set covers DX404 within its on-call window/.test(why.textContent));

  // 14. Q12: a 147-row dump where four rows were the answer
  const flights = {summary: '12 flights matched', claims: [], missing: [], data: {count: 12,
    flights: Array.from({length: 12}, (_, i) => ({
      flight_id: 'DX' + (401 + i) + '-2026-09-14', flight_no: 'DX' + (401 + i),
      date: '2026-09-14', dep_station: 'BLR', arr_station: 'DEL', block_hours: 2.75}))}};
  w.setAnswerText('The longest block time is 2.75h. The flights are DX411 and DX412.');
  const named = draw('lookup', {entity: 'flights'}, flights);
  const namedRows = [...named.querySelector('tbody').children];
  check('a row the answer names is hoisted to the top', /DX411/.test(namedRows[0].textContent),
        namedRows[0].textContent);
  check('both named rows come first',
        namedRows[0].classList.contains('named') && namedRows[1].classList.contains('named') &&
        !namedRows[2].classList.contains('named'));
  check('and they are marked as the ones the answer named',
        /in the reply/.test(namedRows[0].textContent));
  check('the panel says why the order changed',
        /The 2 rows the answer names are shown first/.test(named.textContent),
        named.querySelector('.note') && named.querySelector('.note').textContent);
  check('the flight id drops the date it repeats', !/DX411-2026-09-14/.test(named.textContent));
  check('and the column it duplicates goes with it',
        [...named.querySelector('table').querySelectorAll('th')]
          .filter(t => /flight/i.test(t.textContent)).length === 1,
        [...named.querySelector('table').querySelectorAll('th')].map(t => t.textContent).join('|'));
  w.setAnswerText('');
  const unnamed = draw('lookup', {entity: 'flights'}, flights);
  check('with nothing named the engine order is left alone',
        /DX401/.test([...unnamed.querySelector('tbody').children][0].textContent) &&
        !unnamed.querySelector('tr.named'));

  // 15. number grouping, one convention across both panes
  const money = w.renderAnswer('Cancelling costs INR 1,500,000 against INR 18,500.');
  check('the chat groups money the way the table does',
        /INR 15,00,000/.test(money.textContent) && !/1,500,000/.test(money.textContent),
        money.textContent);
  check('a small figure is untouched', /INR 18,500/.test(money.textContent));
  check('a date is not a number to regroup',
        /on 2026-09-15/.test(w.renderAnswer('on 2026-09-15').textContent));
  check('nor is a crew id', /C-1042/.test(w.renderAnswer('C-1042 is out').textContent));

  // 16. Q32: two thirteen-row tables below the fold
  const vac = payload.data;
  const joint = {summary: '', claims: [], missing: [], data: {
    vacancies: [{pairing_id: 'P-2205', role: 'Captain'}, {pairing_id: 'P-2212', role: 'Captain'}],
    plan_count: 157, total_cost_inr: 42500, tie_count: 20,
    optimal: {assignments: [{cost_inr: 18500, action: 'Assign Captain C-3305 (reserve callout)'},
                            {cost_inr: 24000, action: 'Assign Captain C-1017 (day-off callout)'}]},
    per_vacancy: [vac, vac]}};
  const jointHost = draw('resolve_cover', {}, joint);
  const folds = [...jointHost.querySelectorAll('details.more.standalone')];
  check('each vacancy folds under one line', folds.length === 2, folds.length + ' folds');
  check('and starts shut', folds.every(f => !f.open));
  const line = folds[0].querySelector('summary').textContent;
  check('the summary names the vacancy, the count and the pick',
        /Ranked cover — P-2291 Captain/.test(line) && /options/.test(line) &&
        /recommended C-3310/.test(line), line);
  check('the ranked table is inside the fold, not beside it',
        !!folds[0].querySelector('table') && jointHost.children[0].className === 'panel');
  check('the joint plan itself is left open',
        /Joint plan/.test(jointHost.querySelector('.panel h3').textContent));

  // 17. Q38: an uncomputed opinion looks like a computed answer
  const opinion = w.say('Advisor', w.renderAnswer('Surface three data points per line.'));
  w.trace(opinion, []);
  const chip = opinion.querySelector('.trace .uncomputed');
  check('an answer with no tool behind it is chipped as an opinion',
        !!chip && /not computed — advisory opinion/.test(chip.textContent), chip && chip.textContent);
  const computed = w.say('Advisor', w.renderAnswer('Assign C-3310.'));
  w.trace(computed, [{name: 'resolve_cover'}]);
  check('and a computed one still names its tools, with no chip',
        /computed by/.test(computed.querySelector('.trace').textContent) &&
        !computed.querySelector('.uncomputed'));

  // 18. ported from v1: the seven rule chips under the recommended row
  const plan = draw('resolve_cover', {}, payload);
  const checksRows = plan.querySelectorAll('tr.checks');
  check('the recommended row carries a checks row', checksRows.length === 1,
        checksRows.length + ' checks rows');
  check('naming all seven rules', checksRows[0].querySelectorAll('.rule-tag').length === 7);
  check('each one green, because each one passed',
        checksRows[0].querySelectorAll('.rule-tag.ok').length === 7);
  check('every chip still carries its gloss',
        [...checksRows[0].querySelectorAll('.rule-tag')].every(t => !!t.dataset.tooltip));
  check('and the chips sit under row 1, not on every row',
        plan.querySelector('tbody').children[0].classList.contains('pick') &&
        plan.querySelector('tbody').children[1].classList.contains('checks'));

  // exclusion vocabulary: the fold and the figure above it say the same words
  const exclSummary = [...plan.querySelectorAll('.panel')]
    .find(p => /ruled out/i.test(p.querySelector('h3').textContent))
    .querySelector('details.more summary').textContent;
  check('the fold summary uses the words the header uses',
        /aircraft rating/.test(exclSummary) && !/qual/.test(exclSummary), exclSummary);
  check('and is the engine’s own orientation string, verbatim',
        exclSummary.includes(payload.data.exclusions_orientation),
        payload.data.exclusions_orientation);

  // 19. the desk session id, on the two endpoints that carry a conversation
  const sent = [];
  const realFetch = w.fetch;
  w.fetch = async (url, init) => {
    sent.push({url, init});
    return {ok: true, json: async () => ({reply: 'ok'})};
  };
  await w.api('/api/chat', {message: 'hi'});
  await w.api('/api/reset', {});
  await w.api('/api/tool', {name: 'lookup', arguments: {entity: 'crew'}});
  w.fetch = realFetch;
  const desk = sent[0].init.headers['X-Desk-Session'];
  check('the chat call carries a desk session id', !!desk, JSON.stringify(sent[0].init.headers));
  check('reset carries the same one, so the page is one session',
        sent[1].init.headers['X-Desk-Session'] === desk);
  check('the stateless tool endpoint is left exactly as it was',
        !sent[2].init.headers['X-Desk-Session'] &&
        sent[2].init.headers['Content-Type'] === 'application/json');
  check('and nothing else about the chat call changed',
        sent[0].init.method === 'POST' &&
        sent[0].init.headers['Content-Type'] === 'application/json' &&
        JSON.parse(sent[0].init.body).message === 'hi');

  console.log(fails ? `\n${fails} FAILURE(S)` : '\nchat pane works');
  process.exit(fails ? 1 : 0);
}, 400);
