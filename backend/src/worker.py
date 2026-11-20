import time,logging,redis
from pathlib import Path
from src.config.settings import REDIS_URL
from src.config.database import Session
from src.models.run import Run
from src.services.migration import Cancelled
from src.services.execution import process,InvalidRun
q=redis.Redis.from_url(REDIS_URL)
def progress(run_id,info):
 Path('/tmp/worker-heartbeat').touch()
 with Session() as s:
  r=s.get(Run,run_id)
  if r.cancel_requested or r.status=='cancelled':raise Cancelled()
  r.progress=info;s.commit()
while True:
 Path('/tmp/worker-heartbeat').touch()
 try:
  item=q.blpop('migration-runs',timeout=2)
  if not item:continue
  try:process(int(item[1]),lambda info:progress(int(item[1]),info))
  except InvalidRun:continue
 except Exception:logging.exception('worker loop');time.sleep(2)
