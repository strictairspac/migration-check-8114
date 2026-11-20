"""Month-close (封账) and per-piece billing for migrated inspection items.

Guarantees implemented here
---------------------------
Attribution
  The billing month is always the Beijing-time (UTC+08:00) month of the item's
  ``measured_at``.  A naive timestamp is read as Beijing time, the same
  convention the converter uses.

Race ordering
  Submitting a run and closing a month both take the ``billing_control`` row
  lock and bump one shared counter.  A run records ``submit_seq``; a close
  records ``close_seq``.  ``run.submit_seq < period.close_seq`` means the run
  was submitted first, so its items keep the month they detected even if the
  worker persists them after the close (``after_close`` flags the drift).  If
  the run was submitted after the close, an item whose detected month was
  already closed at submission time moves to the first month still open at
  submission time.  Items whose detected month was open are untouched.

Dedup
  The first successful persistence of ``sample_code + test_name`` wins and is
  the only possible charge.  Every ingestion is registered — even while billing
  is off — so switching billing on later never retroactively charges an older
  ingestion.  Replays and restores never add a line.

Atomicity
  ``Ledger`` only writes inside the caller's transaction.  The worker commits
  target rows, ledger lines and skips together; ``runs.restore`` replaces
  target rows and registers ingestions in one transaction.  Any failure rolls
  all of it back, and a replayed delivery is idempotent via unique keys and
  savepoints.
"""
import hashlib,json
from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from src.config.database import Session
from src.models.billing import BillingControl,BillingIngestion,BillingLine,BillingPeriod,BillingSkip
from src.models.run import Run,TargetRow
from src.services.billing_rules import beijing_month,next_month,resolve_billing_month,valid_month
from src.utils.serialization import record

CONTROL_ID=1
SKIP_REASONS={
 'duplicate_key':'同一委托同一检测项已有首次成功入库，本次不重复计件',
 'restore':'恢复备份仅登记入库，不重复计费',
 'historical':'启用计费前的历史入库，仅登记不补计',
 'billing_disabled':'计费功能未启用，本次不入账',
}


def utcnow():return datetime.now(timezone.utc).replace(tzinfo=None)
def present(dt):return None if dt is None else dt.replace(tzinfo=timezone.utc).isoformat()


def _get_control(s):
 """Fetch the control row, creating it once without poisoning the outer tx."""
 row=s.get(BillingControl,CONTROL_ID)
 if row is not None:return row
 nested=s.begin_nested()
 try:
  row=BillingControl(id=CONTROL_ID,enabled=True,counter=0,backfilled=False)
  s.add(row);s.flush()
  nested.commit()
  return row
 except IntegrityError:
  # Another transaction created it first; only this savepoint is rolled back.
  nested.rollback()
  return s.query(BillingControl).filter_by(id=CONTROL_ID).one()


def _control_locked(s):
 _get_control(s)
 return s.query(BillingControl).filter_by(id=CONTROL_ID).with_for_update().one()


def lock_control(s):
 """Serialise a ledger commit against close.

 The worker holds this lock while it writes target rows and lines, so a close
 either commits before the ingestion (and its ``close_seq`` is visible when the
 ledger decides attribution) or after it (and its snapshot includes the new
 lines).  Either order stays consistent with submission order.
 """
 return _control_locked(s)


def submit_seq(s):
 """Bump and return the shared submission clock.

 The caller's transaction holds the control row lock until commit, which is
 what serialises "submit a migration" against "close a month".
 """
 row=_control_locked(s)
 row.counter+=1
 s.flush()
 return row.counter


