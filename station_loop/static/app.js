"use strict";
const $ = id => document.getElementById(id);
const names = {engineer:"엔지니어",medic:"의무관",scout:"탐사 담당자",control:"제어실",greenhouse:"온실",storage:"창고",oxygen_generator:"산소 생산기",wrench_1:"수리 공구",repair_part_1:"수리 부품"};
const kinds = {move:"이동",pickup:"줍기",give:"전달",repair:"수리",say:"무전",wait:"대기"};
const roomIcons = {greenhouse:"♧",control:"⌘",storage:"▧"};
let state, busy=false, reasonLabels={}, authState=null;
function node(tag, text, className){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(className)e.className=className;return e;}
function avatar(id){return node("span",{engineer:"E",medic:"M",scout:"S"}[id]||"?",`avatar ${id}`);}
function actionLabel(a){return [kinds[a.kind]||a.kind,...Object.entries(a).filter(([k])=>k!=="kind"&&k!=="text").map(([,v])=>names[v]||v)].join(" · ");}
async function api(path, body){const response=await fetch(path,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const result=await response.json();if(!response.ok){const error=new Error(result.error||"요청 실패");Object.assign(error,result);throw error;}return result;}
function options(w, id){const rows=[null,{kind:"wait"}];for(const destination of w.rooms)rows.push({kind:"move",destination});for(const item_id of Object.keys(w.items)){rows.push({kind:"pickup",item_id});for(const recipient of Object.keys(w.agents))if(recipient!==id)rows.push({kind:"give",item_id,recipient});}for(const device_id of Object.keys(w.devices))rows.push({kind:"repair",device_id});return rows;}
function missionHint(w){
  const online=w.devices.oxygen_generator.status==="online",part=w.items.repair_part_1;
  const steps=[!!part?.owner||online,part?.owner==="engineer"||online,online,w.production_streak>=w.rules.success_production_streak];
  if(w.status==="won")return {steps,title:"임무 완료! 승무원 전원이 생존했습니다.",detail:"행동 기록을 돌아보거나 새 게임에서 다른 지시와 동선을 실험해 보세요.",order:""};
  if(w.status==="failed")return {steps,title:w.termination==="oxygen_depleted"?"산소가 고갈됐습니다. 새 게임에서 다시 도전하세요.":"제한된 턴 안에 안정화하지 못했습니다.",detail:"새 게임에서는 탐사 담당자가 부품을 운반하는 동안 엔지니어를 온실로 먼저 보내 보세요.",order:""};
  if(online){const left=Math.max(0,w.rules.success_production_streak-w.production_streak);return {steps,title:`생산기 복구 완료. ${left}턴 더 가동하면 성공합니다.`,detail:"승무원은 대기해도 됩니다. 다음 턴을 진행하며 산소와 전력을 확인하세요. 생산에는 턴당 전력 1이 필요합니다.",order:"생산기가 복구됐으니 모두 대기하며 산소와 전력을 확인해 줘."};}
  if(part?.owner==="engineer")return {steps,title:w.agents.engineer.room==="greenhouse"?"엔지니어에게 생산기 수리를 지시하세요.":"엔지니어가 부품을 받았습니다. 온실로 이동하세요.",detail:"온실에 있는 엔지니어가 수리 부품과 공구를 소지하고 전력이 2 이상이면 수리할 수 있습니다.",order:"엔지니어는 온실로 이동한 뒤 산소 생산기를 수리해 줘. 다른 승무원은 불필요한 물품 이동 없이 대기해 줘."};
  if(part?.owner){const owner=part.owner,labels={scout:"탐사 담당자",medic:"의무관"};const together=w.agents[owner].room===w.agents.engineer.room;return {steps,title:together?`${labels[owner]||owner}가 가진 부품을 엔지니어에게 전달하세요.`:"부품을 확보했습니다. 엔지니어와 같은 구역으로 이동하세요.",detail:"운반 담당자가 엔지니어와 만난 뒤 ‘전달 · 수리 부품 · 엔지니어’를 실행해야 합니다. 물품은 이동만으로 자동 전달되지 않습니다.",order:`${labels[owner]||owner}는 엔지니어가 있는 구역으로 이동한 뒤 수리 부품을 전달해 줘. 엔지니어는 온실에서 수리를 준비해 줘.`};}
  return {steps,title:w.agents.scout.room==="storage"?"탐사 담당자가 창고에 도착했습니다. 수리 부품을 주우세요.":"탐사 담당자를 창고로, 엔지니어를 온실로 보내세요.",detail:"창고 바닥의 부품은 ‘줍기’로 확보합니다. 그다음 제어실을 거쳐 온실로 이동하고 엔지니어에게 전달하세요.",order:"탐사 담당자는 창고에서 수리 부품을 주워 제어실을 거쳐 온실의 엔지니어에게 전달해 줘. 엔지니어는 온실로 이동해 수리를 준비하고, 의무관은 산소를 감시해 줘."};
}
function renderGuide(data){
  const w=data.world,hint=missionHint(w),isLLM=["openai","chatgpt"].includes(data.provider.provider);
  const setup=data.provider.provider==="chatgpt"&&(!authState?.sharing||!data.provider.model);
  $("hint-title").textContent=setup?"먼저 ChatGPT 계정을 연결하고 모델을 선택하세요.":hint.title;
  $("hint-detail").textContent=setup?"바로 아래의 Continue with ChatGPT로 연결하고 모델을 고르면 승무원들에게 지시할 수 있습니다.":hint.detail;
  const labels=["창고에서 부품 줍기","엔지니어에게 전달","온실에서 수리",`${w.rules.success_production_streak}턴 연속 가동`];
  $("mission-steps").replaceChildren(...labels.map((label,i)=>node("li",(hint.steps[i]?"✓ ":`${i+1}. `)+label,hint.steps[i]?"done":"")));
  $("first-turn").hidden=w.tick!==0||w.status!=="running";
  $("first-turn").disabled=busy;
  $("example-order").hidden=!isLLM;
  $("example-order").disabled=busy||setup||!hint.order;
  $("command-help").textContent=isLLM?"① 지시 대상을 고르고 요청을 입력하세요. ② 다음 턴 진행을 누르세요. ③ 기록에서 실제 행동을 확인하세요. 지시 없이 승무원에게 판단을 맡겨도 됩니다.":"현재는 오프라인 승무원이 행동을 결정합니다. 다음 턴 진행만 눌러 관찰하거나, 아래에서 행동을 직접 선택하세요. 자연어 지시는 이 모드에서 해석하지 않습니다.";
}
const sampleScenes=[
  ["비상 경보, 산소 생산기 정지","온실의 산소 생산기가 고장 났습니다. 수리 공구는 엔지니어에게, 부품은 창고에 있습니다. 다음 장면을 누르면 승무원들의 첫 행동을 볼 수 있습니다."],
  ["역할을 나눠 움직이다","엔지니어는 온실로, 탐사 담당자는 창고로 이동했습니다. 동시에 서로 다른 일을 맡으면 제한된 턴을 아낄 수 있습니다. 의무관은 산소를 감시합니다."],
  ["창고에서 부품을 확보하다","탐사 담당자가 수리 부품을 주웠습니다. 창고에 도착하는 것과 물품을 줍는 것은 별도의 행동입니다. 지도 아래 소지품 표시가 바뀐 것을 확인해 보세요."],
  ["닫힌 문을 피해 우회하다","창고와 온실 사이의 직통문은 닫혀 있습니다. 탐사 담당자는 부품을 들고 제어실로 돌아왔습니다. 한 턴에 열린 통로 하나만 이동할 수 있습니다."],
  ["온실에서 합류하다","탐사 담당자가 온실의 엔지니어와 만났습니다. 같은 구역에 도착했지만 부품은 아직 탐사 담당자가 갖고 있습니다. 다음에는 전달 행동이 필요합니다."],
  ["수리 준비를 마치다","탐사 담당자가 부품을 엔지니어에게 전달했습니다. 이제 엔지니어는 온실에서 공구와 부품을 모두 소지하고 있어 수리를 시작할 수 있습니다."],
  ["산소 생산기를 복구하다","엔지니어가 부품 1개와 전력 2를 사용해 생산기를 수리했습니다. 같은 턴에 첫 산소 생산도 이루어졌습니다. 생산량 4가 호흡 소모 3보다 많아 산소가 늘기 시작합니다."],
  ["두 번째 가동을 확인하다","승무원들은 대기하며 생명 유지 장치를 확인합니다. 두 턴 연속 생산에 성공했습니다. 한 턴 더 정상 가동하면 임무가 끝납니다."],
  ["임무 완료, 승무원 전원 생존","생산기가 3턴 연속 가동돼 정거장이 안정됐습니다. 부품 확보 → 우회 이동 → 전달 → 수리 순서로 협력한 결과입니다. 이제 직접 플레이로 돌아가 다른 지시를 내려보거나 샘플을 처음부터 다시 볼 수 있습니다."]
];
function renderSample(data){
  const viewing=data.provider.provider==="scripted",w=data.world;
  $("sample-player").hidden=!viewing;$("play-briefing").hidden=viewing;$("command-deck").hidden=viewing;
  $("start-sample").hidden=viewing;$("return-play").hidden=!viewing;
  $("chatgpt-settings").hidden=viewing||!authState?.enabled;
  $("reset").textContent=viewing?"샘플 처음부터":"새 게임";
  $("sample-note").textContent=viewing?"관전 중 · 로그인과 AI 호출 없이 실제 게임 규칙으로 진행합니다.":"처음이라면 정해진 8턴 시나리오를 다음 버튼으로 따라가 보세요. AI 호출 없이 볼 수 있습니다.";
  if(viewing){const scene=sampleScenes[Math.min(w.tick,8)];$("scene-title").textContent=scene[0];$("scene-detail").textContent=scene[1];$("sample-chapter").textContent=`장면 ${w.tick} / 8`;}
}
function render(data){
  state=data;if(data.reason_labels)reasonLabels=data.reason_labels;
  const w=data.world;
  $("tick").replaceChildren(document.createTextNode(String(w.tick).padStart(2,"0")+" "),node("small",`/ ${w.max_ticks} TURNS`));
  $("status").textContent={running:"MISSION IN PROGRESS",won:"MISSION COMPLETE",failed:"MISSION FAILED"}[w.status];
  $("engine").textContent=data.judge.backend==="wirelog"?`판정: Wirelog ${data.judge.core}`:"판정: Python 참조 엔진";
  $("provider").textContent="승무원: "+({cooperative:"오프라인 협력 규칙",scripted:"샘플 플레이 관전",openai:`LLM · ${data.provider.model}`,chatgpt:`Using ChatGPT plan · ${data.provider.model||"모델 선택 필요"}`}[data.provider.provider]);
  $("mode-note").textContent=["openai","chatgpt"].includes(data.provider.provider)?"지시는 이번 턴의 요청으로 전달됩니다.":"현재 모드에서는 자연어 지시를 해석하지 않습니다. 아래에서 직접 행동을 지정할 수 있습니다.";
  if(data.provider.provider==="chatgpt"&&$("chatgpt-model"))$("chatgpt-model").value=data.provider.model||"";
  $("oxygen").textContent=w.resources.oxygen;$("power").textContent=w.resources.power;
  $("oxygen-meter").max=w.resources.oxygen_capacity;$("oxygen-meter").value=Math.max(0,w.resources.oxygen);
  $("power-meter").value=w.resources.power;
  $("device").textContent=w.devices.oxygen_generator.status==="online"?"가동 중":"복구 필요";
  $("streak").replaceChildren(...Array.from({length:w.rules.success_production_streak},(_,i)=>node("i",undefined,i<w.production_streak?"active":"")));
  $("map").replaceChildren();
  for(const room of ["greenhouse","control","storage"]){const el=node("article",undefined,"room");el.append(node("span",roomIcons[room]),node("h3",names[room]));const items=Object.values(w.items).filter(i=>i.room===room).map(i=>names[i.id]||i.id);el.append(node("small",room==="greenhouse"?(w.devices.oxygen_generator.status==="online"?"산소 +4 / 턴":"생산기 고장"):items.join(", ")||"장비 이상 없음"));const occupants=node("div",undefined,"occupants");for(const [a,info] of Object.entries(w.agents))if(info.room===room)occupants.append(avatar(a));el.append(occupants);$("map").append(el);}
  $("crew").replaceChildren();for(const [id,info] of Object.entries(w.agents)){const row=node("div",undefined,"crew-row"),body=node("div");body.append(node("strong",names[id]),node("small",names[info.room]+" · "+({engineer:"시설 복구 우선",medic:"생존 여유 보존",scout:"자원 확보·운반"}[id])));row.append(avatar(id),body,node("span",Object.values(w.items).filter(i=>i.owner===id).map(i=>names[i.id]||i.id).join(" · ")||"소지품 없음","inventory"));$("crew").append(row);}
  $("timeline").replaceChildren();let count=0;
  if(w.status!=="running")$("timeline").append(node("div",w.status==="won"?"생명 유지 장치 안정화 · 승무원 전원 생존":w.termination==="oxygen_depleted"?"산소 고갈 · 임무 실패":"시간 초과 · 임무 실패",`result ${w.status==="failed"?"failed":""}`));
  if(!data.records.length)$("timeline").append(node("div","아직 정거장은 조용합니다. 첫 턴을 진행해 보세요.","empty"));
  for(const record of data.records){const group=node("div",undefined,"turn-group");group.append(node("div",`TURN ${String(record.tick).padStart(2,"0")}`,"turn-heading"));if(record.instruction?.text)group.append(node("p",`관제 → ${names[record.instruction.target]||"전체"}: ${record.instruction.text}`));for(const event of record.events){count++;const el=node("div",undefined,`event ${event.allowed?"":"rejected"}`),body=node("div",undefined,"event-body"),title=node("div");title.append(node("strong",names[event.agent]),node("span",actionLabel(event.action)+(event.allowed?"":" · 거절"),"action"));body.append(title);if(event.utterance)body.append(node("p",event.utterance));if(event.action.kind==="say")body.append(node("p",event.action.text));const detail=node("details"),summary=node("summary",event.allowed?"실행 조건 충족":"실행하지 못한 이유");detail.append(summary);const list=node("ul");for(const reason of event.reasons)list.append(node("li",reasonLabels[reason]||event.provider_error||reason));if(event.allowed)list.append(node("li","현재 위치·소유권·역할·자원에 적용되는 조건을 만족했습니다."));detail.append(list);body.append(detail);el.append(avatar(event.agent),body);group.append(el);}group.append(node("div",`환경 갱신: 산소 −${record.environment.oxygen_consumed} +${record.environment.oxygen_produced} · 잔량 ${record.world.resources.oxygen}`,"environment"));$("timeline").append(group);}
  $("event-count").textContent=count+" EVENTS";$("timeline").scrollTop=$("timeline").scrollHeight;
  $("manual-controls").replaceChildren();for(const id of Object.keys(w.agents)){const label=node("label",names[id]),select=node("select");select.dataset.agent=id;for(const a of options(w,id)){const option=node("option",a?actionLabel(a):"승무원에게 맡기기");option.value=JSON.stringify(a);select.append(option);}label.append(select);$("manual-controls").append(label);}
  setBusy(busy);
}
function setBusy(value){
  busy=value;const gated=state?.provider.provider==="chatgpt"&&(!authState?.sharing||!!authState?.blocked||!state.provider.model);
  $("advance").disabled=busy||state?.world.status!=="running"||gated;$("reset").disabled=busy;
  $("advance").textContent=busy?"승무원들이 결정하는 중…":"다음 턴 진행 →";
  $("start-sample").disabled=busy||!state;$("return-play").disabled=busy;
  $("sample-next").disabled=busy||state?.world.status!=="running";
  $("sample-next").textContent=busy?"장면 진행 중…":state?.world.status==="won"?"관전 완료":`다음 장면 · ${Math.min((state?.world.tick||0)+1,8)} / 8 →`;
  for(const el of document.querySelectorAll("input,select"))el.disabled=busy;
  if(typeof authButtons==="function")authButtons();if(state){renderGuide(state);renderSample(state);}
}
function showError(error){$("error").hidden=!error;$("error").textContent=error?.message||"";if(["usage_limit","not_eligible","reauth_required","consent_required","not_connected"].includes(error?.code)&&typeof drawAuth==="function")api("/api/auth/status").then(drawAuth).catch(()=>{});}
$("advance").addEventListener("click",async()=>{if(busy)return;showError(null);setBusy(true);try{const overrides={},viewing=state.provider.provider==="scripted";if(!viewing)for(const el of document.querySelectorAll("select[data-agent]")){const action=JSON.parse(el.value);if(action)overrides[el.dataset.agent]={action,utterance:"관제실에서 지정한 행동입니다."};}const text=viewing?"":$("instruction").value.trim(),payload={request_id:crypto.randomUUID(),run_id:state.run_id,expected_tick:state.world.tick,overrides,instruction:text?{target:$("target").value,text}:null};render(await api("/api/turn",payload));$("instruction").value="";}catch(e){showError(e);}finally{setBusy(false);}});
$("reset").addEventListener("click",async()=>{if(busy)return;showError(null);setBusy(true);try{render(await api("/api/reset",{run_id:state.run_id}));}catch(e){showError(e);}finally{setBusy(false);}});
$("example-order").addEventListener("click",()=>{if(!state||busy)return;$("target").value="all";$("instruction").value=missionHint(state.world).order;$("command-deck").scrollIntoView({behavior:"smooth",block:"center"});$("instruction").focus({preventScroll:true});});
$("first-turn").addEventListener("click",()=>{if(!state||busy||state.world.tick!==0)return;const actions={engineer:{kind:"move",destination:"greenhouse"},scout:{kind:"move",destination:"storage"},medic:{kind:"wait"}};for(const select of document.querySelectorAll("select[data-agent]"))select.value=JSON.stringify(actions[select.dataset.agent]);$("manual-panel").open=true;$("command-help").textContent="첫 턴 행동을 선택했습니다. 엔지니어는 온실로, 탐사 담당자는 창고로 이동하고 의무관은 대기합니다. ‘다음 턴 진행’을 누르면 실행됩니다.";$("command-deck").scrollIntoView({behavior:"smooth",block:"center"});});
async function switchMode(mode){if(busy||!state)return;setBusy(true);showError(null);try{render(await api("/api/mode",{run_id:state.run_id,mode}));$("instruction").value="";if(mode==="play"&&typeof refreshAuth==="function")await refreshAuth();}catch(error){if(error.message==="찾을 수 없습니다.")error.message="실행 중인 서버에 샘플 기능이 아직 반영되지 않았습니다. 서버 터미널에서 Ctrl+C로 종료한 뒤 다시 실행하고 브라우저를 새로고침해 주세요.";showError(error);}finally{setBusy(false);}}
$("start-sample").addEventListener("click",()=>switchMode("sample"));
$("return-play").addEventListener("click",()=>switchMode("play"));
$("sample-next").addEventListener("click",()=>$("advance").click());
api("/api/state").then(render).catch(showError);
