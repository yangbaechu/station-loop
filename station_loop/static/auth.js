"use strict";
let authBusy=false;
function authButtons(){
  for(const id of ["auth-account","chatgpt-connect","chatgpt-add","chatgpt-logout","chatgpt-model","chatgpt-retry"]){
    $(id).disabled=authBusy||busy||(id==="chatgpt-model"&&!authState?.sharing);
  }
}
function drawAuth(status,models=[]){
  authState=status;
  $("chatgpt-settings").hidden=!status.enabled||state?.provider.provider==="scripted";
  if(!status.enabled)return;
  const accounts=$("auth-account");accounts.replaceChildren(node("option","새 계정"));accounts.firstChild.value="";
  for(const account of status.accounts){const option=node("option",account.label);option.value=account.id;accounts.append(option);}
  accounts.value=status.active||"";
  const sharing=status.sharing;
  $("chatgpt-logout").hidden=!status.connected;
  $("chatgpt-retry").hidden=!status.blocked;
  $("auth-status").textContent=status.notice||(status.blocked==="usage_limit"?"ChatGPT 사용량 한도에 도달했습니다. Manage usage에서 한도를 확인해 주세요.":status.blocked?"다시 연결하거나 계정 권한을 확인해 주세요.":sharing?"연결됨 · Using ChatGPT plan · 사용할 모델을 선택해 주세요.":status.connected?"계정 로그인은 완료됐지만 구독 사용 권한이 없습니다. Continue with ChatGPT에서 권한을 승인해 주세요.":"ChatGPT 구독으로 승무원들의 AI 요청을 실행할 수 있습니다.");
  const picker=$("chatgpt-model"),chosen=state?.provider.provider==="chatgpt"?state.provider.model:"";
  picker.replaceChildren(node("option",sharing?"모델을 선택하세요":"연결 후 선택"));picker.firstChild.value="";
  for(const model of models){const option=node("option",model.display_name);option.value=model.slug;picker.append(option);}
  picker.value=chosen||"";
  if(status.welcome&&state?.provider.provider!=="scripted"&&!$("chatgpt-welcome").open)$("chatgpt-welcome").showModal();
  authButtons();setBusy(busy);
}
async function refreshAuth(){
  const status=await api("/api/auth/status");
  let models=[];
  if(status.enabled&&status.sharing&&!status.blocked){
    try{models=(await api("/api/auth/models")).models;}catch(error){showError(error);const latest=await api("/api/auth/status");drawAuth(latest);return;}
  }
  drawAuth(status,models);
}
async function authAction(path,body,renderGame=true){
  if(authBusy||busy)return;
  authBusy=true;setBusy(true);authButtons();showError(null);
  try{const result=await api(path,body);if(renderGame)render(result);await refreshAuth();}
  catch(error){showError(error);try{await refreshAuth();}catch(_){} }
  finally{authBusy=false;setBusy(false);authButtons();}
}
async function connectChatGPT(account){
  if(authBusy||busy)return;
  authBusy=true;setBusy(true);authButtons();showError(null);
  try{const result=await api("/api/auth/start",{account:account||null});window.location.assign(result.url);}
  catch(error){showError(error);authBusy=false;setBusy(false);authButtons();}
}
$("chatgpt-connect").addEventListener("click",()=>connectChatGPT($("auth-account").value));
$("chatgpt-add").addEventListener("click",()=>connectChatGPT(null));
$("chatgpt-logout").addEventListener("click",()=>authAction("/api/auth/logout",{}));
$("auth-account").addEventListener("change",()=>{if($("auth-account").value)connectChatGPT($("auth-account").value);});
$("chatgpt-model").addEventListener("change",()=>{if($("chatgpt-model").value)authAction("/api/auth/model",{model:$("chatgpt-model").value});});
$("chatgpt-retry").addEventListener("click",()=>authAction("/api/auth/retry",{},false));
$("chatgpt-welcome-ok").addEventListener("click",async()=>{try{await api("/api/auth/welcome",{});$("chatgpt-welcome").close();}catch(error){showError(error);}});
refreshAuth().catch(showError);