class Ledger:
 """Billing writer bound to one run and one open transaction.

 ``charge=False`` registers the first ingestion but never creates a line; the
 restore and historical-backfill paths use it so they cannot bill.  The ledger
 never commits: target rows, lines and skips are committed by the caller.
 """
 def __init__(self,s,run,batch_name,source='migration',charge=True):
  self.s=s
  self.run=run
  self.batch_name=batch_name
  self.source=source
  self.charge=charge
  self.lines=0
  self.registered=0
  self.created=[]
  self.skipped=[]
  self.enabled=bool(_get_control(s).enabled)
  self._closed_before=None
  self._closed_after=None

 def _closed_months(self):
  if self._closed_before is None:
   seq=int(getattr(self.run,'submit_seq',0) or 0)
   periods=self.s.query(BillingPeriod).filter_by(status='closed').all()
   self._closed_before={p.month for p in periods if p.close_seq<=seq}
   self._closed_after={p.month for p in periods if p.close_seq>seq}
  return self._closed_before

 def attribute(self,measured_at):
  detection=beijing_month(measured_at)
  return detection,resolve_billing_month(detection,self._closed_months())

 def _skip(self,row,reason,detail='',detection=None):
  row=row or {}
  detail=(detail or SKIP_REASONS.get(reason,''))[:500]
  if detection is None:
   try:detection=beijing_month(row['measured_at']) if row.get('measured_at') else None
   except (KeyError,ValueError):detection=None
  item={'sample_code':row.get('sample_code'),'test_name':row.get('test_name'),'detection_month':detection,'reason':reason,'detail':detail}
  self.skipped.append(item)
  self.s.add(BillingSkip(run_id=getattr(self.run,'id',None),run_name=getattr(self.run,'name','') or '',sample_code=item['sample_code'],test_name=item['test_name'],detection_month=detection,reason=reason,detail=detail,created_at=utcnow()))

 def _first_ingestion(self,row,detection,billing,now):
  code=row['sample_code'];name=row['test_name']
  existing=self.s.query(BillingIngestion).filter_by(sample_code=code,test_name=name).first()
  if existing is not None:return existing,False
  # Savepoint: if a concurrent writer wins the unique key, only this row's
  # registration is rolled back — the outer target-row transaction survives.
  nested=self.s.begin_nested()
  try:
   ingestion=BillingIngestion(sample_code=code,test_name=name,run_id=getattr(self.run,'id',None),run_name=getattr(self.run,'name','') or '',batch_name=self.batch_name or '',detection_month=detection,billing_month=billing,source=self.source,ingested_at=now)
   self.s.add(ingestion)
   self.s.flush()
  except IntegrityError:
   nested.rollback()
   return self.s.query(BillingIngestion).filter_by(sample_code=code,test_name=name).first(),False
  nested.commit()
  self.registered+=1
  return ingestion,True

 def ingest(self,row,ingested_at=None):
  """Register one persisted target row and maybe add its billing line."""
  detection,billing=self.attribute(row['measured_at'])
  now=ingested_at or utcnow()
  existing=self.s.query(BillingIngestion).filter_by(sample_code=row['sample_code'],test_name=row['test_name']).first()
  if existing is not None:
   if existing.run_id==getattr(self.run,'id',None):detail='本次运行重复投递，已忽略'
   else:detail='首次入库来自运行 %s（%s）'%(existing.run_name,existing.detection_month)
   self._skip(row,'duplicate_key',detail,detection)
   return None
  ingestion,created=self._first_ingestion(row,detection,billing,now)
  if not created:
   self._skip(row,'duplicate_key','并发写入已占先，本次不重复计件',detection)
   return None
  if not self.charge:
   self._skip(row,'historical' if self.source=='historical' else 'restore',detection=detection)
   return None
  if not self.enabled:
   self._skip(row,'billing_disabled','来源月份 %s'%detection,detection)
   return None
  late=billing!=detection
  after_close=billing in (self._closed_after or set())
  if late:reason='检测月份 %s 已封账，按提交先后归入 %s'%(detection,billing)
  elif after_close:reason='运行先提交、封账后入库，保留月份 %s，月结快照需复核'%billing
  else:reason=''
  line=BillingLine(ingestion_id=ingestion.id,run_id=self.run.id,run_name=self.run.name,submit_seq=int(getattr(self.run,'submit_seq',0) or 0),batch_name=self.batch_name or '',sample_code=row['sample_code'],test_name=row['test_name'],measured_at=row['measured_at'],detection_month=detection,billing_month=billing,adjusted=late,late=late,after_close=after_close,reason=reason,source=self.source,quantity=1,created_at=now)
  self.s.add(line)
  self.s.flush()
  self.lines+=1
  self.created.append(record(line))
  return line

 def summary(self):
  return {'enabled':self.enabled,'charged':self.charge,'billed_lines':self.lines,'registered_ingestions':self.registered,'skipped':len(self.skipped),'skip_details':self.skipped}


