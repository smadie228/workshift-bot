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
  async function post(path,payload){
    const response=await fetch(path,{
      method:"POST",cache:"no-store",credentials:"omit",
      headers:{"Content-Type":"application/json","X-Telegram-Init-Data":initData},
      body:JSON.stringify(payload)
    });
    const result=await response.json();
    if(!response.ok){
      if(response.status===403)throw Error("Нет доступа или сессия Telegram истекла. Закрой и открой кабинет заново.");
      throw Error(result.error||"Не удалось выполнить запрос.");
    }
    return result;
  }
  async function reload(){
    if(!connected){
      state=demoState(currentMonth);
      byId("demo-banner").hidden=false;
      setStatus(false,"Демонстрация");
      paint();
      return;
    }
    if(loading)return;
    loading=true;
    try{
      state=await post("/mini/api/state",{month:currentMonth});
      byId("demo-banner").hidden=true;
      setStatus(true,"Подключено");
      paint();
    }catch(err){
      setStatus(false,"Нет соединения");
      notify(err.message);
      byId("offline-banner").hidden=false;
    }finally{loading=false;}
  }
  function elapsed(){
    if(!state?.active)return "00:00:00";
    const start=Date.parse(state.active.start);
    const seconds=Math.max(0,Math.floor((Date.now()-start)/1000));
    return [Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60]
      .map(pad).join(":");
  }
  function tick(){
    text("live-time",elapsed());
    const now=new Date();
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
    text("shift-break",br.tracked?(br.minutes+" мин · по факту"):(br.minutes||30)+" мин · оценка");
    const btn=byId("shift-action"), pause=byId("break-action");
    btn.textContent=active?"● Ушёл":"● Пришёл";
    btn.classList.toggle("leave",Boolean(active));
    btn.disabled=Boolean(br.on_break);
    pause.hidden=!active;
    pause.textContent=br.on_break?"▶ Закончить перерыв":"☕ Начать перерыв";
    const sum=state.summary;
    text("home-hours",formatHours(sum.hours));
    text("home-shifts",sum.shifts+" закрытых смен");
    text("home-money",money(sum.earned));
    text("month-title-home",MONTHS[Number(currentMonth.slice(5))-1]);
    text("today-date",fmt.format(new Date()));
    text("alerts-summary",state.issues.length?state.issues.length+" записей требуют проверки":"Серьёзных расхождений не обнаружено");
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
    currentMonth=date.getUTCFullYear()+"-"+pad(date.getUTCMonth()+1);
    selectedDay=currentMonth+"-01";
    reload();
  }
  function paintCalendar(){
    const [y,m]=currentMonth.split("-").map(Number);
    text("calendar-month",MONTHS[m-1].slice(0,3)+". "+y);
    const grid=byId("calendar-grid");
    grid.replaceChildren();
    const offset=(new Date(Date.UTC(y,m-1,1)).getUTCDay()+6)%7;
    const days=new Date(Date.UTC(y,m,0)).getUTCDate();
    const active=new Map(state.shifts.map(s=>[s.day,s]));
    const plans=new Map(state.plan.map(s=>[s.day,s]));
    for(let i=0;i<offset;i++)append(grid,"button","empty","");
    for(let day=1;day<=days;day++){
      const k=dkey(day);
      const data=active.get(k),plan=plans.get(k);
      const el=append(grid,"button",[
        data?"worked":plan?.status==="shift"?"planned":plan?.status==="off"?"off":"",
        k===currentMoscow?"today":"",
        k===selectedDay?"selected":""
      ].join(" "),day);
      el.setAttribute("role","gridcell");
      el.setAttribute("aria-label",day+" "+MONTHS[m-1]+(data?" отработанная смена":plan?" план":""));
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
    link.style.marginTop="16px";
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
    if(!state.issues.length)append(box,"div","issue good","✓ Ничего критического не обнаружено.");
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
      const overlay=byId("modal");
      overlay.hidden=false;
      const finish=value=>{
        overlay.hidden=true;byId("modal-confirm").onclick=null;
        byId("modal-cancel").onclick=null;resolve(value);
      };
      byId("modal-confirm").onclick=()=>finish(true);
      byId("modal-cancel").onclick=()=>finish(false);
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
    if(!navigator.onLine){draft(action);return;}
    if(!state.capabilities?.writes){
      notify("Кабинет пока в режиме просмотра. Для отметки используй кнопку в чате бота.");
      return;
    }
    const labels={in:"Начать смену",out:"Завершить смену",
                  pause_start:"Начать перерыв",pause_end:"Закончить перерыв"};
    const ok=await modal(labels[action],"Подтвердить действие? Время запишет сервер по Москве.");
    if(!ok)return;
    const nonce=Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,"0")).join("");
    try{
      await post("/mini/api/action",{action,nonce});
      tg?.HapticFeedback?.notificationOccurred?.("success");
      await reload();
      notify("✅ Сохранено на сервере");
    }catch(err){notify(err.message);}
  }
  document.querySelectorAll("[data-nav]").forEach(btn=>{
    btn.addEventListener("click",()=>navigate(btn.dataset.nav));
    btn.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();navigate(btn.dataset.nav);}});
  });
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
    try{tg.setHeaderColor("#0c1828");tg.setBackgroundColor("#0b1625");}catch(e){}
  }
  const previewTab=new URLSearchParams(window.location.search).get("tab");
  if(["calendar","salary","review"].includes(previewTab))navigate(previewTab);
  setInterval(tick,1000);
  reload();
})();
