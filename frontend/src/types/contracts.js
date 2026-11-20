/** @typedef {{id:number,name:string,source_file:string,rows:object[]}} Batch */
/** @typedef {{id:number,name:string,batch_id:number,rules:object[],chunk_size:number}} Mapping */
/** @typedef {{id:number,name:string,submit_seq:number,status:'queued'|'running'|'completed'|'failed'|'cancelled',result:object|null}} Run */
/** @typedef {{month:string,status:'closed'|'open',close_seq:number,closed_count:number,closed_checksum:string,current_count?:number,drifted?:boolean}} BillingPeriod */
/** @typedef {{sample_code:string,test_name:string,measured_at:string,detection_month:string,billing_month:string,adjusted:boolean,late:boolean,after_close:boolean,reason:string,source:string,run_name:string,batch_name:string}} BillingLine */
export {};