def period_record(p,current=None):
 data={'month':p.month,'status':p.status,'close_seq':p.close_seq,'closed_at':present(p.closed_at),'closed_by':p.closed_by,'note':p.note,'closed_count':p.closed_count,'closed_checksum':p.closed_checksum}
 if current is not None:
  data['current_count']=current[0]
  data['current_checksum']=current[1]
  data['drifted']=current[0]!=p.closed_count or current[1]!=p.closed_checksum
 return data


def _month_lines(s,month):
 return s.query(BillingLine).filter_by(billing_month=month).order_by(BillingLine.sample_code,BillingLine.test_name).all()


def month_digest(lines):
 return hashlib.sha256(json.dumps([[l.sample_code,l.test_name,l.run_id,l.source,l.quantity] for l in lines],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def _month_counts(s,month):
 lines=_month_lines(s,month)
 return len(lines),month_digest(lines)


def status():
 with Session() as s:
  row=s.get(BillingControl,CONTROL_ID)
  enabled=bool(row.enabled) if row is not None else True
  counter=int(row.counter) if row is not None else 0
  backfilled=bool(row.backfilled) if row is not None else False
  periods=[period_record(p,_month_counts(s,p.month)) for p in s.query(BillingPeriod).order_by(BillingPeriod.month).all()]
  return {'enabled':enabled,'counter':counter,'backfilled':backfilled,'closed_months':[p['month'] for p in periods if p['status']=='closed'],'open_months':[p['month'] for p in periods if p['status']!='closed'],'periods':periods,'total_lines':s.query(BillingLine).count(),'total_skips':s.query(BillingSkip).count()}


def set_enabled(enabled):
 with Session() as s:
  row=_control_locked(s)
  row.enabled=bool(enabled)
  s.commit()
  return {'enabled':bool(enabled)}


def _pending_runs(s,seq):
 return [{'id':r.id,'name':r.name,'status':r.status,'submit_seq':r.submit_seq} for r in s.query(Run).filter(Run.status.in_(('queued','running')),Run.submit_seq<=seq).order_by(Run.id).all()]


def close(month,user,note=''):
 if not valid_month(month):raise HTTPException(422,'封账月份须为 YYYY-MM')
 with Session() as s:
  row=_control_locked(s)
  period=s.query(BillingPeriod).filter_by(month=month).first()
  if period is not None and period.status=='closed':raise HTTPException(409,'该月份已经封账，无需重复封账')
  seq=row.counter+1
  row.counter=seq
  s.flush()
  lines=_month_lines(s,month)
  if period is None:
   period=BillingPeriod(month=month);s.add(period)
  period.status='closed'
  period.close_seq=seq
  period.closed_at=utcnow()
  period.closed_by=user
  period.note=note
  period.closed_count=len(lines)
  period.closed_checksum=month_digest(lines)
  s.flush()
  result={'month':month,'close_seq':seq,'billed_count':len(lines),'checksum':period.closed_checksum,'pending_runs':_pending_runs(s,seq)}
  s.commit()
 return result


def reopen(month,user,note=''):
 if not valid_month(month):raise HTTPException(422,'月份须为 YYYY-MM 格式')
 with Session() as s:
  row=_control_locked(s)
  period=s.query(BillingPeriod).filter_by(month=month).first()
  if period is None or period.status!='closed':raise HTTPException(409,'该月份未封账，无需解封')
  row.counter+=1
  period.status='open'
  period.note=(note or '')[:500]
  period.closed_by=user
  period.closed_at=utcnow()
  s.commit()
  return {'month':month,'status':'open','existing_lines_kept':True}


def register_restored(s,run,batch_name,rows):
 """Register ingestions for restored rows that predate the billing ledger.

 A restore never bills. This only fills a missing first-ingestion record (e.g.
 a database migrated before this feature existed) so a later re-migration of
 the same ``sample_code + test_name`` cannot charge a second time. Existing
 ingestions are left exactly as they were, which keeps a retried restore — and
 therefore a lost receipt — fully idempotent.
 """
 registered=0
 ledger=Ledger(s,run,batch_name,source='restore',charge=False)
 for row in rows:
  detection,billing=ledger.attribute(row['measured_at'])
  if s.query(BillingIngestion).filter_by(sample_code=row['sample_code'],test_name=row['test_name']).first() is not None:continue
  _,created=ledger._first_ingestion(row,detection,billing,utcnow())
  if created:registered+=1
 return registered


def month_detail(month):
 if not valid_month(month):raise HTTPException(422,'月份须为 YYYY-MM 格式')
 with Session() as s:
  period=s.query(BillingPeriod).filter_by(month=month).first()
  lines=_month_lines(s,month)
  count,checksum=_month_counts(s,month)
  skips=s.query(BillingSkip).filter(BillingSkip.detection_month==month).order_by(BillingSkip.id.desc()).limit(500).all()
  return {'month':month,'period':period_record(period,(count,checksum)) if period else None,'lines':[record(l) for l in lines],'count':count,'checksum':checksum,'skips':[record(x) for x in skips]}


def explain(month):
 """Every line in a month with its attribution chain and its origin."""
 if not valid_month(month):raise HTTPException(422,'月份须为 YYYY-MM 格式')
 out=[]
 with Session() as s:
  for line in _month_lines(s,month):
   chain=[line.detection_month]
   cursor=line.detection_month
   while cursor!=line.billing_month and len(chain)<120:
    cursor=next_month(cursor)
    chain.append(cursor)
   run=s.get(Run,line.run_id)
   ingestion=s.query(BillingIngestion).filter_by(sample_code=line.sample_code,test_name=line.test_name).first()
   out.append({'line_id':line.id,'run_id':line.run_id,'run_name':line.run_name,'batch_name':line.batch_name,'sample_code':line.sample_code,'test_name':line.test_name,'measured_at':line.measured_at,'detection_month':line.detection_month,'billing_month':line.billing_month,'adjusted':line.adjusted,'after_close':line.after_close,'reason':line.reason,'source':line.source,'origin_chain':chain,'billing_run':'run#%s %s'%(line.run_id,line.run_name),'first_ingested_at':present(ingestion.ingested_at) if ingestion else None,'first_ingestion_run':('run#%s %s'%(ingestion.run_id,ingestion.run_name)) if ingestion else None,'run_submit_seq':line.submit_seq,'run_submitted_at':present(run.created_at) if run else None})
 return out


def list_lines(month=None,run_id=None,limit=500):
 with Session() as s:
  q=s.query(BillingLine)
  if month:q=q.filter_by(billing_month=month)
  if run_id is not None:q=q.filter_by(run_id=run_id)
  return [record(x) for x in q.order_by(BillingLine.id.desc()).limit(min(max(limit,1),2000)).all()]


def list_skips(run_id=None,limit=200):
 with Session() as s:
  q=s.query(BillingSkip)
  if run_id is not None:q=q.filter_by(run_id=run_id)
  return [record(x) for x in q.order_by(BillingSkip.id.desc()).limit(min(max(limit,1),1000)).all()]


def backfill():
 """Register pre-ledger target rows as historical ingestions, never charging.

 Replays completed runs in creation order through the same first-ingestion
 rule so the earliest run keeps the natural key.  Run once; idempotent.
 """
 with Session() as s:
  row=_control_locked(s)
  if row.backfilled:return {'already_backfilled':True,'registered':0,'skipped':0,'runs':0}
  registered=skipped=runs=0
  for run in s.query(Run).filter(Run.status=='completed').order_by(Run.created_at,Run.id).all():
   if s.query(BillingIngestion).filter_by(run_id=run.id).count():continue
   ledger=Ledger(s,run,(run.snapshot or {}).get('batch',{}).get('name',''),source='historical',charge=False)
   for target in s.query(TargetRow).filter_by(run_id=run.id).order_by(TargetRow.ordinal).all():
    ledger.ingest(target.payload,ingested_at=run.created_at or utcnow())
   registered+=ledger.registered;skipped+=len(ledger.skipped);runs+=1
   s.flush()
  row.backfilled=True
  s.commit()
 return {'already_backfilled':False,'registered':registered,'skipped':skipped,'runs':runs}
