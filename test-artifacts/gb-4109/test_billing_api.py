"""API-level checks for the billing/close routes against the real FastAPI app."""
import os,sys
os.environ.setdefault('DATABASE_URL','sqlite+pysqlite:///:memory:')
os.environ.setdefault('JWT_SECRET','test-secret')
os.environ.setdefault('REDIS_URL','redis://localhost:6379/15')
os.environ.setdefault('SEED_DEMO','false')
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..','..','backend'))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from src.config.database import Base,Session


@pytest.fixture()
def client(monkeypatch):
    engine=create_engine('sqlite+pysqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Session.configure(bind=engine)
    import src.models.billing,src.models.run,src.models.batch,src.models.mapping,src.models.identity  # noqa
    Base.metadata.create_all(engine)
    from src.models.identity import User
    from src.models.batch import Batch
    from src.models.mapping import Mapping
    from src.utils.security import hash_password
    from src.services import runs as runs_service
    monkeypatch.setattr(runs_service.queue,'rpush',lambda *a,**k:1)
    rules=[{'source':a,'target':b,'type':c,'required':True} for a,b,c in [('委托号','sample_code','text'),('检测项','test_name','text'),('结果','result_value','decimal'),('单位','unit','text'),('时间','measured_at','datetime')]]
    rows=[{'委托号':'JS-A','检测项':'拉伸强度','结果':'1','单位':'MPa','时间':'2026-11-17T09:00:00+08:00'},
          {'委托号':'JS-B','检测项':'拉伸强度','结果':'2','单位':'MPa','时间':'2026-10-31T20:00:00+08:00'}]
    with Session() as s:
        for role in ('admin','operator','viewer'):
            s.add(User(email=role+'@x',role=role,password=hash_password('pw')))
        b=Batch(name='b',source_file='x.json',rows=rows);s.add(b);s.flush()
        m=Mapping(name='m',batch_id=b.id,rules=rules,chunk_size=4);s.add(m);s.commit();mid=m.id
    from fastapi.testclient import TestClient
    from src.main import app
    c=TestClient(app)
    def auth(email):
        r=c.post('/auth/login',json={'email':email,'password':'pw'})
        return {'Authorization':'Bearer '+r.json()['token']}
    c.admin=auth('admin@x');c.operator=auth('operator@x');c.viewer=auth('viewer@x');c.mapping_id=mid
    return c


def test_operator_can_read_billing_but_not_close(client):
    assert client.get('/billing',headers=client.operator).status_code==200
    assert client.post('/billing/months/2026-11/close',json={'note':''},headers=client.operator).status_code==403
    assert client.post('/billing/months/2026-11/close',json={'note':''},headers=client.admin).status_code==200


def test_invalid_month_and_repeat_close(client):
    assert client.post('/billing/months/2026-13/close',json={'note':''},headers=client.admin).status_code==422
    assert client.post('/billing/months/2026-11/close',json={'note':''},headers=client.admin).status_code==200
    assert client.post('/billing/months/2026-11/close',json={'note':''},headers=client.admin).status_code==409


def test_run_then_explain_shows_beijing_months(client):
    made=client.post('/runs',json={'name':'api-run','mapping_id':client.mapping_id},headers=client.operator)
    assert made.status_code==200 and made.json()['submit_seq']>=1
    from src.services.execution import process
    process(made.json()['id'])
    state=client.get('/billing',headers=client.viewer).json()
    assert state['total_lines']==2
    # No month is closed yet, so each item stays in its detected Beijing month.
    nov=client.get('/billing/months/2026-11',headers=client.viewer).json()
    october=client.get('/billing/months/2026-10',headers=client.viewer).json()
    assert {l['sample_code'] for l in nov['lines']}=={'JS-A'}
    assert {l['sample_code'] for l in october['lines']}=={'JS-B'}
    # 20:00 Beijing on Oct 31 stays October, unlike 20:00 UTC.
    explain=client.get('/billing/months/2026-10/explain',headers=client.admin).json()
    assert explain[0]['detection_month']=='2026-10' and explain[0]['billing_month']=='2026-10'
    # Explain is admin-only.
    assert client.get('/billing/months/2026-10/explain',headers=client.viewer).status_code==403
    # Closing October pushes later-submitted October work to the first open month.
    assert client.post('/billing/months/2026-10/close',json={'note':''},headers=client.admin).status_code==200
    late=client.post('/runs',json={'name':'late-run','mapping_id':client.mapping_id},headers=client.operator)
    from src.models.batch import Batch
    from src.models.mapping import Mapping
    from src.models.run import Run
    with Session() as s:
        r=s.get(Run,late.json()['id'])
        b=s.query(Batch).filter_by(name='b').one()
        b.rows=[{'委托号':'JS-C','检测项':'拉伸强度','结果':'3','单位':'MPa','时间':'2026-10-15T09:00:00+08:00'}]
        r.snapshot={'batch':{'rows':b.rows,'name':b.name},'mapping':r.snapshot['mapping']}
        s.commit()
    process(late.json()['id'])
    moved=client.get('/billing/months/2026-11',headers=client.admin).json()
    assert {l['sample_code'] for l in moved['lines']}=={'JS-A','JS-C'}
    js_c=[l for l in moved['lines'] if l['sample_code']=='JS-C'][0]
    assert js_c['detection_month']=='2026-10' and js_c['adjusted'] is True


def test_toggle_and_backfill(client):
    assert client.post('/billing/toggle',json={'enabled':False},headers=client.admin).json()=={'enabled':False}
    made=client.post('/runs',json={'name':'off-run','mapping_id':client.mapping_id},headers=client.operator)
    from src.services.execution import process
    process(made.json()['id'])
    assert client.get('/billing',headers=client.admin).json()['total_lines']==0
    assert client.post('/billing/toggle',json={'enabled':True},headers=client.admin).json()=={'enabled':True}
    result=client.post('/billing/backfill',headers=client.admin).json()
    # Ingestions were recorded even while billing was off, so nothing is charged.
    assert result['already_backfilled'] is False and result['registered']==0
    assert client.get('/billing',headers=client.admin).json()['total_lines']==0


def test_restore_is_free_and_registers_missing_ingestion_once(client):
    made=client.post('/runs',json={'name':'restore-run','mapping_id':client.mapping_id},headers=client.operator)
    from src.services.execution import process
    process(made.json()['id'])
    backup=client.get(f'/runs/{made.json()["id"]}/backup',headers=client.admin).json()
    first=client.post(f'/runs/{made.json()["id"]}/restore',json={'backup':backup},headers=client.admin)
    assert first.status_code==200 and first.json()['billing']['charged_this_restore']==0
    lines=client.get('/billing',headers=client.admin).json()['total_lines']
    # Retried restore after a "lost receipt" is idempotent.
    second=client.post(f'/runs/{made.json()["id"]}/restore',json={'backup':backup},headers=client.admin)
    assert second.status_code==200
    assert client.get('/billing',headers=client.admin).json()['total_lines']==lines


def test_restore_backfills_ingestion_so_later_migration_stays_free(client):
    from src.models.billing import BillingIngestion,BillingLine,BillingControl,BillingSkip
    from src.models.run import Run,TargetRow
    from src.services.migration import digest,key_digest
    made=client.post('/runs',json={'name':'pre-ledger','mapping_id':client.mapping_id},headers=client.operator)
    from src.services.execution import process
    process(made.json()['id'])
    run_id=made.json()['id']
    backup=client.get(f'/runs/{run_id}/backup',headers=client.admin).json()
    # Simulate data created before the billing ledger existed: no ingestions or
    # lines, but the target rows and completed result are intact.
    with Session() as s:
        s.query(BillingLine).delete();s.query(BillingIngestion).delete();s.query(BillingSkip).delete()
        s.query(BillingControl).delete()
        s.commit()
    restored=client.post(f'/runs/{run_id}/restore',json={'backup':backup},headers=client.admin)
    assert restored.status_code==200 and restored.json()['billing']['registered_ingestions']==2
    # A new run over the same natural keys now stays free forever.
    again=client.post('/runs',json={'name':'same-keys','mapping_id':client.mapping_id},headers=client.operator)
    process(again.json()['id'])
    assert client.get('/billing',headers=client.admin).json()['total_lines']==0
