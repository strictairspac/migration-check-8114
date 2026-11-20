"""One-shot execution of a queued run.

Kept separate from the Redis loop so the same code path can be exercised in
tests and so the worker stays a thin consumer.
"""
import logging
from src.config.database import Session
from src.models.run import Run,TargetRow
from src.services.migration import migrate,make_backup,key_digest,Cancelled
from src.services.billing import Ledger,lock_control
from src.services.audit import log


class InvalidRun(Exception):pass


def process(run_id,progress=lambda info:None):
 """Run idempotently: only a queued run is executed; anything else is a no-op."""
 with Session() as s:
  r=s.query(Run).filter_by(id=run_id).with_for_update().first()
  if not r or r.status!='queued':raise InvalidRun(run_id)
  r.status='running';snapshot=r.snapshot;s.commit()
 try:
  result=migrate(snapshot['batch']['rows'],snapshot['mapping']['rules'],snapshot['mapping']['chunk_size'],progress)
  result['backup']=make_backup(result,run_id,snapshot['batch']['name'])
  with Session() as s:
   r=s.query(Run).filter_by(id=run_id).with_for_update().first()
   if r.cancel_requested:raise Cancelled()
   # The run row stays locked until commit, so its target rows and ledger are
   # written as one unit and a concurrent close sees either all or none.
   for i,row in enumerate(result['accepted_rows']):
    s.add(TargetRow(run_id=run_id,ordinal=i,key_digest=key_digest(row),payload=row))
   # Billing lives in this transaction: target rows, ledger and skips commit
   # together. Lock the control row first so a close cannot interleave between
   # reading closed months and committing the lines. An exception rolls all
   # three back, so a retry starts clean.
   lock_control(s)
   ledger=Ledger(s,r,snapshot['batch']['name'])
   s.flush()
   for row in result['accepted_rows']:ledger.ingest(row)
   result['billing']=ledger.summary()
   r.result=result;r.status='completed'
   s.commit()
  log('worker','run.completed',str(run_id))
  return result
 except Cancelled:
  with Session() as s:r=s.get(Run,run_id);r.status='cancelled';r.error='用户取消，未提交目标行';s.commit()
  log('worker','run.cancelled',str(run_id))
  return None
 except Exception as exc:
  logging.exception('migration failed')
  with Session() as s:r=s.get(Run,run_id);r.status='failed';r.error=str(exc)[:1000];s.commit()
  return None
