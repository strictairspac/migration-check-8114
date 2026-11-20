import redis
from datetime import datetime,timezone
from fastapi import HTTPException
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models.batch import Batch
from src.models.mapping import Mapping
from src.models.run import Run,TargetRow
from src.utils.serialization import record
from src.services.migration import digest,key_digest,validate_backup
from src.services.billing import submit_seq,register_restored
from src.models.billing import BillingLine
queue=redis.Redis.from_url(REDIS_URL)
def list_items():
 with Session() as s:return [record(r) for r in s.query(Run).order_by(Run.id.desc()).limit(100).all()]
def detail(item_id):
 with Session() as s:
  r=s.get(Run,item_id)
  if not r:raise HTTPException(404,'未找到对账运行')
  return record(r)
def create(data):
 with Session() as s:
  m=s.get(Mapping,data.mapping_id)
  if not m:raise HTTPException(404,'未找到字段映射')
  b=s.get(Batch,m.batch_id)
  r=Run(name=data.name,batch_id=b.id,mapping_id=m.id,status='queued',snapshot={'batch':record(b),'mapping':record(m)})
  s.add(r);s.flush()
  # Take the shared close/submit clock inside the same transaction. The control
  # row lock is held until commit, so a concurrent close cannot interleave.
  r.submit_seq=submit_seq(s)
  s.commit();s.refresh(r);result=record(r)
 try:queue.rpush('migration-runs',result['id'])
 except redis.RedisError:
  with Session() as s:r=s.get(Run,result['id']);r.status='failed';r.error='队列不可用，请创建新任务';s.commit()
  raise HTTPException(503,'队列不可用，请稍后重试')
 return result
def cancel(item_id):
 with Session() as s:
  r=s.query(Run).filter_by(id=item_id).with_for_update().first()
  if not r:raise HTTPException(404,'未找到对账运行')
  if r.status not in ('queued','running'):raise HTTPException(409,'运行已终止，无法取消')
  r.cancel_requested=True
  if r.status=='queued':r.status='cancelled'
  s.commit();return record(r)
def targets(item_id):
 detail(item_id)
 with Session() as s:rows=[r.payload for r in s.query(TargetRow).filter_by(run_id=item_id).order_by(TargetRow.ordinal)]
 return {'rows':rows,'count':len(rows),'checksum':digest(rows)}
def backup(item_id):
 r=detail(item_id)
 if r['status']!='completed':raise HTTPException(409,'仅完成运行可以备份')
 return r['result']['backup']
def restore(item_id,data):
 with Session() as s:
  r=s.query(Run).filter_by(id=item_id).with_for_update().first()
  if not r:raise HTTPException(404,'未找到对账运行')
  if r.status!='completed':raise HTTPException(409,'仅完成运行可以恢复')
  try:rows=validate_backup(data.backup,r.result['checksum'],item_id)
  except ValueError as e:raise HTTPException(422,str(e))
  before=s.query(TargetRow).filter_by(run_id=item_id).count()
  s.query(TargetRow).filter_by(run_id=item_id).delete()
  for i,row in enumerate(rows):s.add(TargetRow(run_id=item_id,ordinal=i,key_digest=key_digest(row),payload=row))
  s.flush()
  actual=[x.payload for x in s.query(TargetRow).filter_by(run_id=item_id).order_by(TargetRow.ordinal)]
  proof={'restored_at':datetime.now(timezone.utc).isoformat(),'previous_count':before,'restored_count':len(actual),'checksum':digest(actual),'verified':digest(actual)==r.result['checksum']}
  if not proof['verified']:raise HTTPException(409,'恢复后校验失败，已回滚')
  # Restoring rewrites existing target rows for this run; the billing ledger is
  # untouched. A completed run was already billed at first ingestion, so a
  # restore (including a retried restore after a lost receipt) never adds a line.
  # Only a missing first-ingestion record is filled, and only when this run has
  # no ledger line yet (a database from before the billing feature existed).
  billed=s.query(BillingLine).filter_by(run_id=item_id).count()
  registered=0
  if billed==0:registered=register_restored(s,r,(r.snapshot or {}).get('batch',{}).get('name',''),rows)
  proof['billing']={'billed_lines':billed,'registered_ingestions':registered,'charged_this_restore':0,'note':'恢复备份不重复计费'}
  r.recovery=(r.recovery or [])+[proof];s.commit();return proof
