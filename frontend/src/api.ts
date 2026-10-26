export interface Person {name:string;location:string;availableFrom:string;availableTo:string}
export interface Slots {city?:string;residentArea?:string;maxCommuteMinutes?:number;avoidPlaces?:string[];date?:string;budget?:number;maxDistanceKm?:number;preferences?:string[];people?:Person[]}
export interface Source {title:string;url:string;snippet:string;kind?:string;publisher?:string;checkedAt?:string;publishedAt?:string|null;eventStart?:string|null;eventEnd?:string|null;verification:string}
export interface Stop {order:number;name:string;category:string;address:string;startTime:string;endTime:string;durationMinutes:number;costEstimate:number;description:string;activitySources:Source[];source:{verification:string;url:string|null}}
export interface Plan {id:string;title:string;subtitle:string;tags:string[];theme:string;startTime:string;endTime:string;totalMinutes:number;totalDistanceKm:number;meetingPoint:string;budgetPerPerson:{total:number;transport:number};arrivals:{name:string;from:string;minutes:number;returnMinutes:number}[];stops:Stop[];transfers:{minutes:number;distanceKm:number}[];invitation:string}
export interface Result {version:string;request:Slots;meta:{mode:string;generatedAt:string;dataNote:string;warnings:string[];commonWindow:{from:string;to:string};information?:{channels:{id:string;label:string;status:string;count:number}[];rejectedRecords:number}};plans:Plan[]}
export interface Reply {schema_version:string;intent:string;message:string;next_step:{action:string;options:{key:string;text:string}[];result:Result|null;invitation:string|null;selected_plan_id:string|null;slots:Slots;notices:string[]}}
export interface Progress {type:'progress';role:string;state:string;title:string;detail:string}
export type Payload = {input?:string;action?:'demo'|'invite';plan_id?:string;request?:Slots;home?:{city:string;residentArea?:string}};
export const API_BASE=(import.meta.env.VITE_AGENT_API_URL||'').replace(/\/$/,'');
export const STATIC_PREVIEW=import.meta.env.VITE_STATIC_PREVIEW==='true'&&!API_BASE;
export const apiUrl=(path:string)=>API_BASE+path;
export async function loadConfig(){
  if(STATIC_PREVIEW)return {model:false,places:false,activities:false,feed:false};
  const response=await fetch(apiUrl('/api/config'));if(!response.ok)throw new Error('暂时无法读取服务状态');return response.json();
}
let sampleReply:Reply|null=null;
async function previewTurn(payload:Payload,onProgress:(p:Progress)=>void):Promise<{conversation:string;data:Reply}>{
  if(payload.action==='demo'){
    onProgress({type:'progress',role:'preview',state:'running',title:'正在载入产品样例',detail:'静态展示，不进行真实活动搜索'});
    const response=await fetch(import.meta.env.BASE_URL+'sample-response.json');if(!response.ok)throw new Error('样例暂时不可用');
    sampleReply=await response.json();
    if(!sampleReply?.next_step.result)throw new Error('样例格式无效');
    sampleReply.next_step.result.meta.dataNote='主动选择的杭州产品样例，含历史示意日期，仅用于展示，不是当前活动或所在地推荐。';
    onProgress({type:'progress',role:'preview',state:'done',title:'样例已载入',detail:'路线、费用、日期均为示意，未调用实时 API'});
    return {conversation:'github-static-preview',data:structuredClone(sampleReply)};
  }
  if(payload.action==='invite'&&sampleReply){
    const plan=sampleReply.next_step.result?.plans.find(p=>p.id===payload.plan_id);
    if(plan)return {conversation:'github-static-preview',data:{...structuredClone(sampleReply),intent:'invite',message:'产品样例邀请函已生成。地点、日期与费用仅用于展示。',next_step:{...structuredClone(sampleReply.next_step),action:'share_invitation',selected_plan_id:plan.id,invitation:plan.invitation}}};
  }
  // Pages cannot run the Python coordinator or hold provider credentials.
  const slots=payload.request||payload.home||{};
  sampleReply=null;
  return {conversation:'github-static-preview',data:{schema_version:'1.0',intent:'clarify',message:'目前是 GitHub Pages 产品展示。真实的当地活动检索接口尚未接入；你可以在设置页查看明确标注的产品样例。接通云端服务后，这里会按你的常住地生成路线。',next_step:{action:'provide_details',options:[],result:null,invitation:null,selected_plan_id:null,slots,notices:['当前页面不进行真实活动搜索，产品样例不代表你的所在地推荐。']}} satisfies Reply};
}
export async function turn(conversation:string|null,payload:Payload,onProgress:(p:Progress)=>void) {
  if(STATIC_PREVIEW)return previewTurn(payload,onProgress);
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),75000);
  try {
    const response=await fetch(apiUrl('/turn'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({conversation,...payload}),signal:controller.signal});
    if(!response.ok||!response.body){const data=await response.json().catch(()=>({}));throw new Error(data.error||'连接暂时中断，请稍后重试。');}
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',result:{conversation:string;data:Reply}|null=null;
    const frame=(value:string)=>{for(const line of value.split('\n')){if(!line.startsWith('data:'))continue;const event=JSON.parse(line.slice(5));if(event.type==='progress')onProgress(event);if(event.type==='result')result=event;}};
    for(;;){const{done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});const frames=buffer.split('\n\n');buffer=frames.pop()||'';frames.forEach(frame);}
    buffer+=decoder.decode();if(buffer.trim())frame(buffer);
    if(!result)throw new Error('这次回复没有完整送达，已保留之前的安排。');return result as {conversation:string;data:Reply};
  } finally {clearTimeout(timeout);}
}
