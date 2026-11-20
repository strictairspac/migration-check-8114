import {api,post} from './client.js';
export const batches={list:()=>api('/batches'),save:(d,id)=>post('/batches'+(id?'/'+id:''),d,id?'PUT':'POST')};
export const mappings={list:()=>api('/mappings'),save:(d,id)=>post('/mappings'+(id?'/'+id:''),d,id?'PUT':'POST')};
export const runs={list:()=>api('/runs'),run:d=>post('/runs',d),cancel:id=>post('/runs/'+id+'/cancel',{}),targets:id=>api('/runs/'+id+'/targets'),restore:(id,backup)=>post('/runs/'+id+'/restore',{backup})};
export const billing={
 list:()=>api('/billing'),
 month:m=>api('/billing/months/'+encodeURIComponent(m)),
 explain:m=>api('/billing/months/'+encodeURIComponent(m)+'/explain'),
 lines:(m,runId)=>api('/billing/lines?'+new URLSearchParams({...(m?{month:m}:{}),...(runId?{run_id:runId}:{})}).toString()),
 skips:runId=>api('/billing/skips'+(runId?'?run_id='+runId:'')),
 close:(m,note)=>post('/billing/months/'+encodeURIComponent(m)+'/close',{note}),
 reopen:m=>post('/billing/months/'+encodeURIComponent(m)+'/reopen',{}),
 toggle:enabled=>post('/billing/toggle',{enabled}),
 backfill:()=>post('/billing/backfill',{}),
};
