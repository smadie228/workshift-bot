/* Мои смены · Telegram Mini App. All personal data comes from an authenticated API. */
(() => {
  "use strict";
  const tg=window.Telegram?.WebApp;
  const initData=tg?.initData || "";
  const connected=Boolean(initData);
  const byId=id=>document.getElementById(id);
  const query=sel=>document.querySelector(sel);
  const money=n=>new Intl.NumberFormat("ru-RU",{maximumFractionDigits:2,minimumFractionDigits:0}).format(n||0)+" ₽";
  const pad=n=>String(n).padStart(2,"0");
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
  let commandBusy=false;
  let pendingRequest=null;
  const serverNow=()=>Date.now()+serverOffset;
  const moscowDay=()=>new Intl.DateTimeFormat("sv-SE",{timeZone:"Europe/Moscow",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date(serverNow()));
  byId("demo-banner").hidden=connected;
  byId("shift-action").disabled=connected;


  function demoState(month){
    const entries=[
      {day:month+"-03",start:"16:02",end:"02:11",next_day:true,break_minutes:30,hours:9.65,earnings:2702,source:"manual"},
      {day:month+"-06",start:"16:14",end:"01:55",next_day:true,break_minutes:30,hours:9.18,earnings:2571,source:"button"},
      {day:month+"-10",start:"16:09",end:"02:10",next_day:true,break_minutes:30,hours:9.52,earnings:2666,source:"button"},
      {day:month+"-12",start:"17:05",end:"02:00",next_day:true,break_minutes:30,hours:8.42,earnings:2357,source:"button"}
    ];
    return {
      month, now:new Date().toISOString(),
      summary:{hours:36.77,shifts:4,earned:10296,total:10696,received:6000,
        additional:{bonus:400,allowance:0,reimbursement:0,deduction:0,net_adjustment:400}},
      periods:[{period:1,hours:36.77,earnings:10296,shifts:4},{period:2,hours:0,earnings:0,shifts:0}],
      office_status:{confirmed_days:0,complete:false},
      active:null,break:{active:false,on_break:false,tracked:false,minutes:0},
      shifts:entries,plan:[
        {day:month+"-18",status:"shift",start:"16:00",end:"02:00",source:"manual"},
        {day:month+"-19",status:"off",start:null,end:null,source:"manual"},
        {day:month+"-21",status:"shift",start:"16:00",end:"02:00",source:"import"}
      ],office:{},issues:[],weeks:[
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
  function notify(message){
    const el=byId("toast");el.textContent=message;el.hidden=false;
    if(toastTask)clearTimeout(toastTask);
    toastTask=setTimeout(()=>el.hidden=true,4500);
    tg?.HapticFeedback?.notificationOccurred?.("success");
  }
  function setStatus(ok,label){
    const el=byId("connection");
    el.className="connection "+(ok?"ok":"error");
    el.lastChild.textContent=label;
  }
  function setTheme(name){
    document.body.dataset.theme=name==="light"?"light":"dark";
    byId("theme-switch").textContent=name==="light"?"☾":"☼";
    try{localStorage.setItem("workshift_theme",name);}catch(e){}
    try{tg?.setHeaderColor?.(name==="light"?"#f2f5f5":"#0c1828");tg?.setBackgroundColor?.(name==="light"?"#f2f5f5":"#0b1625");}catch(e){}
  }
  function navigate(page){
    document.querySelectorAll(".page").forEach(e=>e.classList.toggle("shown",e.id==="page-"+page));
    document.querySelectorAll("[data-nav].nav-item").forEach(e=>{
      const active=e.dataset.nav===page;
      e.classList.toggle("active",active);
      if(active)e.setAttribute("aria-current","page");
      else e.removeAttribute("aria-current");
    });
    window.scrollTo({top:0,behavior:"instant"});
    tg?.HapticFeedback?.selectionChanged?.();
  }
  async function post(path,payload,signal){
    const controller=new AbortController();
    const relay=()=>controller.abort();
    if(signal?.aborted)relay();
    signal?.addEventListener("abort",relay,{once:true});
    let expired=false;
    const timeout=setTimeout(()=>{expired=true;controller.abort();},12000);
    try{
      const response=await fetch(path,{
        method:"POST",cache:"no-store",credentials:"omit",signal:controller.signal,
        headers:{"Content-Type":"application/json","X-Telegram-Init-Data":initData},
        body:JSON.stringify(payload)
      });
      const result=await response.json();
      if(!response.ok){
        let message=result.error||"Не удалось выполнить запрос.";
        if(response.status===403)message="Нет доступа или сессия Telegram истекла. Закрой и открой кабинет заново.";
        if(response.status===429)message="Слишком много запросов. Повтори через минуту.";
        const error=Error(message);error.status=response.status;throw error;
      }
      return result;
    }catch(error){
      if(expired)throw Error("Сервер не ответил вовремя. Проверь соединение.");
      throw error;
    }finally{
      clearTimeout(timeout);signal?.removeEventListener("abort",relay);
    }
  }
  async function reload(){
    if(!connected){
      state=demoState(currentMonth);
      byId("demo-banner").hidden=false;
      setStatus(false,"Демонстрация");
      paint();return;
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
      state=result;serverOffset=Date.parse(result.now)-Date.now();
      if(!Number.isFinite(serverOffset))serverOffset=0;
      fresh=true;
      query(".shell").classList.remove("auth-blocked");
      byId("connection-banner").hidden=true;
      byId("demo-banner").hidden=true;
      setStatus(true,"Подключено");paint();
    }catch(error){
      if(sequence!==requestSequence)return;
      const denied=error.status===403;
      if(denied){state=null;query(".shell").classList.add("auth-blocked");}
      setStatus(false,denied?"Нет доступа":"Нет соединения");
      text("connection-error",error.message+(state?" Показаны последние загруженные данные.":""));
      byId("connection-banner").hidden=false;
      byId("offline-banner").hidden=navigator.onLine;
    }finally{
      if(sequence===requestSequence){loading=false;if(state)paintHome();}
    }
  }
  function elapsed(){
    if(!state?.active)return "00:00:00";
    const start=Date.parse(state.active.start);
    const seconds=Math.max(0,Math.floor((serverNow()-start)/1000));
    return [Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60]
      .map(pad).join(":");
  }
  function tick(){
    text("live-time",elapsed());
    const now=new Date(serverNow());
    if(state?.break?.on_break){
      const running=Math.max(0,Math.ceil((serverNow()-Date.parse(state.break.break_started_utc))/60000));
      text("shift-break",((state.break.minutes||0)+running)+" мин · идёт");
    }
    text("shift-now",new Intl.DateTimeFormat("ru-RU",{timeZone:"Europe/Moscow",hour:"2-digit",minute:"2-digit"}).format(now));
  }
  function formatHours(value){
    const minutes=Math.max(0,Math.round((value||0)*60));
    return Math.floor(minutes/60)+" ч "+pad(minutes%60)+" мин";
  }
  function paintHome(){
    const active=state?.active,br=state?.break||{};
    const label=byId("shift-label");
    label.className="state-pill "+(!active?"off":br.on_break?"break":"");
    label.innerHTML="";append(label,"span","dot");
    append(label,"span","",!active?"Смена не начата":br.on_break?"Идёт перерыв":"На работе");
    text("shift-subtitle",!active?"Начни смену одним нажатием":
         br.on_break?"Сначала закончи перерыв, затем отмечай уход":
         "Время фиксируется сервером · Москва");
    text("shift-start",active?new Intl.DateTimeFormat("ru-RU",{timeZone:"Europe/Moscow",hour:"2-digit",minute:"2-digit"}).format(new Date(active.start)):"—");
    text("shift-break",br.tracked?(br.minutes+" мин · по факту"):(br.minutes??30)+" мин · оценка");
    const btn=byId("shift-action"), pause=byId("break-action");
    const readOnly=connected&&!state.capabilities?.writes;
    byId("view-only-banner").hidden=!readOnly;
    btn.textContent=readOnly?"Отметиться в чате":active?"● Ушёл":"● Пришёл";
    btn.classList.toggle("leave",Boolean(active));
    btn.disabled=commandBusy||(!readOnly&&Boolean(br.on_break));
    pause.disabled=commandBusy;
    pause.hidden=!active;
    pause.textContent=br.on_break?"▶ Закончить перерыв":"☕ Начать перерыв";
    const sum=state.summary;
    text("home-hours",formatHours(sum.hours));
    text("home-shifts",sum.shifts+" закрытых смен");
    text("home-money",money(sum.earned));
    text("month-title-home",MONTHS[Number(currentMonth.slice(5))-1]);
    text("today-date",fmt.format(new Date(serverNow())));
    text("alerts-summary",state.issues.length?state.issues.length+" записей требуют проверки":"Личные записи проверены · офис отдельно");
    const list=byId("recent-shifts");list.replaceChildren();
    const items=[...state.shifts].sort((a,b)=>b.day.localeCompare(a.day)).slice(0,4);
    if(!items.length)append(list,"div","empty-card","В этом месяце пока нет закрытых смен.");
    items.forEach(s=>{
      const line=append(list,"article","shift-row");
      const left=append(line,"div");
      append(left,"strong","",s.day.slice(8)+"."+s.day.slice(5,7));
      append(left,"small","",(s.start||"—")+"–"+(s.end||"в работе")+
             (s.next_day?" · +1 день":""));
      append(line,"div","money",s.end?money(s.earnings):"В работе");
    });
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
  function paintCalendar(){
    const [y,m]=currentMonth.split("-").map(Number);
    text("calendar-month",MONTHS[m-1].slice(0,3)+". "+y);
    byId("month-jump").value=currentMonth;
    const grid=byId("calendar-grid");
    grid.replaceChildren();
    const offset=(new Date(Date.UTC(y,m-1,1)).getUTCDay()+6)%7;
    const days=new Date(Date.UTC(y,m,0)).getUTCDate();
    const active=new Map(state.shifts.map(s=>[s.day,s]));
    const plans=new Map(state.plan.map(s=>[s.day,s]));
    for(let i=0;i<offset;i++)append(grid,"span","empty","").setAttribute("aria-hidden","true");
    for(let day=1;day<=days;day++){
      const k=dkey(day);
      const data=active.get(k),plan=plans.get(k);
      const el=append(grid,"button",[
        data?"worked":plan?.status==="shift"?"planned":plan?.status==="off"?"off":"",
        k===moscowDay()?"today":"",
        k===selectedDay?"selected":""
      ].join(" "),day);
      el.setAttribute("aria-pressed",String(k===selectedDay));
      el.setAttribute("aria-label",day+" "+MONTHS[m-1]+(data?(data.end?" отработанная смена":" открытая смена"):plan?" план":""));
      el.onclick=()=>{selectedDay=k;paintCalendar();paintDay();};
    }
    paintDay();
  }
  function line(parent,name,value){
    const el=append(parent,"div","detail-line");
    append(el,"span","",name);
    append(el,"strong","",value);
  }
  function paintDay(){
    const details=byId("day-details");
    details.replaceChildren();
    text("day-heading","День · "+selectedDay.slice(8)+"."+selectedDay.slice(5,7));
    const records=state.shifts.filter(s=>s.day===selectedDay);
    const plan=state.plan.find(p=>p.day===selectedDay);
    if(!records.length)append(details,"p","muted","Фактических отметок нет.");
    for(const s of records){
      append(details,"h3","",s.end?"✅ Закрытая смена":"🟢 Смена открыта");
      line(details,"Приход",s.start||"—");
      line(details,"Уход",(s.end||"—")+(s.next_day?" · следующий день":""));
      line(details,"Перерыв",s.break_minutes+" мин");
      if(s.end){
        line(details,"Отработано",formatHours(s.hours));
        line(details,"Начислено",money(s.earnings));
      }
    }
    if(plan){
      const desc=plan.status==="off"?"Выходной":(plan.start||"—")+"–"+(plan.end||"—");
      line(details,"План",desc);
    }
    if(Object.prototype.hasOwnProperty.call(state.office,selectedDay))
      line(details,"Офис",formatHours(state.office[selectedDay]));
    if(!Object.prototype.hasOwnProperty.call(state.office,selectedDay))
      append(details,"div","detail-source","Точные офисные часы для этой даты пока не подтверждены.");
    const link=append(details,"button","text-button","Изменить в чате бота ›");
    link.classList.add("day-edit-link");
    link.onclick=()=>{notify("Вернись в чат бота и открой календарь: там можно изменить план и смену.");};
  }
  function paintSalary(){
    const s=state.summary,a=s.additional||{};
    text("salary-month",MONTHS[Number(currentMonth.slice(5))-1]+" "+currentMonth.slice(0,4));
    text("salary-total",money(s.total));text("salary-hours",formatHours(s.hours)+" · "+s.shifts+" смен");
    text("salary-base",money(s.earned));text("salary-extra",money(a.net_adjustment));
    text("salary-received",money(s.received));
    text("salary-bonuses",money((a.bonus||0)+(a.allowance||0)));
    text("salary-deductions",money(a.deduction));
    text("salary-reimbursements",money(a.reimbursement));
    const periods=byId("salary-periods");periods.replaceChildren();
    (state.periods||[]).forEach(period=>{
      const card=append(periods,"article","metric");
      append(card,"span","",period.period===1?"1–15 число":"16–конец месяца");
      append(card,"strong","",money(period.earnings));
      append(card,"small","",formatHours(period.hours)+" · за часы");
    });
    const el=byId("weekly-chart");el.replaceChildren();
    const max=Math.max(1,...state.weeks.map(x=>x.hours||0));
    state.weeks.forEach(row=>{
      const group=append(el,"div","bar-group");
      append(group,"strong","",Math.round(row.hours)+" ч");
      const bar=append(group,"div","bar-col");
      bar.style.height=Math.round(14+(row.hours||0)/max*120)+"px";
      append(group,"span","",row.label);
    });
  }
  function paintReview(){
    const box=byId("issue-list");box.replaceChildren();
    if(!state.issues.length)append(box,"div","issue good","✓ В личных отметках явных ошибок не обнаружено.");
    const officeCount=state.office_status?.confirmed_days||0;
    append(box,"div","issue",officeCount?"Офис: подтверждено дат в месяце — "+officeCount+". Полнота табеля не подтверждена.":"Офисный табель не подтверждён. Сверка часов пока недоступна.");
    state.issues.forEach(row=>{
      const item=append(box,"div","issue");
      append(item,"span","when",row.day.slice(8)+"."+row.day.slice(5,7));
      append(item,"span","",row.text);
    });
    renderDrafts();
  }
  function renderDrafts(){
    let draft;
    try{draft=JSON.parse(localStorage.getItem("workshift_draft")||"null");}catch(e){draft=null;}
    const panel=byId("pending-drafts");
    panel.hidden=!draft;
    if(!draft)return;
    panel.replaceChildren();
    append(panel,"strong","","Есть неподтверждённая отметка без сети");
    append(panel,"p","","Действие: "+draft.action+" · время устройства "+draft.deviceTime+
      ". Не включено в расчёт зарплаты. Уточни время через чат с ботом.");
    const remove=append(panel,"button","small-switch","Удалить черновик");
    remove.onclick=()=>{localStorage.removeItem("workshift_draft");renderDrafts();};
  }
  function paint(){
    if(!state)return;
    tick();paintHome();paintCalendar();paintSalary();paintReview();
    byId("offline-banner").hidden=navigator.onLine;
  }
  function modal(title,message){
    return new Promise(resolve=>{
      byId("modal-title").textContent=title;
      byId("modal-text").textContent=message;
      const overlay=byId("modal"),previous=document.activeElement;
      overlay.hidden=false;query("main").inert=true;document.body.classList.add("modal-open");
      const finish=value=>{
        overlay.hidden=true;query("main").inert=false;document.body.classList.remove("modal-open");
        document.removeEventListener("keydown",keys);
        byId("modal-confirm").onclick=null;byId("modal-cancel").onclick=null;
        previous?.focus?.();resolve(value);
      };
      const keys=event=>{
        if(event.key==="Escape"){event.preventDefault();finish(false);}
        if(event.key==="Tab"){
          event.preventDefault();
          (document.activeElement===byId("modal-cancel")?byId("modal-confirm"):byId("modal-cancel")).focus();
        }
      };
      document.addEventListener("keydown",keys);
      byId("modal-confirm").onclick=()=>finish(true);
      byId("modal-cancel").onclick=()=>finish(false);
      byId("modal-cancel").focus();
    });
  }
  function draft(action){
    const deviceTime=new Date().toISOString();
    try{localStorage.setItem("workshift_draft",JSON.stringify({action,deviceTime}));}catch(e){}
    renderDrafts();
    notify("Связи нет. Сохранил только черновик — подтвердить его нужно в чате.");
  }
  async function command(action){
    if(!connected){notify("Это демонстрация. Рабочие часы не изменяются.");return;}
    if(commandBusy)return;
    if(!state){notify("Сначала дождись авторизации и загрузки данных.");return;}
    if(!state.capabilities?.writes){
      if(tg?.close){tg.close();return;}
      notify("Для отметки используй кнопку в чате бота.");return;
    }
    if(!navigator.onLine){draft(action);return;}
    if(!fresh||loading){notify("Обновляем состояние. Повтори после подключения.");await reload();return;}
    commandBusy=true;
    const labels={in:"Начать смену",out:"Завершить смену",pause_start:"Начать перерыв",pause_end:"Закончить перерыв"};
    const candidate=pendingRequest||{
      action,
      nonce:Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,"0")).join(""),
      shift_id:action==="in"?null:state.active?.id,
      break_id:action==="pause_end"?state.break?.break_id:null
    };
    paintHome();
    try{
      const ok=await modal(pendingRequest?"Проверить предыдущую отметку":labels[action],
        pendingRequest?"Повторим тот же запрос без дублирования записи. Новое действие не создаётся.":"Подтвердить действие? Время запишет сервер по Москве.");
      if(!ok)return;
      pendingRequest=candidate;
      await post("/mini/api/action",candidate);
      pendingRequest=null;
      await reload();notify("✅ Сохранено на сервере");
    }catch(error){
      if(error.status&&error.status<500)pendingRequest=null;
      notify(error.message+(pendingRequest?" Результат не подтверждён. Следующее нажатие проверит тот же запрос.":""));
    }finally{commandBusy=false;if(state)paintHome();}
  }
  document.querySelectorAll("[data-nav]").forEach(btn=>{
    btn.addEventListener("click",()=>navigate(btn.dataset.nav));
    btn.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();navigate(btn.dataset.nav);}});
  });
  byId("retry-connection").onclick=()=>reload();
  byId("month-jump").onchange=event=>{
    const input=event.currentTarget,value=input.value;
    if(!/^\d{4}-\d{2}$/.test(value)||!input.checkValidity()){notify("Выбери месяц с 2000 по 2100 год.");return;}
    currentMonth=value;selectedDay=value+"-01";reload();
  };
  byId("month-prev").onclick=()=>changeMonth(-1);
  byId("month-next").onclick=()=>changeMonth(1);
  byId("shift-action").onclick=()=>command(state?.active?"out":"in");
  byId("break-action").onclick=()=>command(state?.break?.on_break?"pause_end":"pause_start");
  const themeToggle=()=>setTheme(document.body.dataset.theme==="light"?"dark":"light");
  byId("theme-switch").onclick=themeToggle;byId("theme-row-switch").onclick=themeToggle;
  try{setTheme(localStorage.getItem("workshift_theme")|| (tg?.colorScheme==="light"?"light":"dark"));}catch(e){setTheme("dark");}
  window.addEventListener("offline",()=>{byId("offline-banner").hidden=false;setStatus(false,"Нет сети");});
  window.addEventListener("online",()=>{byId("offline-banner").hidden=true;reload();});
  if(tg){
    tg.ready();tg.expand();

  }
  const previewTab=new URLSearchParams(window.location.search).get("tab");
  if(["calendar","salary","review"].includes(previewTab))navigate(previewTab);
  document.addEventListener("visibilitychange",()=>{if(!document.hidden&&connected&&!commandBusy)reload();});
  setInterval(()=>{if(connected&&!document.hidden&&!loading&&!commandBusy)reload();},60000);
  setInterval(tick,1000);
  reload();
})();
