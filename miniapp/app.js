/* Мои смены · Telegram Mini App. All personal data comes from an authenticated API. */
(() => {
  "use strict";
  const tg=window.Telegram?.WebApp;
  const initData=tg?.initData || "";
  const connected=Boolean(initData);
  const embeddedLaunch=/(?:^|[&#?])tgWebApp(?:Data|Version|Platform)=/.test(location.hash+"&"+location.search);
  const byId=id=>document.getElementById(id);
  const query=sel=>document.querySelector(sel);
  // Visual privacy is intentionally closed on every fresh launch; revealing never persists.
  let amountsVisible=false;
  const moneyPrivacyTimeoutOptions=[0,15000,30000,60000,120000,300000];
  let moneyPrivacyIdleMs=30000;
  try{const stored=localStorage.getItem("workshift_money_privacy_timeout_v1");if(stored!==null){const saved=Number(stored);if(moneyPrivacyTimeoutOptions.includes(saved))moneyPrivacyIdleMs=saved;}}catch(e){}
  const sessionLockOptions=[0,1,3,5,10,30];
  let sessionLockMinutes=5,sessionLockTimer=null,sessionLocked=false,sessionLockFocus=null;
  // The optional server-enforced second lock is OFF until the owner enables it.
  let securityEnabled=false,securityChecked=false,securityToken="",securityBusy=false;
  let biometricManagerCache=null;
  try{const stored=localStorage.getItem("workshift_session_lock_minutes_v1");if(stored!==null){const saved=Number(stored);if(sessionLockOptions.includes(saved))sessionLockMinutes=saved;}}catch(e){}
  const phonePrivacyPrefsKey="workshift_phone_privacy_v1";
  const phonePrivacyPrefs={autoVisibility:false,shake:false,orientation:false,haptics:false,shakeThreshold:14};
  try{const saved=JSON.parse(localStorage.getItem(phonePrivacyPrefsKey)||"null");if(saved&&typeof saved==="object"){
    if(typeof saved.autoVisibility==="boolean")phonePrivacyPrefs.autoVisibility=saved.autoVisibility;
    else if(typeof saved.faceDown==="boolean")phonePrivacyPrefs.autoVisibility=saved.faceDown; // migrate the old face-down preference
    for(const key of ["shake","orientation","haptics"])if(typeof saved[key]==="boolean")phonePrivacyPrefs[key]=saved[key];
    if([10,14,18,22].includes(Number(saved.shakeThreshold)))phonePrivacyPrefs.shakeThreshold=Number(saved.shakeThreshold);
  }}catch(e){}
  let phoneMotionActive=false,phoneMotionListener=null,phoneMotionProbeTimer=null,phoneMotionSawEvent=false;
  let phoneGravity=null,phonePose=null,phonePoseCandidate=null,phonePoseSince=0;
  // The bank-style gesture toggles only after a complete UP -> DOWN -> UP cycle.
  // A partial movement cannot reveal an amount, even when returning to the app.
  let phoneFlipArmed=false,phoneFlipPending=false,phoneFlipHidden=false,phoneLastFlipAt=0;
  let phoneLastShakePeak=0,phoneShakePeaks=0,phoneLastShakeAction=0;
  let phoneOrientationListener=null,phoneScreenOrientation=null,phoneOrientationMedia=null,lastPhoneOrientation="";
  let moneyPrivacyTimer=null;
  const rubFormat=new Intl.NumberFormat("ru-RU",{maximumFractionDigits:2,minimumFractionDigits:0});
  const money=n=>amountsVisible?rubFormat.format(n||0)+" ₽":"•••• ₽";
  const pad=n=>String(n).padStart(2,"0");
  const plural=(n,forms)=>{const v=Math.abs(n)%100,k=v%10;return forms[v>10&&v<20?2:k===1?0:k>=2&&k<=4?1:2];};
  const MONTHS=["январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"];
  const fmt=new Intl.DateTimeFormat("ru-RU",{timeZone:"Europe/Moscow",day:"2-digit",month:"long",year:"numeric"});
  const currentMoscow=new Intl.DateTimeFormat("sv-SE",{timeZone:"Europe/Moscow",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
  let currentMonth=currentMoscow.slice(0,7);
  let selectedDay=currentMoscow;
  let state=null;
  let toastTask=null;
  let loading=false;
  let fresh=false;
  let requestSequence=0;
  let requestController=null;
  let serverOffset=0;
  let lastSuccessfulStateAt=0;
  let lastBackgroundAttemptAt=0;
  let lastRenderedMinute=-1;
  let lastTickShiftId=null;
  const moscowClock=new Intl.DateTimeFormat("ru-RU",{timeZone:"Europe/Moscow",hour:"2-digit",minute:"2-digit"});
  let commandBusy=false;
  let pendingRequest=null;
  let actualDraft=null;
  let recoveryDraft=null;
  const newNonce=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,"0")).join("");
  const offlineEditDraftKey="workshift_edit_draft_v1";
  let longShiftThreshold=14;
  try{const saved=localStorage.getItem("workshift_long_shift_hours_v1");if(saved!==null&&[0,10,12,14,16,20].includes(Number(saved)))longShiftThreshold=Number(saved);}catch(e){}
  const writeKinds=["save_break","remove_break","undo_break_change","clock_in_at","clock_out_at","move_open_start","delete_shift","restore_shift","apply_office_estimate","save_payment","save_adjustment","void_money","restore_money","set_rate","remove_future_rate","set_default_break","create_series","cancel_series","in","out","pause_start","pause_end","correct_shift","add_shift","close_shift","set_plan","set_plan_batch","mark_day_off","restore_day_off"];
  function rememberWrite(value){
    pendingRequest=value;
    try{if(value)sessionStorage.setItem("workshift_pending_write_v1",JSON.stringify(value));else sessionStorage.removeItem("workshift_pending_write_v1");}catch(e){}
    paintWriteRecovery();
  }
  function paintWriteRecovery(){
    workspaceFieldsBusy();lockBreakControls();
    const box=byId("write-recovery");box.hidden=!pendingRequest||!connected;
    byId("write-retry").disabled=commandBusy||!state||!fresh||loading||!navigator.onLine;
    for(const id of ["actual-retry","plan-retry","bulk-retry","day-write-retry","recovery-retry"]){const inline=byId(id);if(inline){inline.hidden=!pendingRequest;inline.disabled=byId("write-retry").disabled;}}
    lockPlanControls();
    const recoveryFields=byId("recovery-fields");if(recoveryFields)recoveryFields.disabled=commandBusy||Boolean(pendingRequest);
  }
  function restoreWrite(){
    try{
      const value=JSON.parse(sessionStorage.getItem("workshift_pending_write_v1")||"null");
      if(value&&writeKinds.includes(value.action)&&/^[a-f0-9]{32}$/.test(value.nonce)&&JSON.stringify(value).length<8192)pendingRequest=value;
      else sessionStorage.removeItem("workshift_pending_write_v1");
    }catch(e){}
    paintWriteRecovery();
  }
  async function runWrite(candidate,title,message,needsConfirmation=true,retry=false){
    if(!connected){notify("Демонстрация: реальные записи не изменяются.");return false;}
    if(commandBusy)return false;
    if(!amountsVisible&&["save_payment","save_adjustment","void_money","restore_money","set_rate","remove_future_rate"].includes(candidate.action)){
      notify("Сначала покажи суммы кнопкой с глазом, чтобы проверить финансовое действие.");return false;
    }
    if(!state?.capabilities?.writes){notify("Запись временно недоступна. Обнови кабинет.");return false;}
    if(!navigator.onLine||!fresh||loading){notify("Нет актуального соединения. Обнови данные перед сохранением.");return false;}
    if(pendingRequest&&!retry){notify("Сначала проверь предыдущую запись кнопкой «Проверить сохранение».");return false;}
    commandBusy=true;paintHome();paintWriteRecovery();
    if(actualDraft)query("#actual-form fieldset").disabled=true;
    let sent=false;
    try{
      if(needsConfirmation&&!await modal(title,message))return false;
      // Cancel an older read before changing state; it must not overwrite the result.
      requestController?.abort();requestSequence++;loading=false;
      rememberWrite(candidate);sent=true;
      const result=await post("/mini/api/action",candidate);
      rememberWrite(null);resetAnnual();
      if(candidate.action==="set_plan")planDraft=null;
      if(candidate.action==="set_plan_batch")bulkDraft=null;
      if(["mark_day_off","restore_day_off"].includes(candidate.action)){planDraft=null;actualDraft=null;}
      if(["correct_shift","add_shift","close_shift"].includes(candidate.action)){actualDraft=null;clearOfflineEditDraft(candidate);renderDrafts();}
      if(["clock_in_at","clock_out_at","move_open_start"].includes(candidate.action)){clearOfflineEditDraft(candidate);renderDrafts();
        recoveryDraft=null;await closeDay(true);
        if(result.day){currentMonth=result.day.slice(0,7);selectedDay=result.day;}
      }
      fresh=false;
      const updated=await reload();
      if(breakActions.includes(candidate.action)&&breakPanel){breakPanel.dirty=false;breakPanel.feedback="Saved";await loadBreaks();}
      await workspaceAfterWrite(candidate);
      if(!breakActions.includes(candidate.action)||!breakPanel)notify(updated?"Сохранено. Данные обновлены.":"Запись сохранена. Обнови экран, чтобы увидеть результат.","success");
      return result;
    }catch(error){
      // 400 is a definite validation failure. Network/5xx/auth retries can be
      // uncertain after a prior attempt, so retain exactly the same operation.
      if(sent&&[400,409,422].includes(error.status))rememberWrite(null);
      const suffix=pendingRequest?" Результат пока не подтверждён. Нажми «Проверить сохранение» — повторной записи не будет.":" Ничего не изменено этим запросом.";
      for(const id of ["actual-error","plan-error","bulk-error","recovery-error","break-error"]){if(byId(id))text(id,error.message+suffix);}
      if(!byId("workspace-sheet").hidden)workspaceError(error.message+suffix);
      notify(error.message+suffix,"error");
      if(error.status===403){fresh=false;await reload();}
      return false;
    }finally{
      commandBusy=false;
      if(state)paintHome();paintWriteRecovery();
      const fields=query("#actual-form fieldset");if(fields)fields.disabled=Boolean(pendingRequest);
    }
  }
  function directPlans(){return Boolean(connected&&state?.capabilities?.writes&&state?.plan_editor?.direct_writes);}
  function planLabel(value){return !value||value.status==="unset"?"Не задан":value.status==="off"?"Выходной":`${value.start}–${value.end}${value.end<value.start?" (+1 день)":""}`;}
  function writeRetry(parent,id){
    const button=append(parent,"button","small-switch inline-write-retry","Проверить сохранение");
    button.id=id;button.type="button";button.hidden=!pendingRequest;button.onclick=retryWrite;
  }
  function lockPlanControls(){
    const locked=commandBusy||Boolean(pendingRequest);
    for(const id of ["plan-fields","bulk-fields"]){const field=byId(id);if(field)field.disabled=locked;}
    document.querySelectorAll('.day-off-action').forEach(button=>button.disabled=locked);
  }
  async function savePlanHere(draft){
    const row=dayRows().find(r=>r.day===draft.day);
    const candidate={action:"set_plan",nonce:newNonce(),day:draft.day,status:draft.status,
      start:draft.status==="shift"?draft.start:null,end:draft.status==="shift"?draft.end:null,expected_revision:draft.revision};
    await runWrite(candidate,"Сохранить план на "+dateLabel(draft.day)+"?",
      "Было: "+planLabel(row?.plan)+"\nБудет: "+planLabel(draft)+"\n\nМеняется только расписание. Реальные часы и начисления сохранятся.");
  }
  async function saveBulkHere(draft){
    const days=[...draft.days].sort(),rows=dayRows();
    const before=days.map(day=>dateLabel(day)+": "+planLabel(rows.find(r=>r.day===day)?.plan)).join("\n");
    await runWrite({action:"set_plan_batch",nonce:newNonce(),month:draft.month,days,status:draft.status,
      start:draft.status==="shift"?draft.start:null,end:draft.status==="shift"?draft.end:null,expected_revision:draft.revision},
      "Изменить план для "+days.length+" дат?","Будет для всех: "+planLabel(draft)+"\n\nБыло:\n"+before+"\n\nСохранятся все даты вместе или ни одна. Фактические часы и деньги не меняются.");
  }
  async function changeActualOff(row){
    if(!connected){notify("Демонстрация: фактический выходной не сохраняется.");return;}
    if(!state?.capabilities?.actual_days_off){notify("Обнови кабинет: действие пока недоступно.");return;}
    const restoring=Boolean(row.actual_off),hasRecords=row.records.length>0;
    const revision=state?.actual_editor?.revisions?.[row.day];
    if(!/^[a-f0-9]{64}$/.test(revision||"")){notify("Обнови день перед изменением факта.");return;}
    const title=restoring?(row.archived_shifts?"Восстановить прежние смены?":"Снять отметку выходного?"):hasRecords?"Заменить записанную работу выходным?":"Отметить фактический выходной?";
    const message=restoring?(row.archived_shifts?"Прежние смены и перерывы вернутся из архива. Начисления будут пересчитаны. При пересечении с новой работой восстановление не выполнится.":"Останется день без фактических отметок. Расписание не изменится."):hasRecords?
      "За "+dateLabel(row.day)+" из расчёта будут исключены "+formatHours(row.hours)+" и "+money(row.earnings)+".\n\nСмены и перерывы останутся в архиве. Их можно восстановить из этой карточки. План, офисный табель и поступления не изменятся.":
      "За "+dateLabel(row.day)+" будет записано: фактически не работал.\n\nЭто отдельная отметка; план не меняется, рабочие часы не добавляются.";
    await runWrite({action:restoring?"restore_day_off":"mark_day_off",nonce:newNonce(),day:row.day,
      expected_revision:revision,replace_shifts:!restoring&&hasRecords},title,message);
  }
  function paintActualOffActions(facts,row){
    if(row.day>moscowDay()||row.open||(connected&&!state.capabilities?.actual_days_off))return;
    const button=append(facts,"button",row.records.length?"text-button day-off-action":"secondary-action day-off-action",
      row.actual_off?(row.archived_shifts?"Восстановить прежнюю работу":"Снять отметку выходного"):row.records.length?"Вместо работы — фактически выходной":"Фактически выходной");
    button.type="button";button.id="actual-off-action";button.onclick=()=>changeActualOff(row);
    writeRetry(facts,"day-write-retry");lockPlanControls();
  }

  async function retryWrite(){
    if(!pendingRequest)return;
    await runWrite(pendingRequest,"Проверить сохранение","Повторим тот же запрос. Уже сохранённое действие не выполнится ещё раз.",true,true);
  }
  function actualSummary(item){
    return item?`${item.start}–${item.end||"смена открыта"}${item.next_day?" (+1 день)":""}, перерыв ${item.break_minutes} мин`:"Записи нет";
  }
  function openActualEditor(item,row,resume=null){
    if(toastTask)clearTimeout(toastTask);byId("toast").hidden=true;
    if(connected&&!state?.capabilities?.actual_edits){
      item?.end?openChatDestination("fix",{day:row.day,id:item.id}):openChatDestination("day",row.day);return;
    }
    if(commandBusy||pendingRequest){notify("Сначала проверь предыдущее сохранение.");return;}
    const mode=!item?"add_shift":item.end?"correct_shift":"close_shift";
    const currentClock=new Intl.DateTimeFormat("ru-RU",{timeZone:"Europe/Moscow",hour:"2-digit",minute:"2-digit"}).format(new Date(serverNow()));
    actualDraft=resume?{...resume,action:resume.action,day:row.day,shift_id:item?.id||null,
      expected_revision:item?.revision||state?.actual_editor?.revisions?.[row.day]||resume.expected_revision,
      before:actualSummary(item),tracked:Boolean(item?.tracked_breaks),dirty:false,offline_saved:true}:
      {action:mode,day:row.day,shift_id:item?.id||null,
       expected_revision:item?.revision||state?.actual_editor?.revisions?.[row.day]||"",
       start:item?.start||"09:00",end:item?.end||(item?currentClock:"18:00"),
       break_minutes:item?.break_minutes??state?.actual_editor?.default_break_minutes??30,
       before:actualSummary(item),reason:"",dirty:false,tracked:Boolean(item?.tracked_breaks)};
    renderActualEditor();
    query(".day-sheet").scrollTop=0;byId("actual-start").focus({preventScroll:true});
  }
  function renderActualEditor(){
    const draft=actualDraft;if(!draft)return;
    const box=byId("day-details");box.replaceChildren();
    const section=append(box,"section","actual-editor");
    append(section,"h3","",draft.action==="add_shift"?"Добавить фактическую смену":draft.action==="close_shift"?"Уточнить и сохранить уход":"Исправить фактическую смену");
    append(section,"p","plan-note","Это реальные часы, не план. Изменение времени и перерыва повлияет на начисление. Расписание и поступления не изменятся.");
    append(section,"p","actual-before","Было: "+draft.before);
    let offlineNote=draft.offline_saved?append(section,"p","plan-history-note","Черновик сохранён на этом устройстве. Он ещё не отправлен и не влияет на часы или начисления. Проверь значения перед отправкой."):null;
    const form=append(section,"form");form.id="actual-form";
    const fields=append(form,"fieldset");fields.disabled=Boolean(pendingRequest);
    const clocks=append(fields,"div","plan-times");
    function input(parent,label,id,type,value){const caption=append(parent,"label","",label),el=append(caption,"input");el.id=id;el.type=type;el.value=value;return el;}
    const start=input(clocks,"Приход","actual-start","time",draft.start),end=input(clocks,"Уход","actual-end","time",draft.end);
    start.required=end.required=true;start.step=end.step="60";start.readOnly=draft.action==="close_shift";
    const minutes=input(fields,"Перерыв, минут","actual-break","number",draft.break_minutes);minutes.min="0";minutes.max="600";minutes.step="1";minutes.required=true;minutes.inputMode="numeric";
    const preview=append(fields,"p","actual-preview");preview.id="actual-preview";preview.setAttribute("aria-live","polite");
    if(draft.tracked)append(fields,"p","plan-note","Отмеченные перерывы останутся в истории. Изменённая сумма минут будет записана как ручное уточнение.");
    const reason=input(fields,"Комментарий (необязательно)","actual-reason","text",draft.reason);reason.maxLength=160;reason.autocomplete="off";
    const error=append(form,"p","plan-error");error.id="actual-error";error.setAttribute("role","alert");
    const retry=append(form,"button","secondary-action actual-retry","Проверить сохранение");retry.id="actual-retry";retry.type="button";retry.hidden=!pendingRequest;retry.onclick=retryWrite;
    const actions=append(fields,"div","actual-actions"),save=append(actions,"button","main-action",connected?"Сохранить изменения":"Проверить выбор"),cancel=append(actions,"button","secondary-action","Отмена");
    save.type="submit";save.id="actual-save";cancel.type="button";cancel.id="actual-cancel";
    cancel.onclick=()=>{actualDraft=null;paintDay();query(".day-sheet").scrollTop=0;byId("day-close").focus();};
    const refresh=()=>{
      const toMins=t=>/^\d{2}:\d{2}$/.test(t)?Number(t.slice(0,2))*60+Number(t.slice(3)):NaN;
      const a=toMins(draft.start),b=toMins(draft.end),gross=(b-a+1440)%1440,br=Number(draft.break_minutes);
      const valid=Number.isFinite(gross)&&gross>0&&Number.isInteger(br)&&br>=0&&br<=600&&br<gross;
      preview.textContent=valid?`${draft.start}–${draft.end}${b<a?" · уход на следующий день":""}. Оплачиваемое время по минутам: ${formatHours((gross-br)/60)}.`:"Проверь время и перерыв: перерыв должен быть короче смены.";
      return valid;
    };
    const markEdited=()=>{draft.dirty=true;if(draft.offline_saved){draft.offline_saved=false;if(offlineNote)offlineNote.textContent="Ты изменил сохранённый черновик. Новые значения ещё не сохранены.";}};
    start.oninput=()=>{draft.start=start.value;markEdited();refresh();};
    end.oninput=()=>{draft.end=end.value;markEdited();refresh();};
    minutes.oninput=()=>{draft.break_minutes=minutes.value;markEdited();refresh();};
    reason.oninput=()=>{draft.reason=reason.value;markEdited();};
    form.onsubmit=async event=>{
      event.preventDefault();error.textContent="";
      if(!form.reportValidity()||!refresh()){error.textContent="Проверь время и перерыв.";return;}
      if(!connected){error.textContent="Это демонстрация: ничего не сохранено.";return;}
      if(!state?.capabilities?.actual_edits){error.textContent="Сохранение сейчас недоступно. Обнови кабинет.";return;}
      const latest=dayRows().find(r=>r.day===draft.day);
      const revision=draft.shift_id?latest?.records.find(r=>r.id===draft.shift_id)?.revision:state.actual_editor?.revisions?.[draft.day];
      if(!/^[a-f0-9]{64}$/.test(draft.expected_revision)||revision!==draft.expected_revision){error.textContent="Запись изменилась. Отмени редактирование и открой смену заново.";return;}
      const candidate={action:draft.action,nonce:newNonce(),shift_id:draft.shift_id,day:draft.day,start:draft.start,end:draft.end,break_minutes:Number(draft.break_minutes),expected_revision:draft.expected_revision,reason:draft.reason.trim()};
      if(!navigator.onLine||!fresh||loading){
        if(!saveOfflineEditDraft({...draft,kind:"actual"})){error.textContent="На устройстве уже есть черновик для другой смены. Сначала удали его в разделе «Ещё».";return;}
        draft.offline_saved=true;draft.dirty=false;error.textContent="Черновик сохранён на устройстве. Не отправлено и не включено в расчёт. После восстановления связи проверь его в разделе «Ещё».";
        if(!offlineNote)offlineNote=append(section,"p","plan-history-note");offlineNote.textContent="Черновик сохранён на этом устройстве. Он ещё не отправлен и не влияет на часы или начисления. Проверь значения перед отправкой.";
        renderDrafts();notify("Черновик исправления сохранён. Рабочие часы пока не изменены.","warning");return;
      }
      await runWrite(candidate,"Сохранить фактическое время?",`${draft.day}\nБыло: ${draft.before}\nБудет: ${draft.start}–${draft.end}${draft.end<draft.start?" (+1 день)":""}, перерыв ${candidate.break_minutes} мин.\nНачисление пересчитается по ставке этой даты. Изменение останется в журнале.`,true);
    };
    refresh();
  }

  let calendarMode="calendar",tableFilter="relevant",tableSort="date-asc",tableSearch="",chartMetric="money",chartInstance=null,chartPointerHandler=null,dayReturnFocus=null;
  const serverNow=()=>Date.now()+serverOffset;
  const moscowDay=()=>new Intl.DateTimeFormat("sv-SE",{timeZone:"Europe/Moscow",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date(serverNow()));
  byId("demo-banner").hidden=connected;
  byId("shift-action").disabled=connected;


  function dateLabel(day){return new Intl.DateTimeFormat("ru-RU",{timeZone:"UTC",weekday:"short",day:"numeric",month:"short"}).format(new Date(day+"T12:00:00Z"));}
  function demoAgenda(){
    const today=moscowDay(),base=new Date(today+"T12:00:00Z");
    const days=Array.from({length:7},(_,i)=>{
      const at=new Date(base);at.setUTCDate(base.getUTCDate()+i);const day=at.toISOString().slice(0,10);
      const kind=[null,"shift","off",null,"shift","off",null][i];
      const plan=kind?{day,status:kind,start:kind==="shift"?"16:00":null,end:kind==="shift"?"02:00":null,source:"manual"}:null;
      return {day,plan,status:kind==="shift"?"planned":kind==="off"?"off":"unspecified",start:plan?.start,end:plan?.end,next_day:kind==="shift",has_actual:false};
    });
    return {today,days,current_plan:null,next_shift:days[1],search_days:90,counts:{work:2,off:2,unspecified:3}};
  }
  function demoState(month){
    const entries=[
      {day:month+"-03",start:"16:02",end:"02:11",next_day:true,break_minutes:30,hours:9.65,earnings:2702,source:"manual"},
      {day:month+"-06",start:"16:14",end:"01:55",next_day:true,break_minutes:30,hours:9+11/60,earnings:2571.33,source:"button"},
      {day:month+"-10",start:"16:09",end:"02:10",next_day:true,break_minutes:30,hours:9+31/60,earnings:2664.67,source:"button"},
      {day:month+"-12",start:"17:05",end:"02:00",next_day:true,break_minutes:30,hours:8+25/60,earnings:2356.67,source:"button"}
    ];
    return {
      month, agenda:demoAgenda(), now:new Date().toISOString(),
      summary:{hours:36+46/60,shifts:4,earned:10294.67,total:10694.67,received:6000,
        additional:{bonus:400,allowance:0,reimbursement:0,deduction:0,net_adjustment:400}},
      periods:[{period:1,hours:36+46/60,earnings:10294.67,adjustments:400,total:10694.67,received:6000,shifts:4},{period:2,hours:0,earnings:0,adjustments:0,total:0,received:0,shifts:0}],
      office_status:{confirmed_days:0,complete:false},
      rates:[{rate:280,hours:36+46/60,earned:10294.67,shifts:4}],
      forecast:{future_shifts:0,future_hours:0,future_pay:0,assumed_break_minutes:30},
      active:null,break:{active:false,on_break:false,tracked:false,minutes:0},
      shifts:entries,plan:[
        {day:month+"-18",status:"shift",start:"16:00",end:"02:00",source:"manual"},
        {day:month+"-19",status:"off",start:null,end:null,source:"manual"},
        {day:month+"-21",status:"shift",start:"16:00",end:"02:00",source:"import"}
      ].filter(p=>!demoAgenda().days.some(d=>d.day===p.day)).concat(demoAgenda().days.filter(d=>d.plan&&d.day.startsWith(month)).map(d=>d.plan)),office:{},issues:[],weeks:[
        {label:"12.10",hours:18},{label:"19.10",hours:27},
        {label:"26.10",hours:20},{label:"02.11",hours:36},
        {label:"09.11",hours:29},{label:"16.11",hours:17}
      ],capabilities:{writes:false,offline_submission:false}
    };
  }
  function text(id,value){const el=byId(id);if(el)el.textContent=String(value);}
  function append(parent,tag,cl,label){
    const el=document.createElement(tag);
    if(cl)el.className=cl;
    if(label!==undefined)el.textContent=String(label);
    parent.appendChild(el);
    return el;
  }
  function notify(message,kind="info"){
    const el=byId("toast");el.textContent=message;el.dataset.kind=kind;el.hidden=false;
    if(toastTask)clearTimeout(toastTask);
    toastTask=setTimeout(()=>el.hidden=true,4500);
    if(kind==="success"||kind==="error")tg?.HapticFeedback?.notificationOccurred?.(kind);
  }
  function setStatus(ok,label){
    const el=byId("connection");
    el.className="connection "+(ok?"ok":"error");
    el.lastChild.textContent=label;el.setAttribute("aria-label",label);el.title=label;
  }
  function setTheme(name){
    document.body.dataset.theme=name==="light"?"light":"dark";
    byId("theme-switch").textContent=name==="light"?"☾":"☼";
    try{localStorage.setItem("workshift_theme",name);}catch(e){}
    try{tg?.setHeaderColor?.(name==="light"?"#f3f5f9":"#0c1424");tg?.setBackgroundColor?.(name==="light"?"#f3f5f9":"#0c1424");}catch(e){}
    if(state)requestAnimationFrame(drawChart);
  }
  function navigate(page){
    document.body.dataset.page=page;
    query(".skip-link").href="#page-"+page;
    document.querySelectorAll(".page").forEach(e=>e.classList.toggle("shown",e.id==="page-"+page));
    document.querySelectorAll("[data-nav].nav-item").forEach(e=>{
      const active=e.dataset.nav===page;
      e.classList.toggle("active",active);
      if(active)e.setAttribute("aria-current","page");
      else e.removeAttribute("aria-current");
    });
    window.scrollTo({top:0,behavior:"instant"});
    tg?.HapticFeedback?.selectionChanged?.();
    if(page==="salary")requestAnimationFrame(drawChart);
  }
  async function post(path,payload,signal){
    const securityOperation=(path==="/mini/api/security"||
      (path==="/mini/api/workspace"&&payload?.view==="security"));
    const controller=new AbortController();
    const relay=()=>controller.abort();
    if(signal?.aborted)relay();
    signal?.addEventListener("abort",relay,{once:true});
    let expired=false;
    const timeout=setTimeout(()=>{expired=true;controller.abort();},12000);
    try{
      const response=await fetch(path,{
        method:"POST",cache:"no-store",credentials:"omit",signal:controller.signal,
        headers:{"Content-Type":"application/json","X-Telegram-Init-Data":initData,
                 ...(securityToken?{"X-Workshift-Unlock":securityToken}:{})},
        body:JSON.stringify(payload)
      });
      let result=null;
      try{result=await response.json();}catch(e){
        // A gateway may send HTML; an unreadable success is uncertain, not a rejection.
        if(response.ok)throw Error("Не удалось прочитать ответ сервера. Обнови данные; сохранение можно проверить повторно.");
      }
      if(!response.ok){
        let message=typeof result?.error==="string"?result.error:"Не удалось выполнить запрос.";
        if(response.status>=500)message="Сервер временно недоступен. Повтори после восстановления связи.";
        if(!securityOperation&&(response.status===403||response.status===401))message="Сессия Telegram истекла или доступ закрыт. Открой кабинет заново.";
        if(response.status===423){
          message="Требуется разблокировка PIN или биометрией.";
          if(securityEnabled){
            securityToken="";
            lockSession("expired");
          }
        }
        if(response.status===429)message="Слишком много попыток. Повтори позже.";
        if(securityOperation){
          const names={invalid_credential:"Неверный PIN, код или биометрический ключ.",weak_pin:"Выбери более надёжный PIN из 6 цифр.",
            temporarily_locked:"Попытки временно заблокированы. Повтори позже.",security_locked:"Сначала разблокируй кабинет.",
            already_enabled:"PIN уже включён.",not_enabled:"PIN пока не настроен.",
            bad_biometric:"Устройство не предоставило биометрический ключ."};
          message=names[result?.error]||message;
        }
        const error=Error(message);error.status=response.status;throw error;
      }
      if(!result||typeof result!=="object"||Array.isArray(result))throw Error("Ответ сервера неполный. Обнови данные.");
      return result;
    }catch(error){
      if(expired)throw Error("Сервер не ответил вовремя. Проверь соединение.");
      throw error;
    }finally{
      clearTimeout(timeout);signal?.removeEventListener("abort",relay);
    }
  }
  function securityPost(payload){
    // The reverse proxy already allows this owner-verified workspace endpoint;
    // security actions are dispatched before opening any business database.
    return post("/mini/api/workspace",{view:"security",...payload});
  }
  async function reload(){
    if(sessionLocked||(connected&&securityEnabled&&!securityToken))return false;
    if(embeddedLaunch&&!initData){
      state=null;query(".shell").classList.add("auth-blocked");byId("demo-banner").hidden=true;
      text("connection-error","Не удалось получить подтверждённую сессию Telegram. Закрой кабинет и открой его через бота заново.");
      byId("connection-banner").hidden=false;setStatus(false,"Нет сессии");return;
    }
    if(!connected){
      state=demoState(currentMonth);
      byId("demo-banner").hidden=false;
      setStatus(false,"Демонстрация");
      paint();return true;
    }
    const sequence=++requestSequence;
    requestController?.abort();
    requestController=new AbortController();
    const requestedMonth=currentMonth;
    loading=true;fresh=false;
    try{
      const result=await post("/mini/api/state",{month:requestedMonth},requestController.signal);
      if(sequence!==requestSequence)return;
      if(result.month!==requestedMonth)throw Error("Месяц ответа не совпал. Обнови кабинет.");
      lastSuccessfulStateAt=Date.now();
      if(state?.month!==result.month)resetAnnual();
      state=result;serverOffset=Date.parse(result.now)-Date.now();
      if(!Number.isFinite(serverOffset))serverOffset=0;
      fresh=true;
      query(".shell").classList.remove("auth-blocked");
      byId("connection-banner").hidden=true;
      byId("demo-banner").hidden=true;
      setStatus(true,"Подключено");paint();return true;
    }catch(error){
      if(sequence!==requestSequence)return;
      if(error.status===423&&securityEnabled){securityToken="";lockSession("expired");return false;}
      const denied=error.status===403;
      if(!denied&&state&&state.month!==currentMonth){currentMonth=state.month;selectedDay=currentMonth+"-01";paint();}
      if(denied){resetAnnual();closeWorkspace(true);closeDay(true);state=null;bulkDraft=null;paintBulk();byId("day-details").replaceChildren();chartInstance?.clear();query(".shell").classList.add("auth-blocked");}
      setStatus(false,denied?"Нет доступа":"Нет соединения");
      text("connection-error",error.message+(state?" Показаны последние загруженные данные.":""));
      byId("connection-banner").hidden=false;
      byId("offline-banner").hidden=navigator.onLine;
    }finally{
      if(sequence===requestSequence){loading=false;if(state)paintHome();paintSync();paintWriteRecovery();}
    }
  }
  function backgroundRefresh(minAgeMs){
    // Preserve manual refresh and explicit month changes. Only deduplicate background reads.
    if(!connected||sessionLocked||(securityEnabled&&!securityToken)||document.hidden||commandBusy||loading||pendingRequest||!navigator.onLine||
       query(".shell").classList.contains("auth-blocked"))return;
    const now=Date.now();
    if(fresh&&lastSuccessfulStateAt&&now-lastSuccessfulStateAt<minAgeMs)return;
    if(now-lastBackgroundAttemptAt<15000)return; // An unavailable gateway must not be hammered.
    lastBackgroundAttemptAt=now;
    reload();
  }
  function elapsed(){
    if(!state?.active)return "00:00:00";
    const start=Date.parse(state.active.start);
    const seconds=Math.max(0,Math.floor((serverNow()-start)/1000));
    return [Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60]
      .map(pad).join(":");
  }
  function tick(){
    const active=state?.active;
    // The hidden timer used to write to the DOM every second even outside a shift.
    if(!active){
      lastTickShiftId=null;lastRenderedMinute=-1;
      byId("shift-duration-warning").hidden=true;
      return;
    }
    if(document.hidden)return;
    if(lastTickShiftId!==active.id){lastTickShiftId=active.id;lastRenderedMinute=-1;}
    text("live-time",elapsed()); // The running duration stays accurate to the second.
    const time=serverNow(),minute=Math.floor(time/60000);
    if(minute!==lastRenderedMinute){
      lastRenderedMinute=minute;
      text("shift-now",moscowClock.format(new Date(time)));
      paintLongShiftWarning();
    }
    if(state?.break?.on_break){
      const running=Math.max(0,Math.ceil((time-Date.parse(state.break.break_started_utc))/60000));
      const value=((state.break.minutes||0)+running)+" мин · идёт";
      if(byId("shift-break").textContent!==value)text("shift-break",value);
    }
  }
  function paintLongShiftWarning(){
    const panel=byId("shift-duration-warning"),active=state?.active;
    if(!panel||!active||!longShiftThreshold){if(panel)panel.hidden=true;return;}
    const seconds=Math.max(0,Math.floor((serverNow()-Date.parse(active.start))/1000));
    if(seconds<longShiftThreshold*3600){panel.hidden=true;return;}
    const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60);
    const message="Смена длится "+hours+" ч "+pad(minutes)+" мин. Проверь, не забыл ли отметить уход. Автоматически смена не закрывается.";
    if(panel.textContent!==message)panel.textContent=message;
    panel.hidden=false;
  }
  function formatHours(value){
    const minutes=Math.max(0,Math.round((value||0)*60));
    return Math.floor(minutes/60)+" ч "+pad(minutes%60)+" мин";
  }
  function paintHome(){
    const active=state?.active,br=state?.break||{};
    byId("live-time").hidden=!active;byId("idle-title").hidden=Boolean(active);query(".shift-meta").hidden=!active;
    const label=byId("shift-label");
    label.className="state-pill "+(!active?"off":br.on_break?"break":"");
    label.innerHTML="";append(label,"span","dot");
    append(label,"span","",!active?"Сейчас":br.on_break?"Идёт перерыв":"На работе");
    text("shift-subtitle",!active?(connected&&!state.capabilities?.writes?"Отметь приход в чате — время появится здесь.":"Отметь приход — стандартный перерыв учтём автоматически."):
         br.on_break?"Старый открытый перерыв нужно завершить перед уходом.":
         "Время фиксируется сервером · Москва. Перерыв учтён по настройке.");
    text("shift-start",active?new Intl.DateTimeFormat("ru-RU",{timeZone:"Europe/Moscow",hour:"2-digit",minute:"2-digit"}).format(new Date(active.start)):"—");
    text("shift-break",!active?"—":br.tracked?(br.minutes+" мин · по отметкам"):(br.minutes??30)+" мин · автоматически");
    const btn=byId("shift-action"), pause=byId("break-action");
    const readOnly=connected&&!state.capabilities?.writes;
    byId("view-only-banner").hidden=!readOnly;
    btn.textContent=pendingRequest?"Проверить сохранение":readOnly?"Запись временно недоступна":active?"Ушёл":"Пришёл";
    btn.classList.toggle("leave",Boolean(active));
    btn.disabled=commandBusy||(!readOnly&&!pendingRequest&&Boolean(br.on_break));
    pause.disabled=commandBusy||Boolean(pendingRequest);
    // No pause-start control in the normal flow. Keep finish only for
    // legacy/manual intervals that were already started before this update.
    pause.hidden=!active||!br.on_break;
    const correction=byId("active-correct");correction.hidden=!active||!state.capabilities?.actual_edits;
    correction.disabled=commandBusy||Boolean(pendingRequest);
    correction.textContent="Исправить текущую смену";
    correction.onclick=async()=>{
      const day=new Intl.DateTimeFormat("sv-SE",{timeZone:"Europe/Moscow",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date(state.active.start));
      await openCalendarDay(day);
      const row=dayRows().find(r=>r.day===day),item=row?.records.find(r=>r.id===state.active?.id);
      if(item&&!byId("day-sheet").hidden&&!state.capabilities?.attendance_recovery)openActualEditor(item,row);
    };
    const earlier=byId("attendance-earlier");earlier.hidden=!state.capabilities?.attendance_recovery;
    earlier.disabled=commandBusy||Boolean(pendingRequest)||Boolean(br.on_break);
    earlier.textContent=active?"Ушёл раньше — указать время":"Пришёл раньше — указать время";
    earlier.onclick=()=>openRecovery(active?"clock_out_at":"clock_in_at",null,true);
    byId("shift-correction-tools").hidden=earlier.hidden&&correction.hidden;
    pause.textContent="Завершить ранее начатый перерыв";
    const sum=state.summary;
    text("home-hours",formatHours(sum.hours));
    byId("home-hours").setAttribute("aria-label",formatHours(sum.hours));
    text("home-shifts",sum.shifts+" "+plural(sum.shifts,["закрытая смена","закрытые смены","закрытых смен"]));
    text("home-money",money(sum.earned));
    text("month-title-home",MONTHS[Number(currentMonth.slice(5))-1]+" "+currentMonth.slice(0,4)+" ›");
    text("today-date",new Intl.DateTimeFormat("ru-RU",{timeZone:"Europe/Moscow",day:"numeric",month:"long"}).format(new Date(serverNow())));
    byId("alerts-preview").hidden=!state.issues.length;
    text("alerts-title",state.issues.length?"Нужно проверить":"Отметки в порядке");
    text("alerts-summary",state.issues.length?state.issues.length+" записей требуют внимания":"Явных ошибок не найдено");
    query("#alerts-preview .side-icon").textContent=state.issues.length?"!":"✓";
    const list=byId("recent-shifts");list.replaceChildren();
    const items=[...state.shifts].sort((a,b)=>b.day.localeCompare(a.day)).slice(0,2);
    if(!items.length)append(list,"div","empty-card","В этом месяце пока нет закрытых смен.");
    items.forEach(s=>{
      const line=append(list,"button","shift-row");line.onclick=()=>showDay(s.day);line.setAttribute("aria-label","Открыть смену "+s.day);
      const left=append(line,"div");
      append(left,"strong","",s.day.slice(8)+"."+s.day.slice(5,7));
      append(left,"small","",(s.start||"—")+"–"+(s.end||"в работе")+
             (s.next_day?" · +1 день":""));
      append(line,"div","money",s.end?money(s.earnings):"В работе");
    });
    paintLongShiftWarning();
  }
  function dkey(day){return currentMonth+"-"+pad(day);}
  function changeMonth(step){
    const [y,m]=currentMonth.split("-").map(Number);
    const date=new Date(Date.UTC(y,m-1+step,1));
    if(date.getUTCFullYear()<2000||date.getUTCFullYear()>2100){notify("Календарь доступен с 2000 по 2100 год.");return;}
    currentMonth=date.getUTCFullYear()+"-"+pad(date.getUTCMonth()+1);
    selectedDay=currentMonth+"-01";
    reload();
  }
  function icsText(value){
    return String(value).replace(/\\/g,"\\\\").replace(/\r\n|\r|\n/g,"\\n").replace(/,/g,"\\,").replace(/;/g,"\\;");
  }
  function foldIcsLine(value){
    const encoder=new TextEncoder();let output="",current="",octets=0;
    for(const character of String(value)){
      const size=encoder.encode(character).length;
      if(octets+size>75){output+=current+"\r\n";current=" "+character;octets=1+size;}
      else{current+=character;octets+=size;}
    }
    return output+current;
  }
  function moscowUtcStamp(day,clock){
    const [year,month,date]=day.split("-").map(Number),[hour,minute]=clock.split(":").map(Number);
    const target=Date.UTC(year,month-1,date,hour,minute);
    let instant=target;
    const format=new Intl.DateTimeFormat("en-GB",{timeZone:"Europe/Moscow",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hourCycle:"h23"});
    for(let i=0;i<3;i++){
      const parts={};
      for(const part of format.formatToParts(new Date(instant)))if(part.type!=="literal")parts[part.type]=part.value;
      const observed=Date.UTC(Number(parts.year),Number(parts.month)-1,Number(parts.day),Number(parts.hour),Number(parts.minute));
      instant+=target-observed;
    }
    return new Date(instant).toISOString().replace(/[-:]/g,"").replace(/\.\d{3}Z$/,"Z");
  }
  async function exportCalendar(){
    if(!state||loading||commandBusy||bulkDraft)return;
    const clock=/^(?:[01]\d|2[0-3]):[0-5]\d$/;
    const plans=(Array.isArray(state.plan)?state.plan:[])
      .filter(p=>p?.status==="shift"&&/^\d{4}-\d{2}-\d{2}$/.test(p.day||"")&&clock.test(p.start||"")&&clock.test(p.end||""))
      .sort((a,b)=>a.day.localeCompare(b.day));
    if(!plans.length){notify("В выбранном месяце нет плановых смен для экспорта.");return;}
    const stamp=new Date().toISOString().replace(/[-:]/g,"").replace(/\.\d{3}Z$/,"Z");
    const lines=[
      "BEGIN:VCALENDAR","PRODID:-//Moi Smeny//Shift Calendar//RU","VERSION:2.0",
      "CALSCALE:GREGORIAN","METHOD:PUBLISH",
      "X-WR-CALNAME:"+icsText("Мои смены — план "+currentMonth),
      "X-WR-TIMEZONE:Europe/Moscow"
    ];
    for(const plan of plans){
      let endDay=plan.day;
      if(plan.end<=plan.start){
        const next=new Date(plan.day+"T12:00:00Z");
        next.setUTCDate(next.getUTCDate()+1);
        endDay=next.toISOString().slice(0,10);
      }
      lines.push(
        "BEGIN:VEVENT",
        "UID:"+plan.day+"-planned@moi-smeny.local",
        "DTSTAMP:"+stamp,
        "DTSTART:"+moscowUtcStamp(plan.day,plan.start),
        "DTEND:"+moscowUtcStamp(endDay,plan.end),
        "SUMMARY:"+icsText("Рабочая смена (план)"),
        "DESCRIPTION:"+icsText("Плановое время по Москве, не фактическая отметка. Календарь покажет его в часовом поясе устройства."),
        "STATUS:TENTATIVE","TRANSP:OPAQUE","END:VEVENT"
      );
    }
    lines.push("END:VCALENDAR");
    const mime="text/calendar;charset=utf-8";
    const content=lines.map(foldIcsLine).join("\r\n")+"\r\n";
    const filename="moi-smeny-"+currentMonth+".ics";
    const blob=new Blob([content],{type:mime});
    if(typeof File==="function"&&typeof navigator.share==="function"){
      const file=new File([content],filename,{type:mime});
      try{
        if(typeof navigator.canShare!=="function"||navigator.canShare({files:[file]})){
          await navigator.share({files:[file],title:"График смен",text:"Плановые смены — "+currentMonth});
          notify("График передан в меню «Поделиться»: "+plans.length+" "+plural(plans.length,["смена","смены","смен"])+".");
          return;
        }
      }catch(error){if(error?.name==="AbortError")return;}
    }
    const url=URL.createObjectURL(blob),link=document.createElement("a");
    link.href=url;link.download=filename;link.hidden=true;
    document.body.append(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
    notify("Файл .ics подготовлен: "+plans.length+" "+plural(plans.length,["плановая смена","плановые смены","плановых смен"])+". Импортируй его в календарь.");
  }
  function paintCalendar(){
    paintBulk();
    const [y,m]=currentMonth.split("-").map(Number);
    text("calendar-month",MONTHS[m-1]+" "+y);
    const counts=byId("calendar-counts");counts.replaceChildren();
    for(const [label,value] of [["Завершено смен",state.summary.shifts],["Рабочих по плану",state.plan.filter(p=>p.status==="shift").length],["Выходных",state.plan.filter(p=>p.status==="off").length]]){const chip=append(counts,"span");chip.append(document.createTextNode(label+": "));append(chip,"strong","",value);}
    byId("month-jump").value=currentMonth;
    const grid=byId("calendar-grid"),focused=grid.contains(document.activeElement)?document.activeElement.dataset.day:null;grid.replaceChildren();
    const offset=(new Date(Date.UTC(y,m-1,1)).getUTCDay()+6)%7;
    for(let i=0;i<offset;i++)append(grid,"span","empty","").setAttribute("aria-hidden","true");
    for(const row of dayRows()){
      const day=Number(row.day.slice(8)),plan=row.plan;
      const el=append(grid,"button",[
        row.actual_off?"actual-off":row.records.length?"worked":plan?.status==="shift"?"planned":plan?.status==="off"?"off":"",
        row.day===moscowDay()?"today":"",row.day===selectedDay?"selected":"",plan?.status==="off"?"plan-off":plan?.status==="shift"?"plan-work":""
      ].join(" "));
      append(el,"span","",day);
      append(el,"span","day-weekday",["Вс","Пн","Вт","Ср","Чт","Пт","Сб"][new Date(row.day+"T12:00:00Z").getUTCDay()]);
      append(el,"small","",row.actual_off?"вых.":row.hours!==null?shortPaidLabel(row.hours):row.open?"идёт":plan?.status==="shift"?(plan.start||"план"):plan?.status==="off"?"вых.":"");
      if(row.records.length&&plan)append(el,"small","day-plan-label",plan.status==="off"?"план: вых.":"план: раб.");
      el.setAttribute("aria-pressed",String(row.day===selectedDay));
      el.setAttribute("aria-label",day+" "+MONTHS[m-1]+" · "+row.status_label+(plan?" · план: "+(plan.status==="off"?"выходной":(plan.start||"рабочий")):" · план не задан"));
      el.dataset.day=row.day;
      el.setAttribute("aria-label",fullDayLabel(row.day)+" · "+row.status_label+(row.hours!==null?" · "+formatHours(row.hours):"")+(row.records.some(r=>r.office_estimated)?" · расчёт по офису":"")+(state.deleted_shifts?.some(r=>r.day===row.day)?" · есть удалённые смены":"")+(plan?" · план: "+(plan.status==="off"?"выходной":(plan.start||"рабочий")):" · план не задан"));
      if(row.day===moscowDay())el.setAttribute("aria-current","date");
      if(bulkDraft){
        const picked=bulkDraft.days.has(row.day);
        el.classList.toggle("bulk-picked",picked);el.disabled=row.day<moscowDay();
        el.setAttribute("aria-pressed",String(picked));
        el.setAttribute("aria-label",(picked?"Выбрано · ":"Выбрать · ")+el.getAttribute("aria-label"));
        if(picked)append(el,"i","selection-tick","✓").setAttribute("aria-hidden","true");
      }
      el.onclick=()=>bulkDraft?toggleBulkDay(row.day):showDay(row.day);
    }
    restoreCalendarTabStop(focused);
    paintDay();
  }
  function line(parent,name,value){
    const el=append(parent,"div","detail-line");
    append(el,"span","",name);
    append(el,"strong","",value);
  }

  let planDraft=null;
  // A selection is a local draft. Only the existing owner-confirmed chat route writes.
  let bulkDraft=null;
  function bulkRevision(){return state?.plan_editor?.batch_revision||"";}
  function leaveBulk(){bulkDraft=null;paintBulk();if(state)paintCalendar();}
  function beginBulk(){
    if(!state||commandBusy||pendingRequest)return;
    if(bulkDraft){leaveBulk();return;}
    bulkDraft={month:state.month,days:new Set(),status:"shift",start:"16:00",end:"02:00",revision:bulkRevision()};
    setCalendarMode("calendar");paintCalendar();paintBulk();
  }
  function toggleBulkDay(day){
    if(commandBusy||pendingRequest||!bulkDraft||day<moscowDay()||day.slice(0,7)!==bulkDraft.month)return;
    if(bulkDraft.days.has(day))bulkDraft.days.delete(day);else bulkDraft.days.add(day);
    if(!bulkDraft.days.size)bulkDraft.revision=bulkRevision();
    paintCalendar();
    byId("calendar-grid").querySelector(`[data-day="${day}"]`)?.focus({preventScroll:true});
  }
  function paintBulk(){
    if(bulkDraft&&state?.month!==bulkDraft.month)bulkDraft=null;
    const enabled=!connected||Boolean(state?.plan_editor?.supports_batch);
    byId("bulk-toggle").hidden=!enabled;
    byId("bulk-toggle").setAttribute("aria-pressed",String(Boolean(bulkDraft)));
    text("bulk-toggle",bulkDraft?"Отменить выбор":"Выбрать дни");
    byId("bulk-editor").hidden=!bulkDraft;
    text("bulk-hint",bulkDraft?"Выбрано дат: "+bulkDraft.days.size:"Один график для нескольких дат");
    ["month-prev","month-next","month-jump","month-today","mode-table"].forEach(id=>{byId(id).disabled=Boolean(bulkDraft);});
    const exportButton=byId("calendar-export");
    const clock=/^(?:[01]\d|2[0-3]):[0-5]\d$/;
    const exportable=(state?.plan||[]).some(p=>p?.status==="shift"&&/^\d{4}-\d{2}-\d{2}$/.test(p.day||"")&&clock.test(p.start||"")&&clock.test(p.end||""));
    exportButton.disabled=Boolean(bulkDraft)||loading||commandBusy||!exportable;
    exportButton.title=exportable?"Экспортирует только плановые смены; фактические отметки не меняются.":"В выбранном месяце нет плановых смен.";
    if(!bulkDraft)return;
    const draft=bulkDraft;
    if(!draft.days.size)draft.revision=bulkRevision();
    text("bulk-count",draft.days.size?"Выбрано: "+draft.days.size+" "+plural(draft.days.size,["дата","даты","дат"]):"Нажми нужные даты в календаре");
    text("bulk-dates",[...draft.days].sort().map(d=>d.slice(8)+"."+d.slice(5,7)).join(" · "));
    for(const type of ["shift","off","unset"])byId("bulk-"+type).setAttribute("aria-pressed",String(draft.status===type));
    const working=draft.status==="shift";
    byId("bulk-times").hidden=!working;
    byId("bulk-start").disabled=byId("bulk-end").disabled=!working;
    if(document.activeElement!==byId("bulk-start"))byId("bulk-start").value=draft.start;
    if(document.activeElement!==byId("bulk-end"))byId("bulk-end").value=draft.end;
    text("bulk-preview",working?`${draft.start}–${draft.end}${draft.end<draft.start?" · уход на следующий день":""}`:draft.status==="off"?"Выходной для выбранных дат":"План будет снят. Это не выходной и не удаление смен.");
    const stale=connected&&draft.revision!==bulkRevision();
    text("bulk-error",stale?"График изменился. Отмени выбор, обнови данные и выбери даты заново.":"");
    byId("bulk-submit").disabled=!draft.days.size||stale;
    text("bulk-submit",directPlans()?"Сохранить выбранные дни":connected?"Проверить в чате →":"Проверить выбор");
    text("bulk-note",directPlans()?"Меняется только план. Перед сохранением покажем все выбранные даты.":"Меняется только план. Сохранение — после проверки в чате.");
    lockPlanControls();
    const recoveryFields=byId("recovery-fields");if(recoveryFields)recoveryFields.disabled=commandBusy||Boolean(pendingRequest);
    byId("bulk-clear").disabled=!draft.days.size;
  }
  async function submitBulk(event){
    event.preventDefault();if(!bulkDraft)return;
    const draft=bulkDraft,error=byId("bulk-error");error.textContent="";
    if(!draft.days.size){error.textContent="Сначала выбери даты.";return;}
    if(draft.status==="shift"&&(!validClock(draft.start)||!validClock(draft.end)||draft.start===draft.end)){
      error.textContent="Укажи разное время начала и окончания.";return;
    }
    if([...draft.days].some(day=>day<moscowDay()||day.slice(0,7)!==draft.month)){
      error.textContent="Одна из дат уже прошла. Выбери дни заново.";return;
    }
    if(!connected){error.textContent="Выбор проверен. Это демонстрация — ничего не сохранено.";return;}
    if(!state||!fresh||loading||!navigator.onLine){error.textContent="Нет актуального соединения. Обнови данные.";return;}
    if(directPlans()){
      if(draft.month!==state.month||draft.revision!==bulkRevision()){error.textContent="График изменился. Обнови календарь.";return;}
      await saveBulkHere(draft);return;
    }
    const username=state.plan_editor?.bot_username||"";
    if(!/^[A-Za-z][A-Za-z0-9_]{4,31}$/.test(username)||!tg?.openTelegramLink){error.textContent="Переход в чат сейчас недоступен.";return;}
    if(draft.month!==state.month||! /^[a-f0-9]{24}$/.test(draft.revision)||draft.revision!==bulkRevision()){
      error.textContent="График изменился. Выбери дни заново; ничего не сохранено.";return;
    }
    // Arithmetic avoids signed bitwise overflow on the 31st day.
    const mask=[...draft.days].reduce((sum,day)=>sum+2**(Number(day.slice(8))-1),0).toString(16).padStart(8,"0");
    const code={shift:"s",off:"o",unset:"n"}[draft.status];
    const start=draft.status==="shift"?draft.start.replace(":",""):"0000",end=draft.status==="shift"?draft.end.replace(":",""):"0000";
    const payload=`b_${draft.month.replace("-","")}_${mask}_${code}_${start}_${end}_${draft.revision}`;
    if(payload.length>64){error.textContent="Не удалось подготовить выбор.";return;}
    try{tg.openTelegramLink(`https://t.me/${username}?start=${payload}`);tg.close?.();}
    catch(e){error.textContent="Переход не выполнен. План не сохранён.";}
  }
  function validClock(value){return /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(value);}
  byId("bulk-toggle").onclick=beginBulk;
  byId("bulk-cancel").onclick=leaveBulk;
  byId("bulk-clear").onclick=()=>{if(bulkDraft){bulkDraft.days.clear();paintCalendar();}};
  byId("bulk-form").onsubmit=submitBulk;
  for(const type of ["shift","off","unset"])byId("bulk-"+type).onclick=()=>{if(bulkDraft){bulkDraft.status=type;paintBulk();}};
  for(const key of ["start","end"])byId("bulk-"+key).oninput=e=>{if(bulkDraft){bulkDraft[key]=e.target.value;paintBulk();}};
  document.querySelectorAll("[data-bulk-preset]").forEach(button=>button.onclick=()=>{
    if(!bulkDraft)return;[bulkDraft.start,bulkDraft.end]=button.dataset.bulkPreset.split("/");bulkDraft.status="shift";paintBulk();
  });

  function paintPlanEditor(box,row){
    const section=append(box,"section","calendar-plan");
    if(box.tagName!=="DETAILS")append(section,"h3","","График на день");
    const oldLabel=row.plan?(row.plan.status==="off"?"Выходной":(row.plan.start||"—")+"–"+(row.plan.end||"—")):"Не задан";
    if(box.tagName!=="DETAILS")append(section,"p","plan-current","Сейчас в плане: "+oldLabel);
    const editor=state.plan_editor||{},revision=editor.revisions?.[row.day]||"";
    const historical=row.day<moscowDay();
    if(historical&&connected&&!editor.supports_past_plan){
      append(section,"p","plan-note","Обнови кабинет: редактирование прошедшего плана пока недоступно в этой версии сервера.");return;
    }
    if(historical)append(section,"p","plan-history-note","Прошедшая дата. Можно уточнить план — реальные часы и начисления останутся прежними.");
    if(!planDraft||planDraft.day!==row.day||(!planDraft.dirty&&planDraft.revision!==revision)){
      planDraft={day:row.day,status:row.plan?.status||(!connected||editor.supports_unset?"unset":"shift"),start:row.plan?.start||"16:00",end:row.plan?.end||"02:00",revision,dirty:false};
    }
    const draft=planDraft;
    const form=append(section,"form","plan-form");form.id="plan-form";
    const fields=append(form,"fieldset");fields.id="plan-fields";fields.disabled=commandBusy||Boolean(pendingRequest);
    const choices=append(fields,"div","segmented plan-choices");choices.setAttribute("role","group");choices.setAttribute("aria-label","Тип дня по плану");
    const work=append(choices,"button","","Рабочий"),off=append(choices,"button","","Выходной");
    work.type=off.type="button";work.id="plan-work";off.id="plan-off";
    const unset=append(choices,"button","","Не задан");unset.type="button";unset.id="plan-unset";
    unset.hidden=connected&&!editor.supports_unset;
    const times=append(fields,"div","plan-times");times.id="plan-times";
    const startLabel=append(times,"label","","Начало"),start=append(startLabel,"input");
    start.type="time";start.id="plan-start";start.value=draft.start;start.required=true;start.step="60";
    const endLabel=append(times,"label","","Окончание"),end=append(endLabel,"input");
    end.type="time";end.id="plan-end";end.value=draft.end;end.required=true;end.step="60";
    const preview=append(fields,"p","plan-preview");preview.id="plan-preview";preview.setAttribute("aria-live","polite");
    const note=append(fields,"p","plan-note","Только эта дата. Фактические смены и начисления не изменятся.");
    const error=append(fields,"p","plan-error");error.id="plan-error";error.setAttribute("role","alert");
    const submit=append(fields,"button","main-action",directPlans()?"Сохранить план":connected?"Продолжить в чат →":"Проверить выбор");submit.type="submit";submit.id="plan-submit";
    append(fields,"p","plan-note",directPlans()?"Подтверждение появится здесь, без перехода в чат.":connected?"Сохранение — после подтверждения в чате.":"Демо: выбор не сохраняется.");
    const refresh=()=>{
      work.setAttribute("aria-pressed",String(draft.status==="shift"));off.setAttribute("aria-pressed",String(draft.status==="off"));
      unset.setAttribute("aria-pressed",String(draft.status==="unset"));
      times.hidden=draft.status!=="shift";start.disabled=end.disabled=times.hidden;
      const overnight=draft.end<draft.start;
      preview.textContent=draft.status==="unset"?"План будет снят. Это не выходной; фактические смены сохранятся.":draft.status==="off"?"Выбрано: выходной":`Выбрано: ${draft.start}–${draft.end}${overnight?" · уход на следующий день":""}`;
      error.textContent="";
    };
    work.onclick=()=>{draft.status="shift";draft.dirty=true;refresh();};
    off.onclick=()=>{draft.status="off";draft.dirty=true;refresh();};
    unset.onclick=()=>{draft.status="unset";draft.dirty=true;refresh();};
    start.oninput=()=>{draft.start=start.value;draft.dirty=true;refresh();};
    end.oninput=()=>{draft.end=end.value;draft.dirty=true;refresh();};
    form.onsubmit=async event=>{
      event.preventDefault();error.textContent="";
      if(draft.status==="shift"&&(!/^\d{2}:\d{2}$/.test(draft.start)||!/^\d{2}:\d{2}$/.test(draft.end)||draft.start===draft.end)){
        error.textContent="Укажи разное время начала и окончания смены.";return;
      }
      if(!connected){error.textContent="Выбор проверен. Это демонстрация — ничего не сохранено.";return;}
      if(!state||!fresh||loading||!navigator.onLine){error.textContent="Нет актуального соединения. Обнови данные перед изменением плана.";return;}
      const current=state.plan_editor||{};
      if(directPlans()){
        if(current.revisions?.[draft.day]!==draft.revision){error.textContent="План изменился. Открой день заново.";return;}
        await savePlanHere(draft);return;
      }
      if(!/^[A-Za-z][A-Za-z0-9_]{4,31}$/.test(current.bot_username||"")||!tg?.openTelegramLink){
        error.textContent="Открой чат бота → Смены → Календарь → выбери эту дату. Переход из кабинета сейчас недоступен.";return;
      }
      if(!/^[a-f0-9]{32}$/.test(draft.revision)||current.revisions?.[draft.day]!==draft.revision||(draft.day<moscowDay()&&!current.supports_past_plan)){
        error.textContent="План изменился. Закрой карточку и открой день заново.";return;
      }
      const startCode=draft.status!=="shift"?"0000":draft.start.replace(":","");
      const endCode=draft.status!=="shift"?"0000":draft.end.replace(":","");
      const payload=`p_${draft.day.replaceAll("-","")}_${{shift:"s",off:"o",unset:"n"}[draft.status]}_${startCode}_${endCode}_${draft.revision}`;
      if(payload.length>64){error.textContent="Не удалось подготовить выбор. Измени план через календарь чата.";return;}
      try{tg.openTelegramLink(`https://t.me/${current.bot_username}?start=${payload}`);tg.close?.();}
      catch(e){error.textContent="Переход не выполнен. План не сохранён; используй календарь чата.";}
    };
    writeRetry(form,"plan-retry");
    refresh();
  }


  // Break intervals are fetched only when opened, never cached on disk.
  let breakPanel=null,breakSequence=0;
  const breakSavedText="Сохранено. Расчёт обновлён.";
  const breakActions=["save_break","remove_break","undo_break_change"];
  function breakClock(value){return value?new Intl.DateTimeFormat("ru-RU",{timeZone:"Europe/Moscow",day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}).format(new Date(value)):"Идёт сейчас";}
  function breakLocal(value){return value?new Intl.DateTimeFormat("sv-SE",{timeZone:"Europe/Moscow",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(new Date(value)).replace(" ","T"):"";}
  function exactDuration(seconds){const n=Math.max(0,Math.round(seconds));return `${Math.floor(n/3600)} ч ${String(Math.floor(n%3600/60)).padStart(2,"0")} мин${n%60?" "+n%60+" с":""}`;}
  function lockBreakControls(){
    const root=byId("break-panel");if(!root)return;
    const busy=commandBusy||Boolean(pendingRequest)||Boolean(breakPanel?.busy);
    root.setAttribute("aria-busy",String(Boolean(breakPanel?.busy)||commandBusy));
    root.querySelectorAll("fieldset,button:not(#break-retry)").forEach(e=>e.disabled=busy);
    const retry=byId("break-retry");if(retry){retry.hidden=!pendingRequest;retry.disabled=commandBusy||!fresh||loading||!navigator.onLine;}
  }
  function breakError(message){const e=byId("break-error");if(e)e.textContent=message;}
  function breakButton(parent,label,action,cls="secondary-action"){
    const b=append(parent,"button",cls,label);b.type="button";b.onclick=action;return b;
  }
  function paintBreakEntry(parent,row,item){
    if(connected&&!state?.capabilities?.break_edits)return;
    const b=breakButton(parent,"Перерывы и расчёт времени",()=>openBreaks(item,row),"secondary-action shift-break-details");
    b.setAttribute("aria-label","Перерывы и расчёт смены "+dateLabel(row.day)+", приход "+item.start);
  }
  async function openBreaks(item,row){
    if(commandBusy||pendingRequest){notify("Сначала проверь предыдущее сохранение.");return;}
    breakPanel={sid:item.id,item,row,data:null,dirty:false,busy:false};actualDraft=null;recoveryDraft=null;planDraft=null;
    if(toastTask)clearTimeout(toastTask);byId("toast").hidden=true;
    await loadBreaks();
  }
  function demoBreakDetails(panel){
    const start=new Date(panel.row.day+"T"+panel.item.start+":00+03:00");
    let end=panel.item.end?new Date(panel.row.day+"T"+panel.item.end+":00+03:00"):null;
    if(end&&end<=start)end=new Date(end.getTime()+86400000);
    const gross=end?(end-start)/1000:null;
    return {shift_id:panel.sid,day:panel.row.day,start:start.toISOString(),end:end?.toISOString()||null,sessions:[],
      deducted_minutes:panel.item.break_minutes,interval_minutes:0,mode:"total_only",revision:"",undo_event_id:null,
      gross_seconds:gross,paid_seconds:gross===null?null:Math.max(0,gross-panel.item.break_minutes*60),
      earnings:panel.item.earnings,office_estimated:false};
  }
  async function loadBreaks(){
    if(!breakPanel)return;const panel=breakPanel,seq=++breakSequence;panel.busy=true;panel.dirty=false;renderBreaks();
    try{
      const data=connected?await post("/mini/api/workspace",{view:"breaks",shift_id:panel.sid}):demoBreakDetails(panel);
      if(seq!==breakSequence||breakPanel!==panel||byId("day-sheet").hidden)return;
      panel.data=data;panel.busy=false;renderBreaks();
    }catch(error){
      if(seq!==breakSequence||breakPanel!==panel)return;
      panel.busy=false;renderBreaks();breakError(error.message);
      if(error.status===403){await closeDay(true);await reload();}
    }
  }
  async function backFromBreaks(){
    if(commandBusy||breakPanel?.busy)return;
    if(breakPanel?.dirty&&!pendingRequest&&!await modal("Вернуться без сохранения?","Введённый перерыв ещё не сохранён."))return;
    breakPanel=null;breakSequence++;paintDay();
    const target=query(".shift-break-details");target?.focus();
  }
  function renderBreaks(){
    const panel=breakPanel;if(!panel)return;
    const box=byId("day-details");box.replaceChildren();
    const root=append(box,"section","break-panel");root.id="break-panel";
    breakButton(root,"‹ К смене",backFromBreaks,"text-button break-back");
    const h=append(root,"h3","","Перерывы и расчёт");h.id="break-heading";h.tabIndex=-1;
    if(panel.feedback){if(toastTask)clearTimeout(toastTask);byId("toast").hidden=true;const status=append(root,"p","break-feedback",breakSavedText);status.setAttribute("role","status");}
    const data=panel.data;
    if(!connected)append(root,"p","plan-history-note","Демонстрация: записи не меняются.");
    if(!data){append(root,"p","muted",panel.busy?"Загружаем перерывы…":"Не удалось загрузить данные.");}
    else{
      const sum=append(root,"div","break-summary");
      if(data.gross_seconds!==null){
        line(sum,"От прихода до ухода",exactDuration(data.gross_seconds));
        line(sum,"Вычтено на перерывы",data.deducted_minutes+" мин");
        line(sum,"Оплачиваемое время",exactDuration(data.paid_seconds));
        line(sum,"За эту смену",money(data.earnings));
      }else{line(sum,"Смена открыта","С "+breakClock(data.start));line(sum,"Завершённые перерывы в расчёте",data.deducted_minutes+" мин");}
      const mode=data.mode==="total_only"?"Перерыв вычтен автоматически по настройке либо указан общей суммой. Это предварительное значение, не измеренный отдых. После офисного табеля его можно уточнить.":data.mode==="manual_override"?"Общая сумма была уточнена вручную и отличается от интервалов. При правке ниже покажем новый расчёт.":"Вычитается сумма записанных интервалов. Каждый неполный интервал округляется вверх до минуты.";
      append(root,"p","plan-note break-mode",mode);
      if(data.office_estimated)append(root,"p","plan-history-note","Расчёт по офисным цифрам. Изменение перерыва изменит оплачиваемую длительность и может нарушить совпадение с офисом.");
      if(!data.sessions.length)append(root,"p","break-empty","Отдельных интервалов пока нет.");
      const list=append(root,"div","break-list");
      data.sessions.forEach((r,i)=>{
        const card=append(list,"article","break-interval");card.dataset.breakId=String(r.id);
        append(card,"strong","",`Перерыв ${i+1} · ${r.end?r.minutes+" мин":"идёт"}`);
        append(card,"p","plan-note",breakClock(r.start)+" — "+breakClock(r.end));
        const actions=append(card,"div","context-actions");
        breakButton(actions,r.end?"Исправить":"Уточнить окончание",()=>renderBreakForm(r),"small-switch break-edit");
        breakButton(actions,"Убрать ошибочный",()=>submitBreak({action:"remove_break",shift_id:data.shift_id,break_id:r.id,expected_revision:data.revision}),"text-button break-remove");
      });
      const adjust=append(root,"details","break-adjust-disclosure");
      append(adjust,"summary","","Уточнение перерыва после сверки");
      append(adjust,"p","plan-note","Если табель расходится с нашим расчётом, сначала проверь приход, уход и формат часов. Общий вычет можно исправить в карточке смены; точный интервал добавляй только если знаешь его время.");
      breakButton(adjust,"Добавить известный интервал",()=>renderBreakForm(null),"secondary-action break-add");
      if(data.undo_event_id)breakButton(root,"Отменить последнюю правку перерывов",()=>submitBreak({action:"undo_break_change",shift_id:data.shift_id,event_id:data.undo_event_id,expected_revision:data.revision}),"text-button break-undo");
    }
    const error=append(root,"p","plan-error");error.id="break-error";error.setAttribute("role","alert");
    const retry=breakButton(root,"Проверить сохранение",retryWrite);retry.id="break-retry";retry.hidden=!pendingRequest;
    breakButton(root,"Обновить перерывы",loadBreaks,"text-button break-refresh");
    query(".day-sheet").scrollTop=0;h.focus({preventScroll:true});lockBreakControls();
  }
  function renderBreakForm(old){
    const panel=breakPanel;if(!panel?.data||panel.busy||commandBusy||pendingRequest)return;
    const data=panel.data;panel.dirty=false;
    const root=byId("break-panel");root.replaceChildren();
    const h=append(root,"h3","",old?(old.end?"Исправить перерыв":"Когда закончился перерыв?"):"Добавить перерыв");h.tabIndex=-1;
    append(root,"p","plan-note","Укажи фактическое время по Москве. Для ночного перерыва проверь обе даты. Приход и уход смены не изменятся.");
    const form=append(root,"form");form.id="break-form";const fields=append(form,"fieldset");fields.id="break-fields";
    const times=append(fields,"div","break-times");
    for(const [key,label,value] of [["start","Начало · МСК",old?breakLocal(old.start):""],["end","Окончание · МСК",old?.end?breakLocal(old.end):""]]){
      const labelEl=append(times,"label","",label),input=append(labelEl,"input");input.type="datetime-local";input.id="break-"+key;input.name=key;input.value=value;input.required=true;input.step=60;
      input.min=breakLocal(data.start);input.max=breakLocal(data.end||new Date(serverNow()).toISOString());
    }
    append(fields,"p","plan-note","После проверки покажем прежнюю и новую сумму перерыва и влияние на начисление.");
    const save=append(fields,"button","main-action","Проверить изменение");save.type="submit";save.id="break-save";
    breakButton(fields,"Отмена",async()=>{if(panel.dirty&&!await modal("Отменить ввод?","Значения нового перерыва не будут сохранены."))return;panel.dirty=false;renderBreaks();},"text-button break-cancel");
    const error=append(root,"p","plan-error");error.id="break-error";error.setAttribute("role","alert");
    const retry=breakButton(root,"Проверить сохранение",retryWrite);retry.id="break-retry";retry.hidden=!pendingRequest;
    form.oninput=()=>{panel.dirty=true;breakError("");};
    form.onsubmit=async e=>{e.preventDefault();if(!form.reportValidity())return;await submitBreak({action:"save_break",shift_id:data.shift_id,break_id:old?.id??null,start:byId("break-start").value,end:byId("break-end").value,expected_revision:data.revision});};
    query(".day-sheet").scrollTop=0;h.focus({preventScroll:true});lockBreakControls();
  }
  async function submitBreak(change){
    if(!connected){breakError("Это демонстрация. Ничего не сохранено.");return;}
    const panel=breakPanel;if(!panel||panel.busy||commandBusy||pendingRequest)return;
    panel.busy=true;lockBreakControls();breakError("");const seq=breakSequence;
    try{
      const preview=await post("/mini/api/preview",{kind:"break_change",change});
      if(breakPanel!==panel||seq!==breakSequence)return;
      const summary=`Было: ${preview.before_minutes} мин. Будет: ${preview.after_minutes} мин.`+(preview.after_earnings!==null?`\nНачисление: ${money(preview.before_earnings)} → ${money(preview.after_earnings)}`:"")+
        (preview.replaces_total?"\n\nПрежняя общая сумма заменится суммой записанных интервалов — не прибавится к ней.":"")+
        (preview.running_after?"\nНезавершённый перерыв ещё не включён в итог.":"")+"\n\nВремя прихода и ухода, план и поступления сохранятся. Изменение останется в истории.";
      panel.busy=false;
      await runWrite({...change,nonce:newNonce()},change.action==="remove_break"?"Убрать этот перерыв?":change.action==="undo_break_change"?"Отменить последнюю правку?":"Сохранить перерыв?",summary);
    }catch(error){breakError(error.message);if(error.status===403){await closeDay(true);await reload();}}
    finally{if(breakPanel===panel)panel.busy=false;lockBreakControls();}
  }

  function paintDay(){
    if((actualDraft||recoveryDraft||breakPanel)&&!byId("day-sheet").hidden)return;
    if(!byId("day-sheet").hidden&&planDraft?.dirty&&planDraft.day===selectedDay&&byId("plan-form"))return;
    const box=byId("day-details"),sameDay=box.dataset.day===selectedDay;
    const planWasOpen=sameDay&&Boolean(byId("day-plan-disclosure")?.open);
    box.replaceChildren();box.dataset.day=selectedDay;
    text("day-heading",selectedDay.slice(8)+"."+selectedDay.slice(5,7)+"."+selectedDay.slice(0,4));
    const row=dayRows().find(r=>r.day===selectedDay);
    if(!row)return;
    const pager=append(box,"nav","day-browse");pager.setAttribute("aria-label","Соседние дни");
    for(const [step,label,id] of [[-1,"‹ Предыдущий","day-prev"],[1,"Следующий ›","day-next"]]){
      const button=append(pager,"button","small-switch",label);button.type="button";button.id=id;
      button.disabled=Boolean(pendingRequest)||commandBusy||loading||(step<0&&row.day==="2000-01-01")||(step>0&&row.day==="2100-12-31");button.onclick=()=>browseDay(step);
    }
    const historical=row.day<moscowDay(),factsFirst=historical||row.records.length>0||row.actual_off;
    const renderFacts=()=>{
      const facts=append(box,"section","day-facts");facts.setAttribute("aria-label","Фактические смены");
      if(!row.records.length){
        append(facts,"h3","",row.actual_off?"Факт дня":"Фактическая смена");
        append(facts,"p",row.actual_off?"actual-off-status":"muted",row.actual_off?"Фактически выходной · 0 рабочих часов":"Фактических отметок за этот день нет.");
        if(row.day<=moscowDay()&&(!row.actual_off||!row.archived_shifts)){
          const add=append(facts,"button","secondary-action day-add-fact","Добавить фактическую смену →");
          add.type="button";add.onclick=()=>!connected||state.capabilities?.actual_edits?openActualEditor(null,row):openChatDestination("day",row.day);
        }
      }
      for(const item of row.records){
        const record=append(facts,"section","day-fact-record");record.dataset.shiftId=String(item.id);
        append(record,"h3","",item.end?"Фактическая смена":"Смена в работе");
        line(record,"Приход",item.start);
        line(record,"Уход",(item.end||"Не отмечен")+(item.next_day?" · +1 день":""));
        line(record,"Перерыв в записи",item.break_minutes+" мин");
        if(item.end){
          line(record,"Оплачиваемое время",formatHours(item.hours));
          if(item.rate!==undefined)line(record,"Ставка на эту дату",money(item.rate)+"/ч");
          line(record,"Начислено за смену",money(item.earnings));
        }
        const edit=append(record,"button","secondary-action day-fact-edit",item.end?"Исправить фактическую смену →":!connected||state.capabilities?.actual_edits?"Уточнить уход":"Уточнить уход в чате →");
        edit.type="button";
        edit.onclick=()=>!connected||state.capabilities?.actual_edits?openActualEditor(item,row):item.end&&state.plan_editor?.supports_exact_correction?
          openChatDestination("fix",{day:row.day,id:item.id}):openChatDestination("day",row.day);
        append(record,"p","plan-note",!connected?"Демонстрация: форму можно посмотреть, реальные записи не меняются.":state.capabilities?.actual_edits?"Приход, уход и перерыв исправляются здесь. Изменения влияют на начисления и сохраняются в журнале.":item.end?"Приход, уход и перерыв. Изменение часов влияет на начисления; сохранение — после проверки в чате.":"Открытая смена исправляется отдельно от расписания.");
        paintBreakEntry(record,row,item);
        paintRecoveryControls(record,row,item);
        if(item.office_estimated)append(record,"p","plan-history-note","Расчётная смена по офисному фото. Приход и уход приблизительные; это не отметка устройства.");
        const provenance=append(record,"details","day-more");append(provenance,"summary","","Источник записи");
        append(provenance,"div","detail-source","Источник: "+({import:"исторический Excel",manual:"ручная запись",button:"отметка кнопкой"}[item.source]||"учётная запись")+". Ночная смена относится к дате прихода.");
      }
      paintActualOffActions(facts,row);
      paintDeletedShifts(facts,row);
    };
    if(factsFirst){
      renderFacts();
      const disclosure=append(box,"details","disclosure day-plan-disclosure");disclosure.id="day-plan-disclosure";disclosure.open=planWasOpen;
      const summary=append(disclosure,"summary");summary.id="day-plan-toggle";
      append(summary,"strong","","Изменить план");
      append(summary,"span","plan-current",row.plan?(row.plan.status==="off"?"Сейчас: выходной":"Сейчас: "+(row.plan.start||"—")+"–"+(row.plan.end||"—")):"Сейчас: не задан");
      paintPlanEditor(disclosure,row);
    }else{paintPlanEditor(box,row);renderFacts();}
    paintOfficePhoto(box,row);
    const officeBox=append(box,"details","day-more");
    append(officeBox,"summary","","Сверка с офисом");
    if(row.office_hours===null)append(officeBox,"div","detail-source","Офисные часы не подтверждены. Это не нулевой табель и не расхождение.");
    else{
      line(officeBox,"Подтверждено офисом",formatHours(row.office_hours));
      if(row.delta_hours!==null)line(officeBox,"Личные − офис",signedMinutes(row.delta_hours));
      else append(officeBox,"p","muted",row.open?"Сравнение после завершения смены.":"Для сравнения нужна личная отметка.");
    }
    const history=append(box,"button","secondary-action day-history-link","История этого дня");history.type="button";history.onclick=()=>openActivity(row.day);
    if(connected&&!(state.capabilities?.actual_edits&&directPlans())){
      const btn=append(box,"button","text-button day-edit-link","Открыть этот день в чате →");
      btn.onclick=()=>openChatDestination("day",row.day);
    }
  }

  function paintSalary(){
    const sum=state.summary,extra=sum.additional||{};
    text("salary-month",MONTHS[Number(currentMonth.slice(5))-1]+" "+currentMonth.slice(0,4));
    text("salary-total",money(sum.total));text("salary-hours",formatHours(sum.hours)+" · "+sum.shifts+" "+plural(sum.shifts,["закрытая смена","закрытые смены","закрытых смен"]));
    text("salary-base",money(sum.earned));text("salary-extra",money(extra.net_adjustment));
    const hasReceived=Number(sum.received)>0,receivedCard=query(".received-card");
    receivedCard.classList.toggle("is-empty",amountsVisible&&!hasReceived);
    text("salary-received",!amountsVisible?money(sum.received):hasReceived?money(sum.received):"Пока нет");
    query(".received-card p").textContent=!amountsVisible?"Сумма скрыта":hasReceived?"Внесённые поступления":"Поступлений за этот месяц пока нет";
    const cashflowBalance=Number(sum.total||0)-Number(sum.received||0);
    text("salary-balance-label",!amountsVisible?"Сумма скрыта":cashflowBalance>0.01?"Ещё не получено":cashflowBalance< -0.01?(Number(sum.received)>0?"Получено сверх начисленного":"Корректировки выше начислений"):"Начисления и поступления совпадают");
    text("salary-balance",money(Math.abs(cashflowBalance)));
    const receivedShare=Number(sum.total)>0?Math.round(Number(sum.received||0)/Number(sum.total)*100):0;
    const progress=Math.max(0,Math.min(100,receivedShare));
    byId("cashflow-progress-fill").style.width=amountsVisible?progress+"%":"0%";
    byId("cashflow-progress-track").setAttribute("aria-valuenow",amountsVisible?String(progress):"0");
    text("cashflow-progress-label",!amountsVisible?"Суммы скрыты":Number(sum.total)>0?"Получено "+receivedShare+"% от начисленного":"Нет начислений для сравнения");
    text("cashflow-earned-label","Начислено: "+money(sum.total));
    text("cashflow-note",!amountsVisible?"Покажи суммы, чтобы увидеть соотношение.":Number(sum.total)<=0&&Number(sum.received)>0?"Поступления записаны, но начислений за выбранный месяц пока нет.":cashflowBalance>0.01?"Разница рассчитана как начислено с корректировками минус поступления за этот месяц.":cashflowBalance< -0.01?"Поступления за месяц превышают начисления. Это сверка записей, не банковский баланс.":"Начисления и внесённые поступления совпадают.");
    text("salary-bonuses",money((extra.bonus||0)+(extra.allowance||0)));
    text("salary-deductions",money(extra.deduction));text("salary-reimbursements",money(extra.reimbursement));
    const periods=byId("salary-periods");periods.replaceChildren();
    (state.periods||[]).forEach(period=>{
      const card=append(periods,"article","metric period-finance");
      const total=Number(period.total??(Number(period.earnings||0)+Number(period.adjustments||0)));
      const received=Number(period.received||0),difference=total-received;
      append(card,"span","",period.period===1?"1–15 число":"16–конец месяца");
      append(card,"strong","",money(total));
      append(card,"small","",formatHours(period.hours)+" · начислено за часы и корректировки");
      append(card,"small","period-paid","Получено: "+money(received));
      append(card,"small","period-balance",(difference>0.01?"Осталось: ":difference< -0.01?"Сверх начисленного: ":"Сверено: ")+money(Math.abs(difference)));
    });
    const rates=byId("rate-breakdown");rates.replaceChildren();
    for(const rate of state.rates||[]){
      const row=append(rates,"div","rate-row"),left=append(row,"div");
      append(left,"span","",formatHours(rate.hours)+" × "+money(rate.rate)+"/ч");
      append(left,"small","",rate.shifts+" смен · ставка на дату прихода");
      append(row,"strong","",money(rate.earned));
    }
    if(!(state.rates||[]).length)append(rates,"p","footnote","Детализация ставок появится при наличии закрытых смен.");
    const forecast=state.forecast;
    byId("forecast-card").hidden=!(forecast?.future_shifts>0);
    text("forecast-money",forecast?.future_shifts?"+ "+money(forecast.future_pay):"");
    text("forecast-note",forecast?.future_shifts?
      forecast.future_shifts+" запланированных смен · "+formatHours(forecast.future_hours)+". Оценка с перерывом "+forecast.assumed_break_minutes+" мин на смену. Не входит в начисления; открытая смена исключена.":"");
    const weeklyDetails=query(".weekly-details");weeklyDetails.hidden=!(state.weeks||[]).some(x=>x.hours>0);
    const weekly=byId("weekly-chart");weekly.replaceChildren();
    const max=Math.max(1,...state.weeks.map(x=>x.hours||0));
    state.weeks.forEach(row=>{
      const group=append(weekly,"div","bar-group");append(group,"strong","",hm(row.hours));
      const bar=append(group,"div","bar-col");bar.style.height=Math.round(5+(row.hours||0)/max*100)+"px";
      append(group,"span","",row.label);
    });
    requestAnimationFrame(drawChart);
  }
  function paintReview(){
    const box=byId("issue-list");box.replaceChildren();
    if(!state.issues.length)append(box,"div","issue good","В выбранном месяце явных ошибок в личных отметках не найдено.");
    const office=state.office_status||{};
    // Keep earlier office data available without repeatedly prompting for a new tabель.
    if(office.confirmed_days>0){
      const archive=append(box,"details","day-more");
      append(archive,"summary","","Ранее внесённые офисные сведения · "+office.confirmed_days+" дат");
      append(archive,"p","plan-note","Сравнимых дат: "+(office.compared_days||0)+". Эти сведения сохранены отдельно от личных отметок.");
    }
    state.issues.forEach(row=>{
      const item=append(box,"button","issue tappable");
      append(item,"span","when",row.day.slice(8)+"."+row.day.slice(5,7));append(item,"span","",row.text);
      item.onclick=()=>openCalendarDay(row.day);
    });
    renderDrafts();
  }
  function renderDrafts(){
    let markDraft=null;
    try{markDraft=JSON.parse(localStorage.getItem("workshift_draft")||"null");}catch(e){}
    const editDraft=readOfflineEditDraft(),panel=byId("pending-drafts");
    panel.hidden=!markDraft&&!editDraft;
    if(panel.hidden)return;
    panel.replaceChildren();
    if(markDraft){
      const card=append(panel,"article","offline-draft-card");
      append(card,"strong","","Неподтверждённая отметка");
      append(card,"p","","Действие: "+(markDraft.action==="in"?"приход":"уход")+" · время устройства "+markDraft.deviceTime+
        ". Это только напоминание: отметка не создана и в расчёт не входит.");
      const remove=append(card,"button","small-switch","Удалить черновик отметки");remove.onclick=()=>{localStorage.removeItem("workshift_draft");renderDrafts();};
    }
    if(editDraft){
      const card=append(panel,"article","offline-draft-card");
      append(card,"strong","","Черновик исправления · "+editDraft.day.split("-").reverse().join("."));
      append(card,"p","",editDraft.kind==="actual"?
        "Приход "+editDraft.start+" · уход "+editDraft.end+" · перерыв "+editDraft.break_minutes+" мин. Не отправлено, начисления не изменены.":
        (editDraft.action==="clock_in_at"?"Приход":"Уход")+" · "+editDraft.at.replace("T"," ")+" МСК. Время нужно проверить перед отправкой.");
      const actions=append(card,"div","context-actions");
      const resume=append(actions,"button","main-action","Продолжить проверку");
      resume.type="button";resume.disabled=!navigator.onLine||!connected;
      resume.onclick=resumeOfflineDraft;
      const remove=append(actions,"button","text-button","Удалить черновик");
      remove.type="button";remove.onclick=()=>{try{localStorage.removeItem(offlineEditDraftKey);}catch(e){}renderDrafts();};
      append(card,"p","plan-note","Черновик хранится только в этом браузере. Самостоятельно не отправляется.");
    }
  }
  function paint(){
    if(!state)return;
    tick();paintHome();paintCalendar();paintTable();paintSalary();paintReview();paintAgenda();paintSync();paintWriteRecovery();
    byId("offline-banner").hidden=navigator.onLine;
  }
  function paintMoneyPrivacyControls(){
    document.body.dataset.moneyPrivate=String(!amountsVisible);
    const title=amountsVisible?"Скрыть денежные суммы":"Показать денежные суммы";
    document.querySelectorAll("[data-money-toggle]").forEach(button=>{
      button.setAttribute("aria-label",title);button.setAttribute("aria-pressed",String(amountsVisible));
      button.title=title;
    });
    byId("money-privacy-tip").hidden=amountsVisible;
  }
  function clearMoneyPrivacyTimer(){
    if(moneyPrivacyTimer!==null){clearTimeout(moneyPrivacyTimer);moneyPrivacyTimer=null;}
  }
  function scheduleMoneyPrivacyHide(){
    clearMoneyPrivacyTimer();
    if(!amountsVisible||moneyPrivacyIdleMs===0)return;
    moneyPrivacyTimer=setTimeout(()=>{
      moneyPrivacyTimer=null;
      if(amountsVisible){
        setAmountsVisible(false);
        notify("Суммы автоматически скрыты после "+(moneyPrivacyIdleMs<60000?(moneyPrivacyIdleMs/1000)+" секунд":(moneyPrivacyIdleMs/60000)+" мин.")+" бездействия.");
      }
    },moneyPrivacyIdleMs);
  }
  function clearSessionLockTimer(){
    if(sessionLockTimer!==null){clearTimeout(sessionLockTimer);sessionLockTimer=null;}
  }
  function scheduleSessionLock(){
    clearSessionLockTimer();
    if(sessionLocked||sessionLockMinutes===0)return;
    sessionLockTimer=setTimeout(()=>{sessionLockTimer=null;lockSession("idle");},sessionLockMinutes*60000);
  }
  function lockSession(reason="idle"){
    if(sessionLocked||(sessionLockMinutes===0&&!securityEnabled))return;
    sessionLockFocus=document.activeElement;
    sessionLocked=true;clearSessionLockTimer();setAmountsVisible(false);
    if(securityEnabled)securityToken="";
    stopPhoneMotion("Датчик приостановлен, пока кабинет заблокирован.","Пауза");
    if(!byId("modal").hidden)byId("modal-cancel").click();
    byId("session-lock-reason").textContent=reason==="background"?"Кабинет приостановлен после сворачивания Telegram.":
      reason==="startup"?"Подтверди доступ к защищённому кабинету.":
      reason==="expired"?"Сеанс разблокировки истёк. Подтверди доступ заново.":
      "Кабинет приостановлен после отсутствия активности.";
    byId("session-unlock").hidden=securityEnabled;
    byId("pin-unlock-form").hidden=!securityEnabled;
    byId("security-recovery-details").hidden=!securityEnabled;
    byId("security-biometric-unlock").hidden=!securityEnabled||!tg?.BiometricManager;
    byId("session-lock-help").textContent=securityEnabled?
      "Введи PIN или используй биометрию. Суммы и запросы к серверу защищены до разблокировки.":
      "Денежные суммы останутся скрытыми. Чтобы продолжить, нажми кнопку ниже.";
    byId("pin-unlock-feedback").textContent="";
    byId("session-lock").hidden=false;
    document.body.dataset.sessionLocked="true";
    query("main").inert=true;byId("day-sheet").inert=true;byId("workspace-sheet").inert=true;
    if(securityEnabled){byId("pin-unlock-code").value="";byId("pin-unlock-code").focus();}
    else byId("session-unlock").focus();
  }
  async function unlockSession(){
    if(!sessionLocked||(securityEnabled&&!securityToken))return;
    sessionLocked=false;clearSessionLockTimer();
    if(phonePrivacyPrefs.autoVisibility||phonePrivacyPrefs.shake){phonePose=null;phonePoseCandidate=null;phonePoseSince=0;await ensurePhoneMotion(false);}
    byId("session-lock").hidden=true;document.body.dataset.sessionLocked="false";
    const day=byId("day-sheet"),workspace=byId("workspace-sheet");
    day.inert=false;workspace.inert=false;query("main").inert=!day.hidden||!workspace.hidden;
    scheduleSessionLock();
    if(connected&&navigator.onLine&&!pendingRequest&&day.hidden&&workspace.hidden)await reload();
    else if(state)paint();
    if(!workspace.hidden){if(sessionLockFocus?.isConnected&&sessionLockFocus.closest("#workspace-sheet"))sessionLockFocus.focus();else byId("workspace-heading").focus();}
    else if(!day.hidden){if(sessionLockFocus?.isConnected&&sessionLockFocus.closest("#day-sheet"))sessionLockFocus.focus();else byId("day-heading").focus();}
    else if(sessionLockFocus?.isConnected)sessionLockFocus.focus();
    else byId("shift-action").focus();
    notify("Кабинет открыт. Суммы скрыты до отдельного действия или жеста.");
  }
  function securityFeedback(message,unlock=false){
    text(unlock?"pin-unlock-feedback":"account-security-feedback",message);
  }
  function securityRefreshControls(){
    const active=connected&&securityChecked;
    byId("pin-setup-panel").hidden=!active||securityEnabled;
    byId("pin-manage-panel").hidden=!active||!securityEnabled;
    text("account-security-mode",!connected?"Telegram":!securityChecked?"Проверяем":securityEnabled?"PIN включён":"Выключена");
    text("account-security-status",!connected?"Настройка доступна только в авторизованном Telegram.":
      !securityChecked?"Проверяем сервер…":securityEnabled?
      "PIN защищает API и доступ к кабинету; биометрия может заменять PIN на этом устройстве.":
      "Отключено. Можно добавить PIN из 6 цифр и отпечаток / Face ID.");
    const bioButton=byId("security-biometric-enable");
    bioButton.disabled=!tg?.BiometricManager;
    bioButton.textContent="Подключить биометрию";
    if(!tg?.BiometricManager)text("security-biometric-status","Доступно в официальном Telegram на устройстве с биометрией.");
  }
  function showRecoveryCode(code){
    if(!/^[a-f0-9]{32}$/.test(code||""))return;
    text("pin-recovery-code",code);
    byId("pin-recovery-card").hidden=false;
    notify("PIN настроен. Сохрани одноразовый код восстановления в разделе «Ещё».");
  }
  async function telegramBiometric(){
    const manager=tg?.BiometricManager;
    if(!manager||typeof manager.init!=="function")return null;
    if(!manager.isInited){
      await new Promise(resolve=>{
        let completed=false;
        const done=()=>{if(!completed){completed=true;resolve();}};
        try{manager.init(done);}catch(e){done();}
        setTimeout(done,3500);
      });
    }
    if(!manager.isInited||!manager.isBiometricAvailable)return null;
    biometricManagerCache=manager;
    return manager;
  }
  async function biometricDevice(manager){
    const native=String(manager?.deviceId||"");
    if(!native||!crypto?.subtle)return "";
    const bytes=new TextEncoder().encode(native);
    const digest=await crypto.subtle.digest("SHA-256",bytes);
    return Array.from(new Uint8Array(digest),x=>x.toString(16).padStart(2,"0")).join("");
  }
  async function enableBiometric(){
    if(!securityEnabled||!securityToken||securityBusy)return;
    securityBusy=true;
    try{
      const bio=await telegramBiometric();
      if(!bio){securityFeedback("Биометрия недоступна в этом Telegram или на устройстве.");return;}
      if(!bio.isAccessGranted){
        const allowed=await new Promise(resolve=>{
          try{bio.requestAccess({reason:"Разблокировка кабинета «Мои смены»"},resolve);}
          catch(error){resolve(false);}
        });
        if(!allowed){securityFeedback("Доступ к биометрии не разрешён. PIN продолжает работать.");return;}
      }
      const device_id=await biometricDevice(bio);
      if(!device_id){securityFeedback("Не удалось определить безопасный идентификатор устройства.");return;}
      const raw=crypto.getRandomValues(new Uint8Array(32));
      const token=Array.from(raw,x=>x.toString(16).padStart(2,"0")).join("");
      const saved=await new Promise(resolve=>{
        try{bio.updateBiometricToken(token,result=>resolve(Boolean(result)));}
        catch(error){resolve(false);}
      });
      if(!saved){securityFeedback("Telegram не подтвердил защищённое хранение ключа.");return;}
      try{
        await securityPost({action:"biometric_add",device_id,token});
      }catch(error){
        try{bio.updateBiometricToken("",()=>{});}catch(e){}
        throw error;
      }
      securityFeedback("Биометрия включена на этом телефоне. PIN остаётся запасным способом.");
      text("security-biometric-status","Отпечаток / Face ID подключён для этого устройства.");
      byId("security-biometric-enable").textContent="Обновить биометрический ключ";
    }catch(error){securityFeedback(error.message);}
    finally{securityBusy=false;}
  }
  async function unlockWithBiometric(){
    if(!securityEnabled||securityBusy)return;
    securityBusy=true;
    try{
      const bio=await telegramBiometric();
      if(!bio||!bio.isAccessGranted||!bio.isBiometricTokenSaved){
        securityFeedback("Биометрия не настроена на этом устройстве. Используй PIN.","unlock");
        return;
      }
      const device_id=await biometricDevice(bio);
      if(!device_id)throw Error("Устройство не предоставляет биометрический идентификатор.");
      const result=await new Promise(resolve=>{
        try{bio.authenticate({reason:"Разблокировать «Мои смены»"},(ok,token)=>resolve({ok,token}));}
        catch(error){resolve({ok:false});}
      });
      if(!result.ok||!/^[a-f0-9]{64}$/.test(result.token||"")){
        securityFeedback("Биометрия не подтверждена. Попробуй снова или используй PIN.","unlock");return;
      }
      const response=await securityPost(
        {action:"unlock",method:"biometric",device_id,token:result.token});
      securityToken=response.session;
      securityFeedback("","unlock");
      await unlockSession();
    }catch(error){securityFeedback(error.message,"unlock");}
    finally{securityBusy=false;}
  }
  async function securityUnlock(method,credential){
    if(!securityEnabled||securityBusy)return;
    securityBusy=true;
    try{
      const payload=method==="pin"?{action:"unlock",method,pin:credential}:
        {action:"unlock",method:"recovery",recovery:credential};
      const response=await securityPost(payload);
      securityToken=response.session;
      if(method==="recovery")showRecoveryCode(response.recovery_code);
      securityFeedback("","unlock");
      byId("pin-unlock-form").reset();
      byId("security-recovery-form").reset();
      await unlockSession();
      if(method==="recovery")navigate("review");
    }catch(error){securityFeedback(error.message,"unlock");}
    finally{securityBusy=false;}
  }
  async function bootstrapSecurity(){
    if(!connected){
      securityChecked=true;securityEnabled=false;
      securityRefreshControls();
      return reload();
    }
    try{
      const status=await securityPost({action:"status"});
      securityEnabled=Boolean(status.enabled);
      securityChecked=true;
      securityRefreshControls();
      if(securityEnabled){lockSession("startup");return;}
      return reload();
    }catch(error){
      // Never silently fall back to an unprotected data request if the security
      // endpoint cannot be verified.
      securityChecked=false;securityToken="";
      securityRefreshControls();
      query(".shell").classList.add("auth-blocked");
      text("connection-error","Не удалось проверить защиту кабинета. Закрой и открой Mini App через Telegram снова.");
      byId("connection-banner").hidden=false;
      setStatus(false,"Проверка защиты недоступна");
      return false;
    }
  }
  function initSecuritySettings(){
    byId("pin-setup-form").onsubmit=async event=>{
      event.preventDefault();if(securityBusy||!securityChecked||securityEnabled||!connected)return;
      const pin=byId("pin-setup-code").value,confirm=byId("pin-setup-confirm").value;
      if(pin!==confirm){securityFeedback("PIN не совпадает. Повтори ввод.");return;}
      securityBusy=true;
      try{
        const response=await securityPost({action:"setup",pin});
        if(!response.enabled||!response.session||!response.recovery_code)throw Error("Настройка PIN не подтверждена.");
        securityEnabled=true;securityToken=response.session;
        byId("pin-setup-form").reset();
        securityRefreshControls();
        showRecoveryCode(response.recovery_code);
        securityFeedback("PIN включён. Теперь вход после блокировки потребует PIN или биометрию.");
      }catch(error){securityFeedback(error.message);}
      finally{securityBusy=false;}
    };
    byId("pin-unlock-form").onsubmit=event=>{
      event.preventDefault();
      const pin=byId("pin-unlock-code").value;
      if(/^[0-9]{6}$/.test(pin))securityUnlock("pin",pin);
    };
    byId("security-recovery-form").onsubmit=event=>{
      event.preventDefault();
      const code=byId("security-recovery-input").value.trim().toLowerCase();
      if(/^[a-f0-9]{32}$/.test(code))securityUnlock("recovery",code);
      else securityFeedback("Код должен содержать 32 символа.","unlock");
    };
    byId("pin-change-form").onsubmit=async event=>{
      event.preventDefault();if(!securityEnabled||!securityToken||securityBusy)return;
      const old=byId("pin-change-old").value.trim().toLowerCase();
      const pin=byId("pin-change-new").value,confirm=byId("pin-change-confirm").value;
      if(pin!==confirm){securityFeedback("Новый PIN не совпадает.");return;}
      securityBusy=true;
      try{
        const proof=/^[a-f0-9]{32}$/.test(old)?{recovery:old}:{old_pin:old};
        const response=await securityPost({action:"change_pin",...proof,new_pin:pin});
        securityToken=response.session;
        byId("pin-change-form").reset();
        byId("pin-change-details").open=false;
        showRecoveryCode(response.recovery_code);
        securityFeedback("PIN изменён. Предыдущие сессии и биометрические ключи отозваны.");
        try{biometricManagerCache?.updateBiometricToken("",()=>{});}catch(e){}
      }catch(error){securityFeedback(error.message);}
      finally{securityBusy=false;}
    };
    byId("pin-disable-form").onsubmit=async event=>{
      event.preventDefault();if(!securityEnabled||!securityToken||securityBusy)return;
      const old=byId("pin-disable-code").value.trim().toLowerCase();
      securityBusy=true;
      try{
        const proof=/^[a-f0-9]{32}$/.test(old)?{recovery:old}:{pin:old};
        await securityPost({action:"disable",...proof});
        securityToken="";securityEnabled=false;
        byId("pin-disable-form").reset();
        byId("pin-disable-details").open=false;
        byId("pin-recovery-card").hidden=true;
        text("pin-recovery-code","");
        securityRefreshControls();
        securityFeedback("Дополнительная защита выключена. Telegram-авторизация остаётся обязательной.");
        try{biometricManagerCache?.updateBiometricToken("",()=>{});}catch(e){}
      }catch(error){securityFeedback(error.message);}
      finally{securityBusy=false;}
    };
    byId("pin-copy-recovery").onclick=async()=>{
      const value=byId("pin-recovery-code").textContent;
      try{await navigator.clipboard.writeText(value);securityFeedback("Код скопирован. Сохрани его отдельно от телефона.");}
      catch(error){securityFeedback("Выдели код и сохрани его вручную. Не отправляй в чат.");}
    };
    byId("pin-recovery-saved").onclick=()=>{text("pin-recovery-code","");byId("pin-recovery-card").hidden=true;};
    byId("security-lock-now").onclick=()=>lockSession("manual");
    byId("security-biometric-enable").onclick=enableBiometric;
    byId("security-biometric-unlock").onclick=unlockWithBiometric;
    securityRefreshControls();
  }
  function savePhonePrivacyPrefs(){
    try{localStorage.setItem(phonePrivacyPrefsKey,JSON.stringify(phonePrivacyPrefs));}catch(e){}
  }
  function phoneSensorStatus(message,mode){
    text("phone-motion-status",message);
    text("phone-motion-state",mode||(phoneMotionActive?"Активен":(phonePrivacyPrefs.autoVisibility||phonePrivacyPrefs.shake?"Ожидание":"Выключен")));
  }
  function phoneHaptic(){
    if(!phonePrivacyPrefs.haptics||window.matchMedia?.("(prefers-reduced-motion: reduce)").matches)return;
    try{if(tg?.HapticFeedback?.impactOccurred)tg.HapticFeedback.impactOccurred("light");
      else if(typeof navigator.vibrate==="function")navigator.vibrate(18);}catch(e){}
  }
  function hideMoneyFromPhone(reason){
    if(!amountsVisible)return false;
    setAmountsVisible(false);phoneHaptic();notify(reason);return true;
  }
  function stopPhoneMotion(message,mode="Выключен"){
    if(phoneMotionListener){window.removeEventListener("devicemotion",phoneMotionListener);phoneMotionListener=null;}
    phoneMotionActive=false;phoneMotionSawEvent=false;phonePose=null;phonePoseCandidate=null;phonePoseSince=0;phoneGravity=null;
    phoneFlipArmed=false;phoneFlipPending=false;phoneFlipHidden=false;phoneLastFlipAt=0;
    if(phoneMotionProbeTimer!==null){clearTimeout(phoneMotionProbeTimer);phoneMotionProbeTimer=null;}
    if(message)phoneSensorStatus(message,mode);
  }
  function currentPhoneOrientation(){
    const type=screen.orientation?.type;
    if(type)return type.startsWith("portrait")?"portrait":"landscape";
    return window.matchMedia?.("(orientation: portrait)").matches?"portrait":"landscape";
  }
  function handlePhoneOrientationChange(){
    const next=currentPhoneOrientation();if(!lastPhoneOrientation){lastPhoneOrientation=next;return;}
    if(next===lastPhoneOrientation)return;lastPhoneOrientation=next;
    if(phonePrivacyPrefs.orientation)hideMoneyFromPhone("Ориентация телефона изменилась — суммы скрыты.");
  }
  function setPhoneOrientationListener(enabled){
    if(enabled&&!phoneOrientationListener){
      lastPhoneOrientation=currentPhoneOrientation();
      phoneOrientationListener=handlePhoneOrientationChange;
      window.addEventListener("orientationchange",phoneOrientationListener,{passive:true});
      phoneScreenOrientation=screen.orientation||null;
      phoneScreenOrientation?.addEventListener?.("change",phoneOrientationListener);
      phoneOrientationMedia=window.matchMedia?.("(orientation: portrait)")||null;
      phoneOrientationMedia?.addEventListener?.("change",phoneOrientationListener);
      if(phoneOrientationMedia&&!phoneOrientationMedia.addEventListener)phoneOrientationMedia.addListener?.(phoneOrientationListener);
    }else if(!enabled&&phoneOrientationListener){
      window.removeEventListener("orientationchange",phoneOrientationListener);
      phoneScreenOrientation?.removeEventListener?.("change",phoneOrientationListener);
      phoneOrientationMedia?.removeEventListener?.("change",phoneOrientationListener);
      if(phoneOrientationMedia&&!phoneOrientationMedia.removeEventListener)phoneOrientationMedia.removeListener?.(phoneOrientationListener);
      phoneOrientationListener=null;phoneScreenOrientation=null;phoneOrientationMedia=null;lastPhoneOrientation="";
    }
  }
  function handlePhoneMotion(event){
    if(!phoneMotionActive||document.hidden||sessionLocked)return;
    const now=performance.now(),g=event.accelerationIncludingGravity;
    if(!phoneMotionSawEvent){
      phoneMotionSawEvent=true;
      if(phoneMotionProbeTimer!==null){clearTimeout(phoneMotionProbeTimer);phoneMotionProbeTimer=null;}
      phoneSensorStatus("Датчик активен. Положение телефона определяется автоматически; данные остаются на устройстве.","Активен");
    }
    if(phonePrivacyPrefs.autoVisibility){
      let pose=null;
      if(g&&[g.x,g.y,g.z].every(Number.isFinite)){
        if(g.z>=7&&Math.abs(g.x)<5&&Math.abs(g.y)<5)pose="up";
        else if(g.z<=-7&&Math.abs(g.x)<5&&Math.abs(g.y)<5)pose="down";
      }
      if(pose){
        if(pose!==phonePoseCandidate){phonePoseCandidate=pose;phonePoseSince=now;}
        if(now-phonePoseSince>=420&&phonePose!==pose){
          phonePose=pose;
          if(pose==="up"){
            // Only the stable return from a previously observed face-down pose
            // completes a flip. Merely resting face-up never changes privacy.
            if(phoneFlipArmed&&phoneFlipPending&&(!phoneLastFlipAt||now-phoneLastFlipAt>=900)){
              phoneLastFlipAt=now;
              if(!phoneFlipHidden){
                phoneFlipHidden=true;
                hideMoneyFromPhone("Перевернул вниз и обратно — суммы скрыты. Повтори жест, чтобы показать.");
              }else{
                phoneFlipHidden=false;
                setAmountsVisible(true);
                if(amountsVisible){phoneHaptic();notify("Повторный полный переворот — суммы показаны.");}
              }
            }
            phoneFlipPending=false;
            phoneFlipArmed=true;
          }else if(phoneFlipArmed){
            // Arm one full cycle only once; holding the phone face-down
            // must not fire, even when the sensor sends many events.
            phoneFlipPending=true;
          }
        }
      }else{phonePoseCandidate=null;phonePoseSince=0;}
    }
    if(phonePrivacyPrefs.shake&&amountsVisible){
      let a=event.acceleration;
      if(!a||![a.x,a.y,a.z].every(Number.isFinite)){
        if(g&&[g.x,g.y,g.z].every(Number.isFinite)){
          if(!phoneGravity)phoneGravity={x:g.x,y:g.y,z:g.z};
          phoneGravity={x:phoneGravity.x*.82+g.x*.18,y:phoneGravity.y*.82+g.y*.18,z:phoneGravity.z*.82+g.z*.18};
          a={x:g.x-phoneGravity.x,y:g.y-phoneGravity.y,z:g.z-phoneGravity.z};
        }
      }
      if(a&&[a.x,a.y,a.z].every(Number.isFinite)){
        const magnitude=Math.hypot(a.x,a.y,a.z);
        if(magnitude>=phonePrivacyPrefs.shakeThreshold&&now-phoneLastShakePeak>180){
          phoneShakePeaks=now-phoneLastShakePeak<900?phoneShakePeaks+1:1;phoneLastShakePeak=now;
          if(phoneShakePeaks>=2&&now-phoneLastShakeAction>1800){
            phoneShakePeaks=0;phoneLastShakeAction=now;
            hideMoneyFromPhone("Встряхивание обнаружено — суммы скрыты.");
          }
        }
      }
    }
  }
  async function startPhoneMotion({requestPermission=true}={}){
    if(phoneMotionActive)return true;
    const Motion=window.DeviceMotionEvent;
    if(!window.isSecureContext){phoneSensorStatus("Датчики доступны только в защищённом контексте HTTPS.","Недоступен");return false;}
    if(!Motion){phoneSensorStatus("Этот браузер не предоставляет датчик движения.","Недоступен");return false;}
    try{
      let permission="granted";
      if(requestPermission&&typeof Motion.requestPermission==="function")permission=await Motion.requestPermission();
      if(permission!=="granted"){phoneSensorStatus("Доступ к датчику запрещён в настройках устройства.","Нет доступа");return false;}
      phoneMotionListener=handlePhoneMotion;window.addEventListener("devicemotion",phoneMotionListener,{passive:true});
      phoneMotionActive=true;phoneMotionSawEvent=false;phoneGravity=null;phonePose=null;phonePoseCandidate=null;phonePoseSince=0;
      phoneFlipArmed=false;phoneFlipPending=false;phoneFlipHidden=false;phoneLastFlipAt=0;
      phoneShakePeaks=0;phoneLastShakePeak=0;
      phoneSensorStatus("Включено: переверни экраном вниз и обратно вверх, чтобы скрыть; повтори этот жест, чтобы показать.","Ожидание сигнала");
      phoneMotionProbeTimer=setTimeout(()=>{
        phoneMotionProbeTimer=null;
        if(phoneMotionActive&&!phoneMotionSawEvent)phoneSensorStatus("Нет сигнала датчика. Проверь системное разрешение; автоматический режим останется включённым.","Нет сигнала");
      },3500);
      return true;
    }catch(error){
      phoneSensorStatus("Не удалось запустить датчик. Проверь разрешение движения в настройках устройства.","Ошибка");
      return false;
    }
  }
  async function ensurePhoneMotion(requestPermission=true,rollbackKey=null){
    const needed=phonePrivacyPrefs.autoVisibility||phonePrivacyPrefs.shake;
    if(!needed){stopPhoneMotion("Автоматическое управление движением выключено.","Выключен");return true;}
    const started=await startPhoneMotion({requestPermission});
    if(!started&&requestPermission&&rollbackKey){
      phonePrivacyPrefs[rollbackKey]=false;
      const control=byId(rollbackKey==="autoVisibility"?"phone-auto-visibility":"phone-shake-hide");
      if(control)control.checked=false;
      savePhonePrivacyPrefs();
    }
    return started;
  }
  function initPhonePrivacySettings(){
    const controls=[
      ["phone-auto-visibility","autoVisibility"],["phone-shake-hide","shake"],
      ["phone-orientation-hide","orientation"],["phone-haptics","haptics"]
    ];
    for(const [id,key] of controls){
      const input=byId(id);input.checked=phonePrivacyPrefs[key];
      input.onchange=async()=>{
        phonePrivacyPrefs[key]=input.checked;savePhonePrivacyPrefs();
        if(key==="orientation")setPhoneOrientationListener(input.checked);
        if(key==="autoVisibility"){
          if(input.checked){phonePose=null;phonePoseCandidate=null;phonePoseSince=0;phoneFlipArmed=false;phoneFlipPending=false;phoneFlipHidden=false;phoneLastFlipAt=0;}
          await ensurePhoneMotion(true,"autoVisibility");
        }else if(key==="shake"){
          await ensurePhoneMotion(true,"shake");
        }
      };
    }
    const threshold=byId("phone-shake-threshold");threshold.value=String(phonePrivacyPrefs.shakeThreshold);
    threshold.onchange=()=>{const value=Number(threshold.value);if(![10,14,18,22].includes(value)){threshold.value=String(phonePrivacyPrefs.shakeThreshold);return;}phonePrivacyPrefs.shakeThreshold=value;savePhonePrivacyPrefs();};
    setPhoneOrientationListener(phonePrivacyPrefs.orientation);
    phoneSensorStatus(!window.DeviceMotionEvent?"Датчик движения не поддерживается этим браузером.":"Автоматический режим выключен.","Выключен");
    if(phonePrivacyPrefs.autoVisibility||phonePrivacyPrefs.shake)ensurePhoneMotion(false);
  }
  function setAmountsVisible(visible){
    if(visible&&connected&&(!securityChecked||sessionLocked||(securityEnabled&&!securityToken)))return;
    if(amountsVisible===visible)return;
    // Privacy always wins: never leave a financial confirmation in the foreground.
    if(!visible&&!byId("modal").hidden)byId("modal-cancel").click();
    amountsVisible=visible;
    if(visible)scheduleMoneyPrivacyHide();else clearMoneyPrivacyTimer();
    paintMoneyPrivacyControls();
    if(!visible){
      // ECharts can retain monetary values in an old canvas/tool-tip until replaced.
      chartInstance?.clear();annualChartInstance?.clear();clearPaymentHistory();
      if(workspaceBlob){
        URL.revokeObjectURL(workspaceBlob);workspaceBlob=null;byId("ws-download")?.remove();
        const output=byId("ws-report-result");
        if(output){output.replaceChildren();append(output,"p","plan-note","Ссылка на отчёт закрыта вместе с суммами. Чтобы скачать файл, подготовь его заново после показа сумм.");}
      }
    }
    if(state){
      paintHome();paintTable();paintSalary();
      if(annualData&&!byId("annual-panel").hidden)paintAnnualSummary(annualData);
    }
    paintPaymentHistory();
    const day=byId("day-sheet"),workspace=byId("workspace-sheet");
    if(!day.hidden){
      const editing=Boolean(day.querySelector(".day-content form")||actualDraft?.dirty||recoveryDraft?.dirty||planDraft?.dirty||breakPanel?.dirty);
      const locked=!visible&&editing;
      day.classList.toggle("privacy-locked",locked);byId("day-privacy-lock").hidden=!locked;
      if(!editing&&state)paintDay();
    }
    if(!workspace.hidden){
      const editing=Boolean(workspaceDirty||workspace.querySelector(".workspace-scroll form"));
      const locked=!visible&&editing;
      workspace.classList.toggle("privacy-locked",locked);byId("workspace-privacy-lock").hidden=!locked;
      if(!editing&&workspaceData){
        if(workspaceView==="activity")paintActivityList();
        else renderWorkspace(workspaceView);
      }
    }
  }
  function modal(title,message){
    return new Promise(resolve=>{
      text("modal-title",title);text("modal-text",message);
      const overlay=byId("modal"),previous=document.activeElement,day=byId("day-sheet");
      overlay.hidden=false;query("main").inert=true;day.inert=true;byId("workspace-sheet").inert=true;document.body.classList.add("modal-open");
      const finish=value=>{
        overlay.hidden=true;day.inert=false;byId("workspace-sheet").inert=false;query("main").inert=!day.hidden||!byId("workspace-sheet").hidden;
        if(day.hidden&&byId("workspace-sheet").hidden)document.body.classList.remove("modal-open");
        document.removeEventListener("keydown",keys);
        byId("modal-confirm").onclick=null;byId("modal-cancel").onclick=null;
        if(previous?.isConnected)previous.focus();resolve(value);
      };
      const keys=event=>{
        if(event.key==="Escape"){event.preventDefault();finish(false);}
        if(event.key==="Tab"){event.preventDefault();(document.activeElement===byId("modal-cancel")?byId("modal-confirm"):byId("modal-cancel")).focus();}
      };
      document.addEventListener("keydown",keys);
      byId("modal-confirm").onclick=()=>finish(true);byId("modal-cancel").onclick=()=>finish(false);byId("modal-cancel").focus();
    });
  }
  function readOfflineEditDraft(){
    try{
      const value=JSON.parse(localStorage.getItem(offlineEditDraftKey)||"null");
      if(!value)return null;
      const day=value.day,revision=value.expected_revision;
      const validDay=typeof day==="string"&&/^\d{4}-\d{2}-\d{2}$/.test(day);
      const validRevision=typeof revision==="string"&&/^[a-f0-9]{64}$/.test(revision);
      const validId=value.shift_id===null||Number.isSafeInteger(value.shift_id);
      let valid=false;
      if(value.version===1&&validDay&&validRevision&&validId&&typeof value.created_at==="string"){
        if(value.kind==="actual"){
          valid=["correct_shift","add_shift","close_shift"].includes(value.action)&&
            /^\d{2}:\d{2}$/.test(value.start)&&/^\d{2}:\d{2}$/.test(value.end)&&
            Number.isInteger(value.break_minutes)&&value.break_minutes>=0&&value.break_minutes<=600&&
            typeof value.reason==="string"&&value.reason.length<=160;
        }else if(value.kind==="recovery"){
          valid=["clock_in_at","clock_out_at","move_open_start"].includes(value.action)&&
            ["now","chosen"].includes(value.mode)&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value.at);
        }
      }
      if(!valid){localStorage.removeItem(offlineEditDraftKey);return null;}
      return value;
    }catch(e){return null;}
  }
  function saveOfflineEditDraft(value){
    try{
      const old=readOfflineEditDraft();
      const identity=x=>[x.kind,x.action,x.day||"",x.shift_id??""].join("|");
      const kind=value.kind;
      const safe=kind==="actual"?{
        version:1,kind,action:value.action,day:value.day,shift_id:value.shift_id??null,
        expected_revision:value.expected_revision,start:value.start,end:value.end,
        break_minutes:Number(value.break_minutes),reason:String(value.reason||"").slice(0,160),
        before:String(value.before||"").slice(0,300),tracked:Boolean(value.tracked),
        created_at:new Date(serverNow()).toISOString()
      }:{
        version:1,kind:"recovery",action:value.action,day:value.day,shift_id:value.shift_id??null,
        expected_revision:value.expected_revision,mode:value.mode,at:value.at,
        created_at:new Date(serverNow()).toISOString()
      };
      if(old&&identity(old)!==identity(safe))return false;
      const encoded=JSON.stringify(safe);
      if(encoded.length>4096)return false;
      localStorage.setItem(offlineEditDraftKey,encoded);
      return true;
    }catch(e){return false;}
  }
  function clearOfflineEditDraft(candidate){
    const draft=readOfflineEditDraft();if(!draft)return;
    const kind=["correct_shift","add_shift","close_shift"].includes(candidate.action)?"actual":
      ["clock_in_at","clock_out_at","move_open_start"].includes(candidate.action)?"recovery":null;
    const sameId=(draft.shift_id??null)===(candidate.shift_id??null);
    const sameDay=draft.kind==="recovery"||draft.day===candidate.day;
    if(kind===draft.kind&&sameId&&sameDay){try{localStorage.removeItem(offlineEditDraftKey);}catch(e){}}
  }
  async function resumeOfflineDraft(){
    const draft=readOfflineEditDraft();if(!draft)return;
    if(!connected){notify("Открой кабинет через Telegram, чтобы проверить черновик.");return;}
    if(!navigator.onLine){notify("Нет соединения. Черновик останется на устройстве.");return;}
    if(commandBusy||pendingRequest){notify("Сначала проверь предыдущее сохранение.");return;}
    const day=draft.day||draft.at.slice(0,10),month=day.slice(0,7);
    currentMonth=month;selectedDay=day;
    const ok=await reload();
    if(!ok||!fresh||!state){notify("Не удалось проверить черновик по актуальным данным. Он сохранён.");return;}
    if(draft.kind==="actual"){
      const row=dayRows().find(item=>item.day===day);
      const item=draft.shift_id?row?.records.find(record=>record.id===draft.shift_id):null;
      if(draft.shift_id&&!item){notify("Этой смены больше нет. Черновик не отправлен; проверь архив.");return;}
      const revision=item?.revision||state.actual_editor?.revisions?.[day];
      if(revision!==draft.expected_revision){notify("Смена уже изменилась на сервере. Черновик не отправлен; удали его или создай исправление заново.");return;}
      await openCalendarDay(day);
      const freshRow=dayRows().find(item=>item.day===day);
      const freshItem=draft.shift_id?freshRow?.records.find(record=>record.id===draft.shift_id):null;
      const currentRevision=freshItem?.revision||state.actual_editor?.revisions?.[day];
      if(!freshRow||currentRevision!==draft.expected_revision){notify("Данные изменились во время проверки. Черновик сохранён, ничего не отправлено.");return;}
      openActualEditor(freshItem,freshRow,draft);
      return;
    }
    const revision=draft.action==="clock_in_at"?state.attendance_revision:state.attendance_hints?.active_revision;
    if(revision!==draft.expected_revision||(draft.shift_id&&state.active?.id!==draft.shift_id)){
      notify("Состояние смены изменилось. Черновик не отправлен; проверь его перед новым вводом.");return;
    }
    openRecovery(draft.action,null,true,{...draft,mode:"chosen"});
  }
  function draft(action){
    const deviceTime=new Date().toISOString();
    try{localStorage.setItem("workshift_draft",JSON.stringify({action,deviceTime}));}catch(e){}
    renderDrafts();
    notify("Связи нет. Это только черновик, не рабочее время. После подключения уточни часы в календаре.");
  }
  function recoveryReady(){return Boolean(connected&&state?.capabilities?.attendance_recovery);}
  function moscowLocal(value){return new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value)).replace(' ','T');}
  async function openRecovery(action,item=null,forceEarlier=false,resume=null){
    if(commandBusy||pendingRequest){if(pendingRequest)await retryWrite();return;}
    if(connected&&(!fresh||loading)){notify('Обнови данные перед отметкой.');return;}
    if(!byId('workspace-sheet').hidden){await closeWorkspace();if(!byId('workspace-sheet').hidden)return;}
    const active=state?.active,hint=state?.attendance_hints?.[action==='clock_in_at'?'in':'out'];
    const current=moscowLocal(serverNow()),planned=hint?.planned_at?moscowLocal(hint.planned_at):null;
    const editing=action==='move_open_start';
    const original=editing?moscowLocal(active.start):null;
    const chosen=editing||forceEarlier||Boolean(hint?.ask_time&&planned&&hint.difference_minutes>5);
    recoveryDraft=resume?{...resume,action,shift_id:resume.shift_id??(action==='clock_in_at'?null:(item?.id||active?.id)),
      expected_revision:resume.expected_revision,mode:'chosen',at:resume.at,hint,dirty:false,offline_saved:true}:
      {action,shift_id:action==='clock_in_at'?null:(item?.id||active?.id),
       expected_revision:action==='clock_in_at'?state?.attendance_revision:(item?.revision||state?.attendance_hints?.active_revision),
       mode:chosen?'chosen':'now',at:original||(chosen&&planned?planned:current),hint,dirty:false};
    breakPanel=null;breakSequence++;actualDraft=null;planDraft=null;dayReturnFocus=document.activeElement;
    byId('day-sheet').hidden=false;query('main').inert=true;document.body.classList.add('modal-open');
    text('day-heading',editing?'Исправить приход':action==='clock_in_at'?'Во сколько ты пришёл?':'Во сколько ты ушёл?');
    const box=byId('day-details');box.replaceChildren();
    const form=append(box,'form','recovery-form');form.id='recovery-form';
    const fields=append(form,'fieldset');fields.id='recovery-fields';
    const contextNote=append(fields,'p','plan-note',resume?'Это сохранённый черновик. Проверь дату и время: они ещё не отправлены.':editing?'Исправляется начало открытой смены. Уход останется открытым.':planned?
      'В плане: '+dateLabel(planned.slice(0,10))+' в '+planned.slice(11)+'. План — ориентир, а не подтверждение факта.':
      'Нет надёжного времени в плане. Приложение не знает, когда ты пришёл или ушёл: выбери фактическое время.');
    if(hint?.difference_minutes>5)append(fields,'p','plan-history-note','От планового времени прошло больше 5 минут. Проверь время отметки.');
    if(!editing){const modes=append(fields,'div','segmented plan-choices');modes.setAttribute('role','group');modes.setAttribute('aria-label','Время отметки');
      for(const [mode,label] of [['now','Сейчас'],['chosen','Другое время']]){const b=append(modes,'button','',label);b.type='button';b.id='recovery-'+mode;b.onclick=()=>{recoveryDraft.mode=mode;recoveryDraft.dirty=true;if(recoveryDraft.offline_saved){recoveryDraft.offline_saved=false;contextNote.textContent='Ты изменил сохранённый черновик. Новые значения ещё не сохранены.';}refresh();};}}
    const clocks=append(fields,'div','plan-times');clocks.id='recovery-clocks';
    function input(label,id,type,value){const l=append(clocks,'label','',label),e=append(l,'input');e.id=id;e.type=type;e.value=value;e.required=true;return e;}
    const date=input('Дата · Москва','recovery-date','date',recoveryDraft.at.slice(0,10));date.min='2000-01-01';date.max=moscowDay();
    const time=input('Время','recovery-time','time',recoveryDraft.at.slice(11));time.step='60';
    const note=append(fields,'p','recovery-preview');note.id='recovery-preview';note.setAttribute('aria-live','polite');
    const error=append(form,'p','plan-error');error.id='recovery-error';error.setAttribute('role','alert');
    const save=append(fields,'button','main-action','Сохранить время');save.id='recovery-save';save.type='submit';
    const cancel=append(fields,'button','text-button','Отмена');cancel.type='button';cancel.id='recovery-cancel';cancel.onclick=()=>closeDay();
    writeRetry(form,'recovery-retry');
    const refresh=()=>{
      const draft=recoveryDraft;if(!draft)return;
      if(!editing)for(const mode of ['now','chosen'])byId('recovery-'+mode).setAttribute('aria-pressed',String(draft.mode===mode));
      clocks.hidden=draft.mode==='now';date.disabled=time.disabled=clocks.hidden;
      draft.at=date.value+'T'+time.value;
      note.textContent=draft.mode==='now'?'Будет записано серверное время на момент сохранения.':'Будет записано: '+date.value+' в '+time.value+' МСК. Это ручное уточнение, оно останется в истории.';
      error.textContent='';
    };
    date.oninput=time.oninput=()=>{recoveryDraft.dirty=true;if(recoveryDraft.offline_saved){recoveryDraft.offline_saved=false;contextNote.textContent='Ты изменил сохранённый черновик. Новые значения ещё не сохранены.';}refresh();};
    form.onsubmit=async event=>{
      event.preventDefault();if(!form.reportValidity())return;
      if(!recoveryReady()){error.textContent='Демонстрация: время не сохраняется.';return;}
      const draft=recoveryDraft;
      const candidate={action:draft.action,nonce:newNonce(),shift_id:draft.shift_id,expected_revision:draft.expected_revision,mode:draft.mode,at:draft.mode==='chosen'?draft.at:null};
      if(!navigator.onLine||!fresh||loading){
        if(!saveOfflineEditDraft({kind:'recovery',action:draft.action,day:draft.at.slice(0,10),shift_id:draft.shift_id,expected_revision:draft.expected_revision,mode:draft.mode,at:draft.at})){
          error.textContent='На устройстве уже есть черновик для другой записи. Сначала удали его в разделе «Ещё».';return;
        }
        draft.offline_saved=true;draft.dirty=false;error.textContent='Черновик сохранён на устройстве. Он не отправлен и не считается отметкой. После подключения проверь дату и время.';
        contextNote.textContent='Черновик сохранён на этом устройстве. Он не отправлен и не считается отметкой. Проверь дату и время перед отправкой.';
        renderDrafts();notify('Черновик времени сохранён. Отметка пока не создана.','warning');return;
      }
      await runWrite(candidate,'Подтвердить фактическое время?',note.textContent,true);
    };
    refresh();byId('day-close').focus();query('.day-sheet').scrollTop=0;try{tg?.BackButton?.show?.();}catch(e){}
  }
  function paintRecoveryControls(parent,row,item){
    if(connected&&!state?.capabilities?.attendance_recovery)return;
    if(!item.end){const edit=append(parent,'button','secondary-action','Исправить приход');edit.type='button';edit.classList.add('edit-open-start');edit.onclick=()=>openRecovery('move_open_start',item,true);}
    const remove=append(parent,'button','text-button delete-shift','Удалить смену');remove.type='button';
    remove.onclick=()=>{
      if(!connected){notify('Демонстрация: смена не удаляется.');return;}
      runWrite({action:'delete_shift',nonce:newNonce(),shift_id:item.id,expected_revision:item.revision},'Удалить эту фактическую смену?',
        dateLabel(row.day)+' · '+item.start+'–'+(item.end||'смена открыта')+'\n'+(item.end?'Из расчёта уйдут '+formatHours(item.hours)+' и '+money(item.earnings)+'.':'Ошибочный приход и связанные перерывы будут убраны из текущего учёта.')+'\n\nПлан не меняется; день не становится выходным. Полная запись сохранится в архиве. Действие можно отменить из этой даты.');
    };
  }
  function paintDeletedShifts(box,row){
    const items=(state?.deleted_shifts||[]).filter(item=>item.day===row.day);if(!items.length)return;
    const section=append(box,'details','day-more deleted-shifts');append(section,'summary','','Удалённые смены · '+items.length);
    for(const item of items){const card=append(section,'div','ws-record');append(card,'p','',item.start+'–'+(item.end||'открытая смена'));
      const b=append(card,'button','secondary-action restore-shift','Восстановить смену');b.type='button';b.onclick=()=>runWrite({action:'restore_shift',nonce:newNonce(),event_id:item.event_id,expected_revision:item.revision},'Восстановить смену?',
        'Вернутся прежние часы и перерывы. Если они пересекаются с новой работой, восстановление не выполнится.');}
  }
  function paintOfficePhoto(box,row){
    const item=state?.office_photo?.[row.day];if(!item)return;
    const section=append(box,'section','office-photo-proposal');append(section,'h3','','По фото офисного табеля');
    const seconds=item.paid_seconds,h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=seconds%60;
    append(section,'p','plan-history-note',item.hours_decimal.replace('.',',')+' десятичных часа = '+h+' ч '+pad(m)+' мин '+pad(s)+' с. Чтение фотографии предварительное.');
    const clock=v=>new Intl.DateTimeFormat('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(new Date(v));
    append(section,'p','', 'Расчётный интервал: '+clock(item.start)+'–'+clock(item.end)+(item.end.slice(0,10)!==item.day?' · следующий день':'')+'. Перерыв '+item.break_minutes+' мин.');
    append(section,'p','plan-note','Приход взят '+({existing_record:'из текущей записи',plan:'из плана',assumed_16_00:'условно в 16:00'}[item.anchor_source])+'. Уход вычислен так, чтобы оплачиваемая длительность совпала с прочитанным числом. Это оценка, не доказательство времени прихода.');
    append(section,'p','plan-note','По фото не видно, вычтен ли перерыв. Здесь число трактуется как оплачиваемые часы, перерыв добавлен сверх них. Проверь это перед применением.');
    if(item.blocked)append(section,'p','plan-error',item.blocked);
    else {const b=append(section,'button','secondary-action apply-office-estimate','Использовать этот расчёт');b.type='button';b.onclick=()=>runWrite({action:'apply_office_estimate',nonce:newNonce(),day:row.day,expected_revision:item.revision},'Заменить часы расчётом по фото?',
      dateLabel(row.day)+'\nБудет: '+clock(item.start)+'–'+clock(item.end)+', перерыв '+item.break_minutes+' мин.\nОплачиваемая длительность: '+item.hours_decimal+' ч.\n\nЧисло прочитано с фото, приход и уход расчётные. Начисление изменится. Прежняя запись и основание останутся в журнале.');}
  }

  async function command(action){
    if(!connected){notify("Это демонстрация. Рабочие часы не изменяются.");return;}
    if(commandBusy)return;
    if(pendingRequest){await retryWrite();return;}
    if(!state){notify("Сначала дождись авторизации и загрузки данных.");return;}
    if(!state.capabilities?.writes){notify("Запись временно выключена. Обнови кабинет или используй чат.");return;}
    if(!navigator.onLine){draft(action);return;}
    if(!fresh||loading){notify("Сначала обнови состояние смены.");return;}
    if(recoveryReady()&&["in","out"].includes(action)){
      const hint=state.attendance_hints?.[action];
      const near=hint?.planned_at&&Math.abs(serverNow()-Date.parse(hint.planned_at))<=5*60000;
      if(action==="in"&&near){await runWrite({action:"clock_in_at",nonce:newNonce(),shift_id:null,expected_revision:state.attendance_revision,mode:"now",at:null},"Пришёл","",false);}
      else await openRecovery(action==="in"?"clock_in_at":"clock_out_at");
      return;
    }
    const labels={in:"Пришёл",out:"Ушёл",pause_start:"Начать перерыв",pause_end:"Закончить перерыв"};
    const candidate={action,nonce:newNonce(),shift_id:action==="in"?null:state.active?.id,break_id:action==="pause_end"?state.break?.break_id:null};
    if(state.attendance_revision)candidate.state_revision=state.attendance_revision;
    await runWrite(candidate,labels[action],"Подтвердить действие? Время запишет сервер по Москве.",action!=="in");
  }

  function shortPaidLabel(hours){
    // A short label which is visually distinct from a clock-in time (16:00).
    const minutes=Math.max(0,Math.round(hours*60));
    return Math.floor(minutes/60)+"ч"+String(minutes%60).padStart(2,"0");
  }
  function hm(value){
    if(value===null||value===undefined)return "—";
    const m=Math.round(Math.abs(value)*60);return (value<0?"−":"")+Math.floor(m/60)+":"+pad(m%60);
  }
  function signedMinutes(hours){
    const value=Math.round(hours*60);return (value>0?"+":"")+value+" мин";
  }
  function dayRows(){
    if(state?.ledger)return state.ledger;
    if(!state)return [];
    const [year,month]=currentMonth.split("-").map(Number),count=new Date(Date.UTC(year,month,0)).getUTCDate();
    return Array.from({length:count},(_,index)=>{
      const day=currentMonth+"-"+pad(index+1),records=state.shifts.filter(s=>s.day===day),closed=records.filter(s=>s.end);
      const plan=state.plan.find(p=>p.day===day)||null,opened=records.some(s=>!s.end);
      const hours=closed.length?closed.reduce((v,r)=>v+r.hours,0):null;
      const office=Object.prototype.hasOwnProperty.call(state.office,day)?state.office[day]:null;
      const delta=office!==null&&hours!==null&&!opened?hours-office:null;
      const status=opened?"open":closed.length?"worked":plan?.status==="off"?"off":plan?"planned":"empty";
      return {day,records,plan,open:opened,status,status_label:{open:"В работе",worked:"Отработано",off:"Выходной",planned:"План",empty:"Нет записей"}[status],
        hours,earnings:closed.length?closed.reduce((v,r)=>v+r.earnings,0):null,office_hours:office,delta_hours:delta,
        comparison:delta!==null?(Math.abs(delta)<.02?"matched":"difference"):"office_missing",closed_shifts:closed.length,
        plan_hours:null,plan_pay:null,future_plan:false};
    });
  }
  function setCalendarMode(mode){
    calendarMode=mode;
    byId("mode-table").setAttribute("aria-pressed",String(mode==="table"));
    byId("mode-calendar").setAttribute("aria-pressed",String(mode==="calendar"));
    byId("table-panel").hidden=mode!=="table";byId("calendar-panel").hidden=mode!=="calendar";
  }
  function paintTable(){
    const needle=tableSearch.toLowerCase().trim();
    const deletedDays=new Set((state?.deleted_shifts||[]).map(x=>x.day));
    byId("table-reset").hidden=!tableSearch&&tableFilter==="relevant"&&tableSort==="date-asc";
    let rows=dayRows().filter(row=>{
      const matches=tableFilter==="all"||tableFilter==="relevant"&&(row.records.length||row.actual_off||row.plan||row.office_hours!==null||deletedDays.has(row.day))||tableFilter==="worked"&&row.closed_shifts>0||
        tableFilter==="planned"&&row.plan||tableFilter==="review"&&(row.status==="missing"||row.comparison==="difference"||row.open)||tableFilter==="estimated"&&row.records.some(r=>r.office_estimated)||tableFilter==="deleted"&&deletedDays.has(row.day);
      const searchable=[row.day,row.day.slice(8)+"."+row.day.slice(5,7),row.status_label,
        ...row.records.map(r=>r.start+" "+(r.end||"")),row.plan?.start||"",row.plan?.end||""].join(" ").toLowerCase();
      return matches&&(!needle||searchable.includes(needle));
    });
    rows.sort((a,b)=>tableSort==="date-desc"?b.day.localeCompare(a.day):tableSort==="hours"?(b.hours??-1)-(a.hours??-1):tableSort==="earnings"?(b.earnings??-1)-(a.earnings??-1):a.day.localeCompare(b.day));
    const hours=rows.reduce((v,r)=>v+(r.hours||0),0),earnings=rows.reduce((v,r)=>v+(r.earnings||0),0);
    const summary=rows.length+" дат · факт "+formatHours(hours)+" · "+money(earnings);
    if(byId("table-summary").textContent!==summary)text("table-summary",summary);
    const body=byId("ledger-body");body.replaceChildren();byId("table-empty").hidden=rows.length!==0;
    for(const row of rows){
      const tr=append(body,"tr",row.status==="empty"&&row.office_hours===null?"is-empty":"");
      const date=append(tr,"td");date.dataset.label="День";
      const btn=append(date,"button","day-link",row.day.slice(8)+"."+row.day.slice(5,7)+" · "+new Intl.DateTimeFormat("ru-RU",{weekday:"short",timeZone:"UTC"}).format(new Date(row.day+"T12:00:00Z")));btn.onclick=()=>showDay(row.day);btn.dataset.day=row.day;btn.setAttribute("aria-label",fullDayLabel(row.day)+" · "+row.status_label);
      const plan=row.plan?.status==="off"?"Выходной":row.plan?(row.plan.start||"—")+"–"+(row.plan.end||"—"):"—";
      const factual=row.records.map(r=>r.start+"–"+(r.end||"…")+(r.next_day?" +1":"")).join("; ")||"—";
      append(btn,"span","compact-fact",row.actual_off?"Фактически выходной":row.records.length?factual:row.plan?plan:"План не задан");
      const values=[["План",plan],["Факт",factual],["Оплачиваемое время",hm(row.hours)],
        ["За часы",row.earnings===null?"—":money(row.earnings)],["Офис",hm(row.office_hours)]];
      for(const [label,value] of values){const td=append(tr,"td","",value);td.dataset.label=label;}
      const td=append(tr,"td"),status=row.comparison==="difference"?"Разница "+signedMinutes(row.delta_hours):row.status_label;
      append(td,"span","status-tag "+(row.comparison==="difference"?"difference":row.status),status);
      if(row.records.some(r=>r.office_estimated))append(td,"span","status-tag estimate-tag","Расчёт по офису");
      if(deletedDays.has(row.day))append(td,"span","status-tag archive-tag","Есть удалённые");
    }
  }
  function showDay(day){
    if(!state||!day.startsWith(currentMonth))return;
    const fromTable=document.activeElement?.classList.contains("day-link");
    breakPanel=null;breakSequence++;actualDraft=null;planDraft=null;dayReturnFocus=document.activeElement;selectedDay=day;paintCalendar();paintTable();
    dayReturnFocus=fromTable?byId("ledger-body").querySelector(`[data-day="${day}"]`):byId("calendar-grid").querySelector(`[data-day="${day}"]`);
    if(!dayReturnFocus?.isConnected)dayReturnFocus=byId("mode-table");byId("day-sheet").hidden=false;
    query("main").inert=true;document.body.classList.add("modal-open");byId("day-close").focus();
    try{tg?.BackButton?.show?.();}catch(e){}
  }
  let discardPrompt=false;
  async function closeDay(force=false){
    if(force!==true&&!byId("modal").hidden){byId("modal-cancel").click();return;}
    if(force!==true&&(commandBusy||breakPanel?.busy))return;
    if(force!==true&&!pendingRequest&&(actualDraft?.dirty||recoveryDraft?.dirty||breakPanel?.dirty||(directPlans()&&planDraft?.dirty))){
      if(discardPrompt)return;discardPrompt=true;
      const discard=await modal("Закрыть без сохранения?","Введённые изменения ещё не сохранены. Отмена вернёт тебя к форме.");
      discardPrompt=false;if(!discard)return;
    }
    actualDraft=null;planDraft=null;recoveryDraft=null;breakPanel=null;breakSequence++;
    byId("day-sheet").hidden=true;query("main").inert=false;document.body.classList.remove("modal-open");
    if(dayReturnFocus?.isConnected)dayReturnFocus.focus();
    try{tg?.BackButton?.hide?.();}catch(e){}
  }
  function paintSync(){
    const button=byId("manual-refresh");button.disabled=loading;
    if(!connected){text("sync-status","Демо · вымышленные данные");return;}
    if(loading){text("sync-status","Обновление…");return;}
    const stamp=state?.now?new Intl.DateTimeFormat("ru-RU",{timeZone:"Europe/Moscow",hour:"2-digit",minute:"2-digit"}).format(new Date(state.now)):null;
    text("sync-status",fresh&&navigator.onLine?("Обновлено "+stamp+" МСК"):(stamp?"Последние данные: "+stamp+" МСК · нет связи":"Данные не загружены"));
  }
  async function openCalendarDay(day){
    if(!/^\d{4}-\d{2}-\d{2}$/.test(day)||!state)return;
    if(connected&&(!fresh||loading)){notify("Дождись обновления данных и повтори открытие дня.");return;}
    const month=day.slice(0,7);
    if(month!==state.month){
      currentMonth=month;selectedDay=day;
      const ok=await reload();
      if(!ok||!state||state.month!==month||currentMonth!==month){notify("Не удалось открыть день. Повтори после загрузки данных.");return;}
    }
    navigate("calendar");setCalendarMode("calendar");showDay(day);
  }
  function paintAgenda(){
    const agenda=state?.agenda;byId("agenda-section").hidden=!agenda;
    if(!agenda)return;
    const next=(!state.active&&agenda.current_plan)||agenda.next_shift;
    const card=byId("next-plan");
    if(next){
      text("next-plan-label",next===agenda.current_plan?"По плану сейчас · нет отметки":"Ближайшая смена");
      text("next-plan-title",dateLabel(next.day));
      text("next-plan-time",next.start+"–"+next.end+(next.next_day?" · +1 день":""));
      card.onclick=()=>openCalendarDay(next.day);
    }else{
      text("next-plan-label","График впереди");text("next-plan-title","Будущих смен нет");
      text("next-plan-time","В ближайшие "+(agenda.search_days||90)+" дней нет предстоящих рабочих смен с указанным временем.");
      card.onclick=()=>openCalendarDay(agenda.today);
    }
    const counts=agenda.counts||{};
    text("agenda-summary",(counts.work||0)+" раб. · "+(counts.off||0)+" вых.");
    const container=byId("agenda-days");container.replaceChildren();
    for(const day of agenda.days||[]){
      const row=append(container,"button","agenda-day");row.type="button";row.dataset.day=day.day;
      const left=append(row,"span");append(left,"strong","",day.day===agenda.today?"Сегодня · "+dateLabel(day.day):dateLabel(day.day));
      const label=day.status==="actual_off"?"Факт: выходной":day.status==="open"?"Смена открыта":day.status==="worked"?"Есть фактическая смена":day.plan?.status==="off"?"Выходной":day.plan?day.start+"–"+day.end+(day.next_day?" +1":""):"План не задан";
      append(left,"small","",label);append(row,"span","arrow","›");row.onclick=()=>openCalendarDay(day.day);
    }
  }
  function openChatDestination(destination,value){
    if(["payment","reports","plan"].includes(destination)&&(!connected||state?.capabilities?.workspace)){openWorkspace(destination);return;}
    if(!connected){notify("Демо: переход в личный бот не выполняется. Реальные записи не меняются.");return;}
    if(!state||!fresh||loading||!navigator.onLine){notify("Сначала обнови данные и проверь соединение.");return;}
    const username=state.plan_editor?.bot_username||"";
    if(!/^[A-Za-z][A-Za-z0-9_]{4,31}$/.test(username)||!tg?.openTelegramLink){notify("Переход сейчас недоступен. Открой нужный раздел в чате бота.");return;}
    const month=state.month.replaceAll("-","");let payload;
    if(destination==="reports"||destination==="payment")payload="q_"+destination+"_"+month;
    else if(destination==="fix"&&/^\d{4}-\d{2}-\d{2}$/.test(value?.day||"")&&Number.isSafeInteger(value?.id)&&value.id>0)
      payload="q_fix_"+value.day.replaceAll("-","")+"_"+value.id;
    else if(destination==="day"&&/^\d{4}-\d{2}-\d{2}$/.test(value||""))payload="q_day_"+value.replaceAll("-","");
    else if(["plan","edit","home"].includes(destination))payload="q_"+destination;
    else return;
    try{tg.openTelegramLink("https://t.me/"+username+"?start="+payload);tg.close?.();}
    catch(e){notify("Не удалось перейти. Записи не изменились; открой чат вручную.");}
  }
  function returnToChat(){openChatDestination("home");}
  function drawChart(){
    const element=byId("income-chart");
    if(!state||!byId("page-salary").classList.contains("shown")||!element.clientWidth)return;
    if(!window.echarts){element.textContent="График недоступен. Все значения доступны в табеле.";return;}
    if(!chartInstance)chartInstance=echarts.init(element,null,{renderer:"canvas"});
    const rows=dayRows(),today=moscowDay(),css=getComputedStyle(document.body);
    const muted=css.getPropertyValue("--muted").trim(),ink=css.getPropertyValue("--ink").trim();
    let total=0,planned=0;
    const earned=[],future=[];
    rows.forEach(row=>{
      total+=row.earnings||0;planned+=row.future_plan?(row.plan_pay||0):0;
      earned.push(row.day<=today||row.earnings!==null?Math.round(total*100)/100:null);
      future.push(row.day>=today&&state.forecast?.future_shifts?Math.round((total+planned)*100)/100:null);
    });
    // Concealed mode renders hours only: no ruble figures in axes, tooltip or canvas.
    const hours=!amountsVisible||chartMetric==="hours";
    const moneyChart=byId("chart-money"),hoursChart=byId("chart-hours");
    moneyChart.disabled=!amountsVisible;
    moneyChart.title=amountsVisible?"График начислений":"Открой суммы глазом, чтобы выбрать рубли";
    moneyChart.setAttribute("aria-pressed",String(!hours));hoursChart.setAttribute("aria-pressed",String(hours));
    element.dataset.moneyPrivacy=amountsVisible?"unlocked":"hours-only";
    const series=hours?[
      {name:"Факт",type:"bar",data:rows.map(r=>r.hours),barMaxWidth:16,itemStyle:{borderRadius:[3,3,0,0]}},
      {name:"План · оценка",type:"bar",data:rows.map(r=>r.future_plan?r.plan_hours:null),barMaxWidth:16,itemStyle:{opacity:.55,borderRadius:[3,3,0,0]}}
    ]:[
      {name:"За часы · накоплено",type:"line",step:"end",data:earned,showSymbol:false,lineStyle:{width:2.5},areaStyle:{opacity:.08}},
      {name:"План · оценка",type:"line",step:"end",data:future,showSymbol:false,lineStyle:{width:2,type:"dashed"},connectNulls:true}
    ];
    chartInstance.setOption({animation:false,backgroundColor:"transparent",color:[css.getPropertyValue("--positive").trim(),css.getPropertyValue("--accent").trim()],
      aria:{enabled:true,description:hours?"Фактические и плановые часы по дням выбранного месяца":"Накопленные начисления за часы и отдельная оценка будущего плана"},
      textStyle:{fontFamily:"system-ui",color:ink},grid:{left:7,right:12,top:62,bottom:25,containLabel:true},
      legend:{top:3,left:3,itemWidth:14,itemHeight:8,textStyle:{color:muted,fontSize:12}},
      tooltip:{trigger:"axis",renderMode:"richText",confine:true,formatter:params=>{
        const items=params.filter(p=>p.value!==null&&p.value!==undefined);
        return (items[0]?.axisValue||"")+" "+MONTHS[Number(currentMonth.slice(5))-1]+"\n"+items.map(p=>p.seriesName+": "+(hours?formatHours(p.value):money(p.value))).join("\n");
      }},xAxis:{type:"category",data:rows.map(r=>r.day.slice(8)),boundaryGap:hours,
        axisLabel:{color:muted,fontSize:12,interval:Math.max(1,Math.floor(rows.length/7))},axisLine:{lineStyle:{color:css.getPropertyValue("--line").trim()}},axisTick:{show:false}},
      yAxis:{type:"value",axisLabel:{color:muted,fontSize:12,formatter:v=>hours?v+" ч":Math.abs(v)>=1000?(v/1000)+" тыс.":String(v)},
        splitLine:{lineStyle:{color:css.getPropertyValue("--line").trim(),type:"dashed"}}},series},true);
    const surface=chartInstance.getZr();
    if(chartPointerHandler)surface.off("click",chartPointerHandler);
    chartPointerHandler=event=>{
      const point=[event.offsetX,event.offsetY];
      if(!chartInstance.containPixel({gridIndex:0},point))return;
      const value=chartInstance.convertFromPixel({gridIndex:0},point);
      const index=Math.round(Array.isArray(value)?value[0]:value);
      if(rows[index])showDay(rows[index].day);
    };
    surface.on("click",chartPointerHandler);
    chartInstance.resize();
    text("chart-note",!amountsVisible?"Суммы скрыты: на графике только часы. Чтобы увидеть рубли, нажми на глаз вверху.":hours?"Факт — оплачиваемые часы. План — оценка с заданным перерывом.":"Только начисления за часы. Пунктир — будущий план, не заработанные деньги. Нажми на точку для разбора дня.");
  }


  let annualSequence=0,annualData=null,annualController=null,annualChartInstance=null;
  let paymentHistorySequence=0,paymentHistoryController=null,paymentHistoryData=null;
  let paymentHistorySearch="",paymentHistoryMonth="all",paymentHistoryPeriod="all";
  function resetPaymentHistoryFilters(){
    paymentHistorySearch="";paymentHistoryMonth="all";paymentHistoryPeriod="all";
    const search=byId("payment-history-search"),month=byId("payment-history-month"),period=byId("payment-history-period");
    if(search)search.value="";if(month)month.value="all";if(period)period.value="all";
    const reset=byId("payment-history-reset");if(reset)reset.hidden=true;
  }
  function clearPaymentHistory(){
    paymentHistorySequence++;paymentHistoryController?.abort();paymentHistoryController=null;paymentHistoryData=null;
    resetPaymentHistoryFilters();
    const filters=byId("payment-history-filters");if(filters)filters.hidden=true;
    const list=byId("payment-history-list");if(list){list.replaceChildren();list.hidden=true;}
    const button=byId("payment-history-load");if(button){button.disabled=!amountsVisible;button.textContent="Загрузить историю";button.setAttribute("aria-expanded","false");}
    text("payment-history-note",amountsVisible?"Нажми, чтобы загрузить последние 60 поступлений.":"История выплат скрыта. Покажи суммы, чтобы открыть её.");
  }
  function paintPaymentHistory(){
    const list=byId("payment-history-list"),button=byId("payment-history-load"),filters=byId("payment-history-filters");
    if(!amountsVisible){if(list){list.replaceChildren();list.hidden=true;}filters.hidden=true;resetPaymentHistoryFilters();if(button){button.disabled=true;button.textContent="Загрузить историю";button.setAttribute("aria-expanded","false");}text("payment-history-note","История выплат скрыта. Покажи суммы, чтобы открыть её.");return;}
    button.disabled=false;
    if(paymentHistoryData===null){list.hidden=true;filters.hidden=true;button.textContent="Загрузить историю";button.setAttribute("aria-expanded","false");text("payment-history-note","Последние 60 поступлений по всем рабочим месяцам. Загружаются только после нажатия.");return;}
    list.replaceChildren();list.hidden=false;filters.hidden=false;button.textContent="Обновить историю";button.setAttribute("aria-expanded","true");
    const rows=paymentHistoryData.payments||[];
    const months=[...new Set(rows.map(row=>row.work_month))].sort((a,b)=>b.localeCompare(a));
    if(paymentHistoryMonth!=="all"&&!months.includes(paymentHistoryMonth))paymentHistoryMonth="all";
    const monthSelect=byId("payment-history-month");monthSelect.replaceChildren();
    const allMonths=append(monthSelect,"option","","Все месяцы");allMonths.value="all";
    for(const month of months){const option=append(monthSelect,"option","",monthName(month));option.value=month;}
    monthSelect.value=paymentHistoryMonth;
    const searchInput=byId("payment-history-search");
    if(document.activeElement!==searchInput)searchInput.value=paymentHistorySearch;
    byId("payment-history-period").value=paymentHistoryPeriod;
    const needle=paymentHistorySearch.trim().toLocaleLowerCase("ru-RU");
    const filtered=rows.filter(row=>{
      if(paymentHistoryMonth!=="all"&&row.work_month!==paymentHistoryMonth)return false;
      if(paymentHistoryPeriod!=="all"&&String(row.period)!==paymentHistoryPeriod)return false;
      if(!needle)return true;
      const part=row.period===1?"За 1–15 число":"За 16–конец месяца";
      const searchable=[row.work_month,monthName(row.work_month),row.paid_on,localDateLabel(row.paid_on),part,row.note||"",String(row.amount_rub)].join(" ").toLocaleLowerCase("ru-RU");
      return searchable.includes(needle);
    }).sort((a,b)=>b.paid_on.localeCompare(a.paid_on)||b.work_month.localeCompare(a.work_month));
    const total=filtered.reduce((sum,row)=>sum+Number(row.amount_rub||0),0);
    const activeFilters=Boolean(needle)||paymentHistoryMonth!=="all"||paymentHistoryPeriod!=="all";
    byId("payment-history-reset").hidden=!activeFilters;
    const countLabel=activeFilters?"Показано "+filtered.length+" из "+rows.length+" "+plural(rows.length,["записи","записей","записей"]):rows.length+" "+plural(rows.length,["запись","записи","записей"]);
    text("payment-history-note",rows.length?countLabel+" · "+money(total)+(paymentHistoryData.has_more&&!activeFilters?" · показаны последние 60":""):"Поступлений пока нет.");
    if(!rows.length){append(list,"p","muted","История выплат пока пустая.");return;}
    if(!filtered.length){append(list,"p","muted","По фильтру записей нет. Измени запрос или сбрось фильтры.");return;}
    const groups=new Map();
    for(const row of filtered){if(!groups.has(row.work_month))groups.set(row.work_month,[]);groups.get(row.work_month).push(row);}
    for(const [month,items] of groups){
      const section=append(list,"section","payment-history-month");append(section,"h3","",monthName(month));
      for(const row of items){
        const card=append(section,"article","payment-history-item"),left=append(card,"div");
        append(left,"strong","",localDateLabel(row.paid_on));
        append(left,"small","",row.period===1?"За 1–15 число":"За 16–конец месяца");
        const right=append(card,"div","payment-history-amount");append(right,"strong","",money(row.amount_rub));
        if(row.note)append(card,"p","plan-note",row.note);
      }
    }
  }
  async function loadPaymentHistory(){
    if(!amountsVisible){notify("Сначала покажи суммы кнопкой с глазом.");return;}
    if(!connected){notify("История выплат появится после подключения Telegram.");return;}
    const sequence=++paymentHistorySequence;paymentHistoryController?.abort();paymentHistoryController=new AbortController();
    const button=byId("payment-history-load");button.disabled=true;text("payment-history-note","Загружаю историю выплат…");
    try{
      const result=await post("/mini/api/workspace",{view:"payment_history",limit:60},paymentHistoryController.signal);
      if(sequence!==paymentHistorySequence||!amountsVisible||sessionLocked)return;
      if(!Array.isArray(result.payments)||result.payments.length>60||result.payments.some(r=>!/^\d{4}-\d{2}$/.test(r.work_month||"")||!/^\d{4}-\d{2}-\d{2}$/.test(r.paid_on||"")||![1,2].includes(r.period)||!Number.isFinite(Number(r.amount_rub))))throw Error("Сервер вернул некорректную историю выплат.");
      paymentHistoryData=result;paintPaymentHistory();
    }catch(error){if(sequence!==paymentHistorySequence||error.name==="AbortError")return;text("payment-history-note",error.message+" Попробуй ещё раз.");}
    finally{if(sequence===paymentHistorySequence)button.disabled=!amountsVisible;}
  }
  function drawAnnualChart(){
    const element=byId("annual-finance-chart");
    if(!annualData||!amountsVisible||byId("annual-panel").hidden){annualChartInstance?.clear();element.hidden=true;text("annual-finance-note","Покажи суммы, чтобы увидеть годовую динамику.");return;}
    element.hidden=false;
    if(!window.echarts){text("annual-finance-note","Годовой график недоступен. Сравнение доступно в списке месяцев.");return;}
    if(!annualChartInstance)annualChartInstance=echarts.init(element,null,{renderer:"canvas"});
    const css=getComputedStyle(document.body),months=annualData.months;
    annualChartInstance.setOption({animation:false,backgroundColor:"transparent",
      color:[css.getPropertyValue("--accent").trim(),css.getPropertyValue("--positive").trim()],
      aria:{enabled:true,description:"Начислено с корректировками и фактически записанные поступления по месяцам года."},
      textStyle:{fontFamily:"system-ui",color:css.getPropertyValue("--ink").trim()},
      grid:{left:8,right:12,top:38,bottom:28,containLabel:true},
      legend:{top:2,left:4,itemWidth:14,itemHeight:8,textStyle:{color:css.getPropertyValue("--muted").trim(),fontSize:12}},
      tooltip:{trigger:"axis",renderMode:"richText",confine:true,formatter:params=>params.map(p=>p.seriesName+": "+money(p.value)).join("\n")},
      xAxis:{type:"category",data:months.map(m=>MONTHS[Number(m.month.slice(5))-1].slice(0,3)),axisLabel:{color:css.getPropertyValue("--muted").trim()},axisLine:{lineStyle:{color:css.getPropertyValue("--line").trim()}},axisTick:{show:false}},
      yAxis:{type:"value",axisLabel:{color:css.getPropertyValue("--muted").trim(),formatter:v=>Math.abs(v)>=1000?(v/1000)+" тыс.":String(v)},splitLine:{lineStyle:{color:css.getPropertyValue("--line").trim(),type:"dashed"}}},
      series:[{name:"Начислено",type:"bar",data:months.map(m=>m.total),barMaxWidth:18,itemStyle:{borderRadius:[3,3,0,0]}},
        {name:"Получено",type:"bar",data:months.map(m=>m.received),barMaxWidth:18,itemStyle:{borderRadius:[3,3,0,0]}}]},true);
    annualChartInstance.resize();text("annual-finance-note","Начисления и внесённые поступления по рабочим месяцам. Прогноз в график не включён.");
  }
  function paintAnnualSummary(data){
    const year=data.year;
    text("annual-status",year+" · "+data.shifts+" закрытых смен"+(connected?"":" · вымышленные данные"));
    const metrics=byId("annual-metrics");metrics.replaceChildren();
    const annualDifference=Number(data.total||0)-Number(data.received||0);
    for(const [label,value] of [
      ["Отработано",formatHours(data.hours)],
      ["Начислено с корректировками",money(data.total)],
      ["Получено",money(data.received)],
      [annualDifference>0.01?"Ещё не получено":annualDifference< -0.01?"Получено сверх начисленного":"Разница",money(Math.abs(annualDifference))]
    ]){
      const card=append(metrics,"article","metric");append(card,"span","",label);append(card,"strong","",value);
    }
    const months=byId("annual-months");months.replaceChildren();
    data.months.forEach(m=>{
      const row=append(months,"button","annual-month");
      const left=append(row,"span");append(left,"strong","",MONTHS[Number(m.month.slice(5))-1]);append(left,"small","",formatHours(m.hours)+" · "+m.shifts+" смен");
      const right=append(row,"span");append(right,"strong","",money(m.earned));append(right,"small","","За часы · получено "+money(m.received));
      row.onclick=()=>{currentMonth=m.month;selectedDay=m.month+"-01";resetAnnual();reload();};
    });
    requestAnimationFrame(drawAnnualChart);
  }
  function resetAnnual(){
    annualSequence++;annualController?.abort();annualData=null;
    byId("annual-metrics").replaceChildren();byId("annual-months").replaceChildren();annualChartInstance?.clear();
    byId("annual-finance-chart").hidden=true;text("annual-finance-note","");text("annual-status","");byId("annual-panel").hidden=true;
    byId("annual-toggle").setAttribute("aria-expanded","false");text("annual-toggle","Открыть год");
  }
  async function loadAnnual(){
    if(!state)return;
    const sequence=++annualSequence,year=Number(state.month.slice(0,4));
    annualController?.abort();annualController=new AbortController();
    byId("annual-panel").hidden=false;byId("annual-toggle").setAttribute("aria-expanded","true");
    text("annual-toggle","Скрыть год");text("annual-status","Загружаю "+year+"…");
    try{
      let data;
      if(connected)data=await post("/mini/api/analytics",{year},annualController.signal);
      else{
        const months=Array.from({length:12},(_,i)=>{const d=demoState(year+"-"+pad(i+1));return {month:d.month,...d.summary};});
        data={year,months,hours:months.reduce((v,m)=>v+m.hours,0),shifts:months.reduce((v,m)=>v+m.shifts,0),earned:months.reduce((v,m)=>v+m.earned,0),total:months.reduce((v,m)=>v+m.total,0),received:months.reduce((v,m)=>v+m.received,0)};
      }
      if(sequence!==annualSequence||!state||Number(state.month.slice(0,4))!==year)return;
      if(data.year!==year||!Array.isArray(data.months)||data.months.length!==12)throw Error("Неверный ответ годовой сводки.");
      annualData=data;paintAnnualSummary(data);
    }catch(error){
      if(sequence!==annualSequence||error.name==="AbortError")return;
      if(error.status===403){resetAnnual();closeDay(true);state=null;bulkDraft=null;paintBulk();query(".shell").classList.add("auth-blocked");byId("connection-banner").hidden=false;text("connection-error",error.message);setStatus(false,"Нет доступа");return;}
      text("annual-status",error.message+" Закрой и открой год, чтобы повторить.");
    }
  }
  byId("annual-toggle").onclick=()=>{if(byId("annual-panel").hidden)loadAnnual();else resetAnnual();};
  byId("payment-history-load").onclick=loadPaymentHistory;
  byId("payment-history-search").oninput=event=>{paymentHistorySearch=event.target.value;paintPaymentHistory();};
  byId("payment-history-month").onchange=event=>{paymentHistoryMonth=event.target.value;paintPaymentHistory();};
  byId("payment-history-period").onchange=event=>{paymentHistoryPeriod=event.target.value;paintPaymentHistory();};
  byId("payment-history-reset").onclick=()=>{resetPaymentHistoryFilters();paintPaymentHistory();};
  paintPaymentHistory();

  document.querySelectorAll("[data-nav]").forEach(btn=>{
    btn.addEventListener("click",()=>{if(btn.dataset.mode)setCalendarMode(btn.dataset.mode);navigate(btn.dataset.nav);});
    btn.addEventListener("keydown",e=>{if((e.key==="Enter"||e.key===" ")&&btn.tagName!=="BUTTON"){e.preventDefault();navigate(btn.dataset.nav);}});
  });

  byId("mode-table").onclick=()=>setCalendarMode("table");
  byId("mode-calendar").onclick=()=>setCalendarMode("calendar");
  byId("table-filter").onchange=e=>{tableFilter=e.target.value;paintTable();};
  byId("table-sort").onchange=e=>{tableSort=e.target.value;paintTable();};
  byId("table-search").oninput=e=>{tableSearch=e.target.value;paintTable();};
  byId("chart-money").onclick=()=>{if(!amountsVisible){notify("Покажи суммы глазом, чтобы увидеть график начислений.");return;}chartMetric="money";byId("chart-money").setAttribute("aria-pressed","true");byId("chart-hours").setAttribute("aria-pressed","false");drawChart();};
  byId("chart-hours").onclick=()=>{chartMetric="hours";byId("chart-money").setAttribute("aria-pressed","false");byId("chart-hours").setAttribute("aria-pressed","true");drawChart();};
  byId("day-close").onclick=closeDay;
  byId("day-sheet").onclick=e=>{if(e.target===byId("day-sheet"))closeDay();};
  byId("return-chat").onclick=returnToChat;
  document.addEventListener("keydown",e=>{
    if(!byId("modal").hidden)return;
    if(e.altKey&&e.shiftKey&&e.key.toLowerCase()==="h"&&amountsVisible){e.preventDefault();setAmountsVisible(false);notify("Суммы скрыты сочетанием Alt+Shift+H.");return;}
    if(byId("day-sheet").hidden)return;
    if(e.key==="Escape"){e.preventDefault();closeDay();}
    if(e.key==="Tab"){
      const items=[...byId("day-sheet").querySelectorAll("button:not(:disabled),input:not(:disabled),summary")].filter(el=>el.getClientRects().length&&getComputedStyle(el).visibility!=="hidden"),first=items[0],last=items.at(-1);
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
      else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
    }
  });
  window.addEventListener("resize",()=>{chartInstance?.resize();annualChartInstance?.resize();});
  const readingObserver=new ResizeObserver(()=>{
    const large=parseFloat(getComputedStyle(document.documentElement).fontSize)>=24&&innerWidth<600;
    document.documentElement.classList.toggle("large-reflow",large);
  });
  readingObserver.observe(document.documentElement);
  const navObserver=new ResizeObserver(entries=>{const h=entries[0].target.getBoundingClientRect().height;document.documentElement.style.setProperty("--nav-height",h+"px");});
  navObserver.observe(query(".bottom-nav"));
  try{tg?.BackButton?.onClick?.(()=>!byId("workspace-sheet").hidden?closeWorkspace():closeDay());}catch(e){}

  // All routine entry forms stay in this Mini App. Chat remains an explicit optional link.
  let workspaceData=null,workspaceView="money",workspaceDirty=false,workspaceBusy=false,workspaceSequence=0,workspaceFocus=null,workspaceBlob=null,workspaceSection="money",workspaceLeaving=false;
  const workspaceKinds=["save_payment","save_adjustment","void_money","restore_money","set_rate","remove_future_rate","set_default_break","create_series","cancel_series"];
  const adjustmentNames={bonus:"Премия",allowance:"Доплата",reimbursement:"Возмещение",deduction:"Удержание"};
  function workspaceError(message){text("workspace-error",message);}
  function workspaceNavigation(){
    const labels={money:"Деньги",plan:"График",reports:"Отчёты",settings:"Настройки",activity:"Журнал"};
    text("workspace-context",labels[workspaceSection]||"Мои смены");
    document.querySelectorAll("#workspace-nav button").forEach(b=>{
      const active=b.dataset.section===workspaceSection;b.setAttribute("aria-current",active?"page":"false");
      b.disabled=commandBusy||workspaceBusy||Boolean(pendingRequest);
    });
  }
  async function switchWorkspaceSection(section){
    if(commandBusy||workspaceBusy||pendingRequest||workspaceLeaving)return;
    workspaceLeaving=true;
    try{
      if(workspaceDirty&&!await modal("Уйти без сохранения?","Введённые значения ещё не сохранены. Отмена оставит форму открытой."))return;
      workspaceDirty=false;workspaceSection=section;renderWorkspace(section);
      byId("workspace-heading").focus({preventScroll:true});
    }finally{workspaceLeaving=false;}
  }
  function showWorkspaceBack(section){
    const button=byId("workspace-back");button.hidden=false;
    button.textContent=section==="settings"?"‹ Ставка и перерыв":"‹ Все записи денег";
    button.onclick=()=>switchWorkspaceSection(section);
  }
  function localDateLabel(value){
    if(!/^\d{4}-\d{2}-\d{2}$/.test(value||""))return String(value||"");
    return new Intl.DateTimeFormat("ru-RU",{day:"numeric",month:"short",year:"numeric",timeZone:"UTC"}).format(new Date(value+"T12:00:00Z"));
  }
  function monthName(value){return MONTHS[Number(value.slice(5))-1]+" "+value.slice(0,4);}
  function normalAmount(value){return String(value).replace(/[\s\u00a0\u202f]/g,"").replace(",",".");}
  function validateAmount(input){
    const value=normalAmount(input.value);
    input.setCustomValidity(!value?"":!/^\d{1,7}(?:\.\d{1,2})?$/.test(value)||Number(value)<=0||Number(value)>1000000?"Введи сумму от 0,01 до 1 000 000 ₽, не больше двух знаков после запятой.":"");
  }
  function workspaceFieldsBusy(){
    workspaceNavigation();
    byId("workspace-sheet").setAttribute("aria-busy",String(commandBusy||workspaceBusy));
    const blocked=commandBusy||Boolean(pendingRequest)||workspaceBusy;
    byId("workspace-content").querySelectorAll("fieldset,button,#activity-category").forEach(e=>e.disabled=blocked);
    byId("workspace-close").disabled=commandBusy||workspaceBusy;
    byId("workspace-refresh").disabled=blocked;
    byId("workspace-retry").hidden=!pendingRequest;
    byId("workspace-retry").disabled=commandBusy||!fresh||loading||!navigator.onLine;
  }
  function workspaceHeading(title){text("workspace-heading",title);workspaceError("");byId("workspace-sheet").querySelector(".workspace-scroll").scrollTop=0;byId("workspace-back").hidden=true;workspaceNavigation();if(toastTask)clearTimeout(toastTask);byId("toast").hidden=true;}
  function wsButton(parent,label,handler,cls="secondary-action"){
    const button=append(parent,"button",cls,label);button.type="button";button.onclick=handler;return button;
  }
  function wsInput(parent,label,key,type,value){
    const caption=append(parent,"label","ws-label",label),input=append(caption,"input");
    input.type=type;input.name=key;input.id="ws-"+key;input.value=value??"";return input;
  }
  function wsSelect(parent,label,key,choices,value){
    const caption=append(parent,"label","ws-label",label),select=append(caption,"select");select.name=key;select.id="ws-"+key;
    for(const [id,title] of choices){const option=append(select,"option","",title);option.value=id;}
    select.value=String(value);return select;
  }
  function workspaceDemo(){return {month:state?.month||currentMonth,today:moscowDay(),revision:"",payments:[],adjustments:[],archives:[],rates:[],current_rate:280,default_break_minutes:30,series:[]};}
  async function openWorkspace(kind="money"){
    if(commandBusy)return;
    if(connected&&(!state||!fresh||loading)){notify("Сначала дождись загрузки данных.");return;}
    if(!byId("day-sheet").hidden){await closeDay();if(!byId("day-sheet").hidden)return;}
    workspaceFocus=document.activeElement;workspaceDirty=false;workspaceView=kind;workspaceSection=["payment","adjustment","money"].includes(kind)?"money":kind;
    byId("workspace-sheet").hidden=false;query("main").inert=true;document.body.classList.add("modal-open");
    byId("workspace-close").focus();try{tg?.BackButton?.show?.();}catch(e){}
    await loadWorkspace(kind);
  }
  async function loadWorkspace(view=workspaceView){
    workspaceView=view;workspaceDirty=false;workspaceHeading("Загрузка…");
    const sequence=++workspaceSequence,box=byId("workspace-content");box.replaceChildren();
    append(box,"p","muted","Загружаем актуальные данные…");workspaceBusy=true;workspaceFieldsBusy();
    try{
      const [result,activity]=await Promise.all([
        connected?post("/mini/api/workspace",{month:state.month}):Promise.resolve(workspaceDemo()),
        view==="activity"?(connected?post("/mini/api/workspace",activityPayload()):Promise.resolve(demoActivity())):Promise.resolve(null)
      ]);
      if(activity)result.activity=activity;
      if(sequence!==workspaceSequence||byId("workspace-sheet").hidden)return false;
      workspaceData=result;renderWorkspace(view);return true;
    }catch(error){
      if(sequence!==workspaceSequence||byId("workspace-sheet").hidden)return false;
      box.replaceChildren();workspaceHeading("Не удалось загрузить");workspaceError(error.message);
      wsButton(box,"Повторить",()=>loadWorkspace(view));
      if(error.status===403){await closeWorkspace(true);await reload();}
      return false;
    }finally{if(sequence===workspaceSequence){workspaceBusy=false;workspaceFieldsBusy();}}
  }
  async function closeWorkspace(force=false){
    if(force!==true&&!byId("modal").hidden){byId("modal-cancel").click();return;}
    if(force!==true&&(commandBusy||workspaceBusy))return;
    if(force!==true&&workspaceLeaving)return;
    if(force!==true&&workspaceDirty&&!pendingRequest){
      workspaceLeaving=true;const discard=await modal("Закрыть без сохранения?","Изменения ещё не записаны. Отмена вернёт тебя к форме.");workspaceLeaving=false;if(!discard)return;
    }
    workspaceSequence++;activityEpoch++;workspaceDirty=false;workspaceData=null;workspaceBusy=false;
    if(workspaceBlob){URL.revokeObjectURL(workspaceBlob);workspaceBlob=null;}
    byId("workspace-sheet").hidden=true;byId("workspace-content").replaceChildren();workspaceError("");
    query("main").inert=!byId("day-sheet").hidden;if(byId("day-sheet").hidden)document.body.classList.remove("modal-open");
    if(workspaceFocus?.isConnected&&force!==true)workspaceFocus.focus();try{tg?.BackButton?.hide?.();}catch(e){}
  }
  function wsForm(box,label,submitHandler){
    const returnSection=workspaceSection;
    const form=append(box,"form","ws-form"),fields=append(form,"fieldset"),area=append(fields,"div","ws-fields");form.id="workspace-form";
    form.oninput=()=>{workspaceDirty=true;workspaceError("");};
    const buttons=append(fields,"div","ws-actions"),save=append(buttons,"button","main-action",label);save.type="submit";save.id="ws-save";
    wsButton(buttons,"Отмена",async()=>{if(workspaceDirty&&!await modal("Отменить ввод?","Введённые значения не будут сохранены."))return;workspaceDirty=false;if(returnSection==="plan")await closeWorkspace();else renderWorkspace(returnSection);});
    form.onsubmit=async event=>{event.preventDefault();workspaceError("");if(commandBusy||pendingRequest||workspaceBusy)return;try{await submitHandler(Object.fromEntries(new FormData(form)),fields);}catch(error){workspaceError(error.message);}};
    return {form,fields,area,save};
  }
  function renderWorkspace(view){
    if(view!=="activity")activityEpoch++;
    workspaceSection=["payment","adjustment","money"].includes(view)?"money":view;
    workspaceView=view;workspaceDirty=false;const box=byId("workspace-content");box.replaceChildren();workspaceError("");
    if(!connected)append(box,"p","plan-history-note","Демонстрация. Формы можно посмотреть; реальные записи не сохраняются.");
    if(!amountsVisible&&["money","settings","activity"].includes(view))append(box,"p","money-privacy-tip","Суммы скрыты. Нажми на глаз. Время автоскрытия настраивается в разделе «Ещё».");
    if(view==="payment")renderMoneyForm(box,"payment");
    else if(view==="adjustment")renderMoneyForm(box,"adjustment");
    else if(view==="settings")renderWorkspaceSettings(box);
    else if(view==="plan")renderSeries(box);
    else if(view==="reports")renderReports(box);
    else if(view==="activity")renderActivity(box);
    else renderMoneyList(box);
    workspaceFieldsBusy();
  }
  function moneyLabel(kind,row){return kind==="payment"?`${money(row.amount_rub)} · ${row.paid_on} · ${row.period===1?"1–15":"16–конец"} · за ${row.work_month}`:`${adjustmentNames[row.category]} · ${money(row.amount_rub)} · ${row.day}`;}
  function renderMoneyList(box){
    workspaceHeading("Записи денег");append(box,"p","workspace-period",monthName(workspaceData.month));
    const actions=append(box,"div","context-actions");wsButton(actions,"+ Поступление",()=>renderWorkspace("payment"));wsButton(actions,"+ Доплата / удержание",()=>renderWorkspace("adjustment"));
    for(const [kind,title,rows] of [["payment","Полученные деньги",workspaceData.payments],["adjustment","Корректировки начисления",workspaceData.adjustments]]){
      append(box,"h3","ws-section",title);
      if(!rows.length)append(box,"p","muted","За выбранный месяц записей нет.");
      for(const row of rows){const card=append(box,"article","ws-record");append(card,"strong","ws-record-amount",money(row.amount_rub));append(card,"p","ws-record-meta",kind==="payment"?"Поступление · "+localDateLabel(row.paid_on)+" · "+(row.period===1?"1–15 число":"16–конец месяца"):adjustmentNames[row.category]+" · "+localDateLabel(row.day));if(row.note)append(card,"p","plan-note",amountsVisible?row.note:"Комментарий скрыт");
        const actions=append(card,"div","context-actions");wsButton(actions,"Изменить",()=>{box.replaceChildren();renderMoneyForm(box,kind,row);workspaceFieldsBusy();},"small-switch");
        wsButton(actions,"Отменить запись",async()=>{await runWrite({action:"void_money",kind,record_id:row.id,expected_revision:workspaceData.revision,nonce:newNonce()},"Исключить ошибочную запись?",moneyLabel(kind,row)+"\n\n"+(kind==="payment"?"Изменится только учёт полученных денег. Это не возврат и не банковская операция.":"Эта корректировка перестанет входить в начисление.")+"\nПолная запись останется в архиве; её можно восстановить.");},"text-button");
      }
    }
    if(workspaceData.archives.length){const details=append(box,"details","day-more");append(details,"summary","","Отменённые записи · "+workspaceData.archives.length);
      for(const archive of workspaceData.archives){const row=append(details,"article","ws-record");append(row,"p","",moneyLabel(archive.kind,archive.old));wsButton(row,"Восстановить",()=>runWrite({action:"restore_money",event_id:archive.event_id,expected_revision:workspaceData.revision,nonce:newNonce()},"Восстановить запись?",moneyLabel(archive.kind,archive.old)+"\nОна снова войдёт в соответствующие итоги."),"small-switch");}
    }
    append(box,"p","plan-note","Начисления и поступления — разные показатели. Никаких операций с банковским счётом.");
  }
  function renderMoneyForm(box,kind,old=null){
    workspaceView="money";workspaceHeading((old?"Изменить":"Добавить")+(kind==="payment"?" поступление":" корректировку"));showWorkspaceBack("money");
    if(!amountsVisible){
      append(box,"p","money-privacy-tip","Денежная форма закрыта для посторонних. Чтобы проверить сумму и сохранить запись, покажи суммы кнопкой с глазом.");
      wsButton(box,"Показать суммы и продолжить",()=>{setAmountsVisible(true);box.replaceChildren();renderMoneyForm(box,kind,old);workspaceFieldsBusy();},"main-action");
      return;
    }
    append(box,"p","plan-note",kind==="payment"?"Запись о деньгах, которые уже получены. Часы и начисления не изменятся.":"Отдельная сумма к начислению. Приход, уход и полученные деньги не меняются.");
    if(old)append(box,"p","actual-before","Было: "+moneyLabel(kind,old));
    const expected=workspaceData.revision,month=workspaceData.month,today=workspaceData.today;
    const {area}=wsForm(box,"Сохранить",async values=>{
      const candidate={action:kind==="payment"?"save_payment":"save_adjustment",record_id:old?.id??null,amount:normalAmount(values.amount),note:values.note,expected_revision:expected,nonce:newNonce()};
      if(kind==="payment")Object.assign(candidate,{work_month:values.work_month,period:Number(values.period),paid_on:values.paid_on});
      else Object.assign(candidate,{day:values.day,category:values.category});
      const after=kind==="payment"?`${candidate.amount} ₽ · ${candidate.paid_on} · за ${candidate.work_month}, часть ${candidate.period}`:`${adjustmentNames[candidate.category]} · ${candidate.amount} ₽ · ${candidate.day}`;
      await runWrite(candidate,old?"Подтвердить исправление?":"Сохранить запись?",(old?"Было: "+moneyLabel(kind,old)+"\n\n":"")+"Будет: "+after+(candidate.note?"\nКомментарий: "+candidate.note:"")+"\n\n"+(kind==="payment"?"Это запись поступления, не начисление и не банковский перевод.":"Корректировка изменит итог начисления. Реальные часы останутся прежними."));
    });
    if(kind==="payment"){
      const m=wsInput(area,"За рабочий месяц","work_month","month",old?.work_month||month);m.required=true;m.min="2000-01";m.max="2100-12";
      wsSelect(area,"Часть месяца","period",[[1,"1–15 число"],[2,"16–конец месяца"]],old?.period||1);
      const d=wsInput(area,"Дата получения","paid_on","date",old?.paid_on||today);d.required=true;d.min="2000-01-01";d.max=today;
    }else{
      wsSelect(area,"Вид суммы","category",Object.entries(adjustmentNames),old?.category||"bonus");
      const d=wsInput(area,"За какую дату","day","date",old?.day||(month===today.slice(0,7)?today:month+"-01"));d.required=true;d.min="2000-01-01";d.max=today;
    }
    const amount=wsInput(area,"Сумма, ₽","amount","text",old?.amount_rub||"");amount.inputMode="decimal";amount.required=true;amount.maxLength=18;amount.placeholder="0,00";amount.oninput=()=>validateAmount(amount);amount.onblur=()=>{if(amount.value)amount.value=normalAmount(amount.value).replace(".",",");validateAmount(amount);};validateAmount(amount);
    wsInput(area,"Комментарий (необязательно)","note","text",old?.note||"").maxLength=150;
  }
  function renderWorkspaceSettings(box){
    workspaceHeading("Ставка и перерыв");append(box,"p","plan-note","Эти настройки не переписывают прошлые смены и их начисления.");
    const rate=append(box,"article","ws-record");append(rate,"h3","","Сейчас: "+money(workspaceData.current_rate)+"/ч");wsButton(rate,"Новая ставка с будущей даты",()=>rateForm());
    const br=append(box,"article","ws-record");append(br,"h3","","Перерыв новых смен: "+workspaceData.default_break_minutes+" мин");wsButton(br,"Изменить перерыв",()=>{
      box.replaceChildren();workspaceHeading("Перерыв новых смен");showWorkspaceBack("settings");const expected=workspaceData.revision,before=workspaceData.default_break_minutes;
      append(box,"p","plan-note","Только для новых смен. Уже открытая смена и история не изменятся.");
      const {area}=wsForm(box,"Сохранить",v=>runWrite({action:"set_default_break",minutes:Number(v.minutes),expected_revision:expected,nonce:newNonce()},"Изменить перерыв?",`Было: ${before} мин. Будет: ${v.minutes} мин.\nТолько новые смены.`));
      const input=wsInput(area,"Минуты","minutes","number",before);input.required=true;input.min=0;input.max=600;input.step=1;workspaceFieldsBusy();
    });
    const upcoming=workspaceData.rates.filter(r=>r.effective_from>workspaceData.today);
    if(upcoming.length)append(box,"h3","ws-section","Запланированные ставки");
    for(const r of upcoming){const card=append(box,"article","ws-record");append(card,"strong","",r.effective_from+" · "+money(r.rate_rub)+"/ч");wsButton(card,"Изменить",()=>rateForm(r),"small-switch");wsButton(card,"Отменить будущую ставку",()=>runWrite({action:"remove_future_rate",effective_from:r.effective_from,expected_revision:workspaceData.revision,nonce:newNonce()},"Отменить будущую ставку?",`Ставка ${money(r.rate_rub)}/ч с ${r.effective_from} будет убрана. Продолжит действовать предыдущая ставка. Прошлые начисления сохраняются.`),"text-button");}
    const history=append(box,"details","day-more");append(history,"summary","","История ставок · только просмотр");
    for(const r of workspaceData.rates.filter(r=>r.effective_from<=workspaceData.today))append(history,"p","plan-note",r.effective_from+" · "+money(r.rate_rub)+"/ч");
    function rateForm(old){
      box.replaceChildren();workspaceHeading("Ставка с будущей даты");showWorkspaceBack("settings");
      if(!amountsVisible){
        append(box,"p","money-privacy-tip","Ставка скрыта. Чтобы проверить или изменить её, покажи суммы кнопкой с глазом.");
        wsButton(box,"Показать суммы и продолжить",()=>{setAmountsVisible(true);rateForm(old);},"main-action");return;
      }
      const expected=workspaceData.revision;
      const tomorrow=new Date(workspaceData.today+"T12:00:00Z");tomorrow.setUTCDate(tomorrow.getUTCDate()+1);const min=tomorrow.toISOString().slice(0,10);
      append(box,"p","plan-note","Ставка применяется по дате начала смены. Сегодняшние и прошлые начисления сохранятся.");
      const {area}=wsForm(box,"Сохранить ставку",v=>runWrite({action:"set_rate",effective_from:v.effective_from,amount:v.amount.replace(",","."),expected_revision:expected,nonce:newNonce()},"Сохранить будущую ставку?",`С ${v.effective_from}: ${v.amount} ₽/ч.\nПрошлые начисления не пересчитаются.`));
      const d=wsInput(area,"Начнёт действовать","effective_from","date",old?.effective_from||min);d.required=true;d.min=min;d.max="2100-12-31";d.readOnly=Boolean(old);
      const a=wsInput(area,"Рублей в час","amount","text",old?.rate_rub||workspaceData.current_rate);a.required=true;a.inputMode="decimal";a.pattern="[0-9]{1,6}([.,][0-9]{1,2})?";workspaceFieldsBusy();
    }
  }
  function renderSeries(box){
    workspaceHeading("Повторяющийся график");append(box,"p","plan-note","Сначала предпросмотр. Существующие планы, ручные исключения и фактическая работа не заменяются.");
    const {form,area,save}=wsForm(box,"Посмотреть даты",async values=>{
      const spec={start:values.start,end:values.end,pattern:values.pattern,weekdays:[...form.querySelectorAll('[name=weekday]:checked')].map(e=>Number(e.value)),start_time:values.start_time,end_time:values.end_time};
      workspaceBusy=true;workspaceFieldsBusy();
      try{
        if(!connected){workspaceError("Демо: график не создаётся. Реальные даты не меняются.");return;}
        const preview=await post("/mini/api/preview",{kind:"series",spec});
        if(byId("workspace-sheet").hidden)return;
        previewBox.replaceChildren();append(previewBox,"strong","",`${preview.days.length} дат: ${preview.shift_count} рабочих, ${preview.off_count} выходных`);
        append(previewBox,"p","plan-note",`Пропущено занятых дат: ${preview.skipped.length}. Все изменения сохранятся вместе.`);
        const dates=append(previewBox,"details","day-more");append(dates,"summary","","Все даты и пропуски");
        for(const d of preview.days)append(dates,"p","plan-note",d.day+" · "+(d.status==="shift"?d.start_time+"–"+d.end_time:"Выходной"));
        for(const d of preview.skipped)append(dates,"p","plan-note",d.day+" · пропуск: "+d.reason);
        if(preview.days.length)wsButton(previewBox,"Сохранить график",()=>runWrite({action:"create_series",spec:preview.spec,expected_revision:preview.digest,nonce:newNonce()},"Создать повторяющийся график?",`${preview.spec.start} — ${preview.spec.end}\n${preview.spec.start_time}–${preview.spec.end_time}\n${preview.shift_count} рабочих и ${preview.off_count} выходных.\n${preview.skipped.length} занятых дат останутся без изменений.\nЭто только план, не отработанные часы.`),"main-action").id="ws-series-save";
      }finally{workspaceBusy=false;workspaceFieldsBusy();}
    });
    const tomorrow=new Date(workspaceData.today+"T12:00:00Z");tomorrow.setUTCDate(tomorrow.getUTCDate()+1);const first=tomorrow.toISOString().slice(0,10);tomorrow.setUTCDate(tomorrow.getUTCDate()+27);
    for(const [key,label,val] of [["start","Первый день цикла",first],["end","Последний день",tomorrow.toISOString().slice(0,10)]]){const x=wsInput(area,label,key,"date",val);x.required=true;x.min=workspaceData.today;x.max="2100-12-31";}
    const pattern=wsSelect(area,"Повторение","pattern",[["2x2","2 рабочих / 2 выходных"],["3x3","3 рабочих / 3 выходных"],["5x2","5 рабочих / 2 выходных от первой даты"],["weekly","По дням недели"]],"2x2");
    const weekdays=append(area,"div","ws-weekdays");weekdays.hidden=true;
    ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"].forEach((name,i)=>{const l=append(weekdays,"label","",name),c=append(l,"input");c.type="checkbox";c.name="weekday";c.value=i;c.checked=i<5;});
    pattern.onchange=()=>{weekdays.hidden=pattern.value!=="weekly";};
    for(const [key,label,val] of [["start_time","Начало смены","16:00"],["end_time","Окончание смены","02:00"]]){const t=wsInput(area,label,key,"time",val);t.required=true;t.step=60;}
    const previewBox=append(box,"section","ws-preview");previewBox.id="ws-series-preview";form.addEventListener("input",()=>previewBox.replaceChildren());
    const details=append(box,"details","day-more");append(details,"summary","","Созданные серии · "+workspaceData.series.length);
    for(const series of workspaceData.series){const card=append(details,"article","ws-record");append(card,"strong","",series.spec.start+" — "+series.spec.end);append(card,"p","plan-note",series.spec.pattern+" · "+(series.status==="active"?"Действует":"Отменена"));
      if(series.status==="active")wsButton(card,"Отменить будущую часть",async()=>{
        if(commandBusy||pendingRequest||workspaceBusy)return;
        workspaceBusy=true;workspaceFieldsBusy();
        try{const preview=await post("/mini/api/preview",{kind:"cancel_series",series_id:series.id});workspaceBusy=false;
          await runWrite({action:"cancel_series",series_id:series.id,expected_revision:preview.digest,nonce:newNonce()},"Отменить будущую часть графика?",`Будет снято дат: ${preview.days.length}. Сохранится: ${preview.kept.length}.\nПрошедшие, начавшиеся и изменённые вручную дни не удаляются.\n`+preview.days.join(", "));
        }catch(error){workspaceError(error.message);}finally{workspaceBusy=false;workspaceFieldsBusy();}
      },"small-switch");
    }
  }
  function renderReports(box){
    workspaceHeading("Скачать отчёт");append(box,"p","plan-note","Файл формируется здесь. Он содержит срез данных; редактирование скачанного файла не меняет бот.");
    const form=append(box,"form","ws-form"),fields=append(form,"fieldset"),area=append(fields,"div","ws-fields");
    const month=wsInput(area,"Период","report_month","month",workspaceData.month);month.min="2000-01";month.max="2100-12";month.required=true;
    wsSelect(area,"Формат","report_format",[["xlsx","Excel за месяц"],["pdf","PDF за месяц"],["annual_pdf","PDF за выбранный год"]],"xlsx");
    const button=append(fields,"button","main-action","Подготовить файл");button.id="ws-report-build";button.type="submit";
    const output=append(box,"div","ws-preview");output.id="ws-report-result";
    form.oninput=()=>{output.replaceChildren();if(workspaceBlob){URL.revokeObjectURL(workspaceBlob);workspaceBlob=null;}};
    form.onsubmit=async event=>{
      event.preventDefault();if(workspaceBusy||commandBusy)return;
      if(!amountsVisible){workspaceError("Суммы скрыты. Покажи их глазом перед скачиванием отчёта с денежными данными.");return;}
      if(!connected){workspaceError("Демо: личные файлы недоступны.");return;}
      workspaceBusy=true;workspaceFieldsBusy();workspaceError("");output.replaceChildren();const sequence=workspaceSequence;
      try{
        const result=await post("/mini/api/report",{month:month.value,format:form.elements.report_format.value});
        if(sequence!==workspaceSequence||byId("workspace-sheet").hidden)return;
        if(!amountsVisible){workspaceError("Суммы скрыты. Отчёт не открыт; покажи суммы и подготовь файл заново.");return;}
        if(workspaceBlob)URL.revokeObjectURL(workspaceBlob);
        const raw=Uint8Array.from(atob(result.base64),c=>c.charCodeAt(0));workspaceBlob=URL.createObjectURL(new Blob([raw],{type:result.mime}));
        append(output,"p","plan-note","Файл готов. Нажми кнопку скачивания; сохранение файла выполняет Telegram или браузер.");
        const link=append(output,"a","main-action ws-download","Скачать "+result.name);link.href=workspaceBlob;link.download=result.name;link.id="ws-download";
      }catch(error){workspaceError(error.message);}finally{workspaceBusy=false;workspaceFieldsBusy();}
    };
  }
  async function workspaceAfterWrite(candidate){
    if(!workspaceKinds.includes(candidate.action)||byId("workspace-sheet").hidden)return;
    workspaceDirty=false;
    const savedMonth=candidate.action==="save_payment"?candidate.work_month:candidate.action==="save_adjustment"?candidate.day?.slice(0,7):null;
    if(savedMonth&&savedMonth!==state?.month){currentMonth=savedMonth;selectedDay=savedMonth+"-01";await reload();}
    await loadWorkspace(["create_series","cancel_series"].includes(candidate.action)?"plan":["set_rate","remove_future_rate","set_default_break"].includes(candidate.action)?"settings":"money");
  }

  document.querySelectorAll("#workspace-nav button").forEach(b=>b.onclick=()=>switchWorkspaceSection(b.dataset.section));
  function fitDialogs(){
    const viewport=window.visualViewport;
    const height=viewport?.height||window.innerHeight,offset=viewport?.offsetTop||0;
    if(viewport&&Math.abs(viewport.scale-1)>.01)return; // Never fight pinch zoom.
    document.documentElement.style.setProperty("--dialog-height",Math.round(height)+"px");
    document.documentElement.style.setProperty("--dialog-top",Math.round(offset)+"px");
  }
  window.visualViewport?.addEventListener("resize",fitDialogs);window.visualViewport?.addEventListener("scroll",fitDialogs);
  window.addEventListener("resize",fitDialogs);fitDialogs();
  byId("workspace-close").onclick=()=>closeWorkspace();
  byId("workspace-refresh").onclick=async()=>{if(commandBusy||workspaceBusy||pendingRequest)return;if(workspaceDirty&&!await modal("Загрузить актуальные данные?","Несохранённый ввод будет заменён данными сервера."))return;await loadWorkspace(workspaceView);};
  byId("workspace-retry").onclick=retryWrite;
  byId("workspace-sheet").onclick=e=>{if(e.target===byId("workspace-sheet"))closeWorkspace();};
  document.querySelectorAll("[data-workspace]").forEach(button=>button.onclick=()=>openWorkspace(button.dataset.workspace));
  document.addEventListener("keydown",e=>{
    if(byId("workspace-sheet").hidden||!byId("modal").hidden)return;
    if(e.key==="Escape"){e.preventDefault();closeWorkspace();}
    if(e.key==="Tab"){
      const items=[...byId("workspace-sheet").querySelectorAll("button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href],summary")].filter(x=>x.getClientRects().length&&!x.closest("fieldset:disabled")),first=items[0],last=items.at(-1);
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
    }
  });
  // Read-only history: business summaries only, never raw event payloads.
  let activityDay=null,activityCategory="all",activityRows=[],activityCursor=null,activityMore=false,activityEpoch=0;
  let calendarFocusDay=null,calendarKeyBusy=false,dayNavigationBusy=false;
  const fullDayLabel=day=>new Intl.DateTimeFormat("ru-RU",{weekday:"long",day:"numeric",month:"long",year:"numeric",timeZone:"UTC"}).format(new Date(day+"T12:00:00Z"));
  function shiftedDay(day,step){const d=new Date(day+"T12:00:00Z");d.setUTCDate(d.getUTCDate()+step);return d.toISOString().slice(0,10);}
  function activityPayload(before){const value={view:"activity",category:activityCategory};if(activityDay)value.day=activityDay;if(before)value.before_id=before;return value;}
  function demoActivity(){
    const day=activityDay||currentMonth+"-10";
    return {items:[{id:3,at:day+"T19:05:00+03:00",category:"time",title:"Уход уточнён",day,fields:[{label:"Время",value:"18:50"}],source:"Кабинет",manually_timed:true},{id:2,at:day+"T09:00:00+03:00",category:"time",title:"Приход отмечен",day,fields:[{label:"Время",value:"09:00"}],source:"Кабинет"}].filter(x=>activityCategory==="all"||x.category===activityCategory),has_more:false,next_before_id:null,read_only:true};
  }
  async function openActivity(day=null){
    if(commandBusy||workspaceBusy)return;
    activityDay=day;activityCategory="all";activityEpoch++;
    await openWorkspace("activity");
  }
  function renderActivity(box){
    workspaceHeading(activityDay?"История дня":"История изменений");
    append(box,"p","plan-note",activityDay?fullDayLabel(activityDay):"Последние действия, новые сверху. Время внесения отдельно от даты смены.");
    const info=append(box,"details","activity-help");append(info,"summary","","Как читать историю");
    const tip=append(info,"p","activity-explanation","Это журнал внесённых действий, не текущее состояние дня. Исправления и восстановление доступны в карточке дня или в разделе денег.");
    tip.id="activity-explanation";
    const filter=wsSelect(box,"Показать","activity-category",[["all","Все изменения"],["time","Рабочее время"],["plan","Расписание"],["money","Деньги и настройки"]],activityCategory);
    filter.id="activity-category";filter.onchange=()=>{activityCategory=filter.value;loadActivityPage(false);};
    const list=append(box,"ol","activity-list");list.id="activity-list";list.setAttribute("aria-label","История действий");
    const status=append(box,"p","plan-note");status.id="activity-status";status.setAttribute("role","status");status.setAttribute("aria-live","polite");
    const more=wsButton(box,"Показать более ранние",()=>loadActivityPage(true));more.id="activity-more";
    const data=workspaceData.activity||demoActivity();activityRows=data.items;activityCursor=data.next_before_id;activityMore=data.has_more;
    paintActivityList();
  }
  function paintActivityList(){
    const list=byId("activity-list");if(!list)return;list.replaceChildren();
    for(const item of activityRows){
      const entry=append(list,"li","activity-item");entry.dataset.eventId=String(item.id);entry.tabIndex=-1;
      append(entry,"h3","",item.title);
      if(item.at){const time=append(entry,"time","activity-time",new Intl.DateTimeFormat("ru-RU",{dateStyle:"medium",timeStyle:"short",timeZone:"Europe/Moscow"}).format(new Date(item.at))+" МСК · внесено");time.dateTime=item.at;}
      if(item.day)append(entry,"p","plan-note","Дата учёта: "+localDateLabel(item.day));
      if(item.fields.length){const dl=append(entry,"dl","activity-fields");for(const field of item.fields){
      append(dl,"dt","",field.label);
      const sensitive=item.category==="money"||/[\d\s,.]+\s*₽/.test(String(field.value));
      append(dl,"dd","",!amountsVisible&&sensitive?"•••• ₽":field.value);
    }}
      if(item.estimated)append(entry,"p","estimate-tag","Расчёт по офисному источнику, не точная отметка устройства");
      else if(item.manually_timed)append(entry,"p","plan-note","Время указано вручную");
      if(item.day)wsButton(entry,"Открыть день",async()=>{await closeWorkspace();if(byId("workspace-sheet").hidden)await openCalendarDay(item.day);},"text-button");
    }
    text("activity-status",activityRows.length?"Показано действий: "+activityRows.length+(activityMore?". Есть более ранние.":"."):activityMore?"В этой части журнала совпадений нет. Можно посмотреть более ранние записи.":"Для этого фильтра записей пока нет.");
    byId("activity-more").hidden=!activityMore;
  }
  async function loadActivityPage(appendRows){
    if(workspaceBusy||commandBusy||pendingRequest)return;
    const sequence=++activityEpoch,workspace=workspaceSequence,oldCount=appendRows?activityRows.length:0;
    workspaceBusy=true;workspaceFieldsBusy();workspaceError("");
    try{
      const data=connected?await post("/mini/api/workspace",activityPayload(appendRows?activityCursor:null)):demoActivity();
      if(sequence!==activityEpoch||workspace!==workspaceSequence||workspaceView!=="activity"||byId("workspace-sheet").hidden)return;
      activityRows=appendRows?[...activityRows,...data.items.filter(x=>!activityRows.some(old=>old.id===x.id))]:data.items;
      activityCursor=data.next_before_id;activityMore=data.has_more;workspaceData.activity={...data,items:activityRows};paintActivityList();
      if(appendRows&&activityRows.length>oldCount)byId("activity-list").children[oldCount]?.focus();
    }catch(error){
      if(sequence!==activityEpoch||workspace!==workspaceSequence)return;
      workspaceError(error.message);
      if(error.status===403){await closeWorkspace(true);await reload();}
    }finally{if(sequence===activityEpoch){workspaceBusy=false;workspaceFieldsBusy();}}
  }
  function restoreCalendarTabStop(focusDay=null){
    const buttons=[...byId("calendar-grid").querySelectorAll("button:not(:disabled)")];
    const preferred=focusDay||calendarFocusDay||selectedDay;
    const target=buttons.find(b=>b.dataset.day===preferred)||buttons.find(b=>b.dataset.day===moscowDay())||buttons[0];
    buttons.forEach(b=>b.tabIndex=b===target?0:-1);
    if(target){calendarFocusDay=target.dataset.day;if(focusDay)target.focus({preventScroll:true});}
  }
  async function calendarKeyboard(event){
    const button=event.target.closest("#calendar-grid button");if(!button||event.altKey||event.ctrlKey||event.metaKey)return;
    const key=event.key,day=button.dataset.day;
    if(!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","Home","End","PageUp","PageDown"].includes(key))return;
    event.preventDefault();if(calendarKeyBusy||loading||commandBusy)return;
    let target=day;
    if(key.startsWith("Arrow"))target=shiftedDay(day,{ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7}[key]);
    else if(key==="Home"||key==="End"){const weekday=(new Date(day+"T12:00:00Z").getUTCDay()+6)%7;target=shiftedDay(day,key==="Home"?-weekday:6-weekday);}
    else{const d=new Date(day+"T12:00:00Z"),n=d.getUTCDate();d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()+(key==="PageUp"?-1:1));const last=new Date(Date.UTC(d.getUTCFullYear(),d.getUTCMonth()+1,0)).getUTCDate();d.setUTCDate(Math.min(n,last));target=d.toISOString().slice(0,10);}
    if(target<"2000-01-01"||target>"2100-12-31")return;
    if(bulkDraft&&(target.slice(0,7)!==currentMonth||target<moscowDay()))return;
    calendarKeyBusy=true;
    try{
      if(target.slice(0,7)!==currentMonth){currentMonth=target.slice(0,7);calendarFocusDay=target;const loaded=await reload();if(!loaded||state?.month!==target.slice(0,7))return;}
      const el=byId("calendar-grid").querySelector(`[data-day="${target}"]:not(:disabled)`);
      if(el){calendarFocusDay=target;restoreCalendarTabStop(target);el.scrollIntoView({block:"nearest",behavior:"instant"});}
    }finally{calendarKeyBusy=false;}
  }
  async function browseDay(step){
    if(dayNavigationBusy||commandBusy||loading||pendingRequest||recoveryDraft)return;
    const target=shiftedDay(selectedDay,step);if(target<"2000-01-01"||target>"2100-12-31")return;
    dayNavigationBusy=true;
    try{await closeDay();if(!byId("day-sheet").hidden)return;await openCalendarDay(target);if(!byId("day-sheet").hidden)byId("day-heading").focus();}
    finally{dayNavigationBusy=false;}
  }
  byId("calendar-grid").addEventListener("keydown",calendarKeyboard);
  byId("calendar-grid").addEventListener("focusin",event=>{const b=event.target.closest("button[data-day]");if(b){calendarFocusDay=b.dataset.day;restoreCalendarTabStop();}});
  document.querySelectorAll("[data-activity]").forEach(button=>button.onclick=()=>openActivity());

  byId("write-retry").onclick=retryWrite;
  byId("bulk-retry").onclick=retryWrite;
  byId("retry-connection").onclick=()=>reload();
  byId("month-today").onclick=()=>{currentMonth=moscowDay().slice(0,7);selectedDay=moscowDay();reload();};
  byId("manual-refresh").onclick=async()=>{const pending=reload();paintSync();await pending;paintSync();};
  byId("table-reset").onclick=()=>{tableSearch="";tableFilter="relevant";tableSort="date-asc";byId("table-search").value="";byId("table-filter").value="relevant";byId("table-sort").value="date-asc";paintTable();};
  document.querySelectorAll("[data-chat]").forEach(button=>button.onclick=()=>openChatDestination(button.dataset.chat));

  byId("month-jump").onchange=event=>{
    const input=event.currentTarget,value=input.value;
    if(!/^\d{4}-\d{2}$/.test(value)||!input.checkValidity()){notify("Выбери месяц с 2000 по 2100 год.");return;}
    currentMonth=value;selectedDay=value+"-01";reload();
  };
  byId("month-prev").onclick=()=>changeMonth(-1);
  byId("month-next").onclick=()=>changeMonth(1);
  byId("calendar-export").onclick=exportCalendar;
  byId("shift-action").onclick=()=>command(state?.active?"out":"in");
  byId("break-action").onclick=()=>{if(state?.break?.on_break)command("pause_end");};
  document.querySelectorAll("[data-money-toggle]").forEach(button=>button.onclick=()=>setAmountsVisible(!amountsVisible));
  const moneyPrivacySelect=byId("money-privacy-timeout");
  moneyPrivacySelect.value=String(moneyPrivacyIdleMs/1000);
  moneyPrivacySelect.onchange=()=>{
    const value=Number(moneyPrivacySelect.value)*1000;
    if(!moneyPrivacyTimeoutOptions.includes(value)){moneyPrivacySelect.value=String(moneyPrivacyIdleMs/1000);return;}
    moneyPrivacyIdleMs=value;try{localStorage.setItem("workshift_money_privacy_timeout_v1",String(value));}catch(e){}
    if(amountsVisible)scheduleMoneyPrivacyHide();else clearMoneyPrivacyTimer();
    notify(value===0?"Автоскрытие по бездействию выключено. При сворачивании Telegram суммы всё равно скроются.":"Автоскрытие сумм: "+(value<60000?(value/1000)+" секунд":(value/60000)+" мин."));
  };
  const sessionLockSelect=byId("session-lock-timeout");
  sessionLockSelect.value=String(sessionLockMinutes);
  sessionLockSelect.onchange=()=>{
    const value=Number(sessionLockSelect.value);
    if(!sessionLockOptions.includes(value)){sessionLockSelect.value=String(sessionLockMinutes);return;}
    sessionLockMinutes=value;try{localStorage.setItem("workshift_session_lock_minutes_v1",String(value));}catch(e){}
    scheduleSessionLock();
    notify(value===0?"Экранная блокировка выключена. Суммы всё равно скрываются при сворачивании.":"Блокировка кабинета: "+value+" мин. бездействия; при сворачивании — сразу.");
  };
  byId("session-unlock").onclick=unlockSession;
  initSecuritySettings();
  initPhonePrivacySettings();
  for(const eventName of ["pointerdown","keydown","touchstart","wheel"]){
    document.addEventListener(eventName,()=>{
      if(sessionLocked)return;
      scheduleSessionLock();
      if(amountsVisible)scheduleMoneyPrivacyHide();
    },{passive:true});
  }
  paintMoneyPrivacyControls(); // The first render starts concealed: no flash of amounts.
  const themeToggle=()=>setTheme(document.body.dataset.theme==="light"?"dark":"light");
  byId("theme-switch").onclick=themeToggle;byId("theme-row-switch").onclick=themeToggle;
  function setReading(large){document.documentElement.dataset.reading=large?"large":"normal";byId("text-size-switch").setAttribute("aria-pressed",String(large));text("text-size-switch",large?"Выключить":"Включить");try{localStorage.setItem("workshift_reading",large?"large":"normal");}catch(e){}requestAnimationFrame(()=>chartInstance?.resize());}
  byId("text-size-switch").onclick=()=>setReading(document.documentElement.dataset.reading!=="large");
  const longShiftSelect=byId("long-shift-threshold");longShiftSelect.value=String(longShiftThreshold);
  longShiftSelect.onchange=()=>{
    const value=Number(longShiftSelect.value);if(![0,10,12,14,16,20].includes(value)){longShiftSelect.value=String(longShiftThreshold);return;}
    longShiftThreshold=value;try{localStorage.setItem("workshift_long_shift_hours_v1",String(value));}catch(e){}
    paintLongShiftWarning();notify(value?"Напоминание включено после "+value+" ч. Смена не закроется автоматически.":"Напоминание о долгой смене выключено.");
  };
  try{setReading(localStorage.getItem("workshift_reading")==="large");}catch(e){setReading(false);}
  try{setTheme(localStorage.getItem("workshift_theme")|| (tg?.colorScheme==="light"?"light":"dark"));}catch(e){setTheme("dark");}
  window.addEventListener("offline",()=>{byId("offline-banner").hidden=false;setStatus(false,"Нет сети");paintSync();});
  window.addEventListener("online",()=>{byId("offline-banner").hidden=true;reload();});
  if(tg){
    tg.ready();tg.expand();

  }
  const previewTab=new URLSearchParams(window.location.search).get("tab");
  if(["calendar","salary","review"].includes(previewTab))navigate(previewTab);
  document.addEventListener("visibilitychange",()=>{
    if(document.hidden){
      setAmountsVisible(false);
      stopPhoneMotion("Датчик приостановлен в фоне. После возврата он запустится автоматически.","Пауза");
      if(sessionLockMinutes>0||securityEnabled)lockSession("background");
    }else{
      if(phonePrivacyPrefs.autoVisibility||phonePrivacyPrefs.shake)ensurePhoneMotion(false);
      if(!sessionLocked){scheduleSessionLock();backgroundRefresh(15000);}
    }
  });
  window.addEventListener("pagehide",()=>{
    setAmountsVisible(false);stopPhoneMotion("Датчик временно приостановлен. Он запустится автоматически при возврате.","Пауза");
    setPhoneOrientationListener(false);if(sessionLockMinutes>0||securityEnabled)lockSession("background");
  });
  window.addEventListener("pageshow",()=>{
    if(!document.hidden){
      if(!sessionLocked)scheduleSessionLock();
      setPhoneOrientationListener(phonePrivacyPrefs.orientation);
      if(phonePrivacyPrefs.autoVisibility||phonePrivacyPrefs.shake)ensurePhoneMotion(false);
    }
  });
  setInterval(()=>backgroundRefresh(60000),60000);
  setInterval(tick,1000);
  document.body.dataset.sessionLocked="false";
  scheduleSessionLock();
  restoreWrite();
  bootstrapSecurity();
})();
