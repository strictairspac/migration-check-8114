"""Unit + integration checks for Beijing-time billing and month close.

Run from the repo root:
    PYTHONPATH=backend python3 -m pytest test-artifacts/gb-4109/test_billing.py -q

Uses an in-memory SQLite database; PostgreSQL-specific row locking is a no-op
there, so ordering tests drive the shared sequence counter directly.
"""
import os,sys,json
os.environ.setdefault('DATABASE_URL','sqlite+pysqlite:///:memory:')
os.environ.setdefault('JWT_SECRET','test-secret')
os.environ.setdefault('REDIS_URL','redis://localhost:6379/15')
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..','..','backend'))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from src.config import database
from src.config.database import Base,Session


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    engine=create_engine('sqlite+pysqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session.configure(bind=engine)
    import src.models.billing,src.models.run,src.models.batch,src.models.mapping,src.models.identity  # noqa
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


RULES=[{'source':a,'target':b,'type':c,'required':True} for a,b,c in [('委托号','sample_code','text'),('检测项','test_name','text'),('结果','result_value','decimal'),('单位','unit','text'),('时间','measured_at','datetime')]]


def make_run(seq=None,name='r1',status='queued',rows=None,rules=None):
    from src.models.batch import Batch
    from src.models.mapping import Mapping
    from src.models.run import Run
    from src.services.billing import submit_seq
    rules=rules if rules is not None else []
    with Session() as s:
        b=Batch(name='b-'+name,source_file='x.json',rows=rows or [])
        s.add(b);s.flush()
        m=Mapping(name='m-'+name,batch_id=b.id,rules=rules,chunk_size=4)
        s.add(m);s.flush()
        r=Run(name=name,batch_id=b.id,mapping_id=m.id,status=status,submit_seq=0,snapshot={'batch':{'rows':rows or [],'name':b.name},'mapping':{'rules':rules,'chunk_size':4}})
        s.add(r);s.flush()
        # Always allocate through the real shared clock (which also creates the
        # control row); a pinned seq then simulates an earlier/later submit.
        r.submit_seq=submit_seq(s)
        if seq is not None:r.submit_seq=seq
        s.commit();s.refresh(r)
        return r.id


def row(code,month='2026-11',day='17',utc=False):
    tz='+00:00' if utc else '+08:00'
    return {'sample_code':code,'test_name':'拉伸强度','result_value':'420.000000','unit':'MPa','measured_at':f'{month}-{day}T09:00:00{tz}'}


def source_row(code,month='2026-11',day='17',utc=False):
    tz='+00:00' if utc else '+08:00'
    return {'委托号':code,'检测项':'拉伸强度','结果':'420.0','单位':'MPa','时间':f'{month}-{day}T09:00:00{tz}'}


def bill(run_id,rows,source='migration',charge=True):
    from src.models.run import Run
    from src.services.billing import Ledger
    with Session() as s:
        r=s.get(Run,run_id)
        ledger=Ledger(s,r,'b',source=source,charge=charge)
        for x in rows:ledger.ingest(x)
        s.commit()
        return ledger.summary()


# ---------------------------------------------------------------- attribution
def test_beijing_month_boundary():
    from src.services.billing_rules import beijing_month
    # 16:30 UTC on Oct 31 is 00:30 on Nov 1 in Beijing.
    assert beijing_month('2026-10-31T16:30:00+00:00')=='2026-11'
    assert beijing_month('2026-10-31T15:59:00+00:00')=='2026-10'
    # Naive timestamps are Beijing by convention.
    assert beijing_month('2026-11-01T00:00:00')=='2026-11'


def test_resolve_skips_consecutive_closed_months():
    from src.services.billing_rules import resolve_billing_month
    assert resolve_billing_month('2026-09',{'2026-09','2026-10'})=='2026-11'
    assert resolve_billing_month('2026-12',{'2026-12'})=='2027-01'
    assert resolve_billing_month('2026-11',set())=='2026-11'


# ----------------------------------------------------------------- first only
def test_same_key_billed_once_across_runs():
    a=make_run(1,'a');b=make_run(2,'b')
    assert bill(a,[row('JS-1')])['billed_lines']==1
    summary=bill(b,[row('JS-1')])
    assert summary['billed_lines']==0 and summary['skipped']==1
    from src.services.billing import list_lines
    assert len(list_lines())==1


def test_restore_and_backfill_never_charge():
    a=make_run(1,'a')
    bill(a,[row('JS-1')])
    summary=bill(a,[row('JS-1')],source='restore',charge=False)
    assert summary['billed_lines']==0
    from src.services.billing import list_lines
    assert len(list_lines())==1


def test_disabled_does_not_back_charge_when_enabled_later():
    from src.services.billing import set_enabled,list_lines
    set_enabled(False)
    a=make_run(1,'a');b=make_run(2,'b')
    assert bill(a,[row('JS-1')])['billed_lines']==0
    set_enabled(True)
    # Same key re-ingested: still only a registered first ingestion, not billed.
    assert bill(b,[row('JS-1')])['billed_lines']==0
    # A brand-new key is billed normally.
    assert bill(b,[row('JS-2')])['billed_lines']==1
    assert len(list_lines())==1


# ------------------------------------------------------------- close and race
def test_late_entry_moves_to_first_open_month():
    from src.services.billing import close,list_lines
    close('2026-10','admin')
    close('2026-11','admin')
    late=make_run(3,'late')
    assert bill(late,[row('JS-1',month='2026-10')])['billed_lines']==1
    from src.services.billing import explain
    line=explain('2026-12')[0]
    assert line['detection_month']=='2026-10' and line['billing_month']=='2026-12'
    assert line['origin_chain']==['2026-10','2026-11','2026-12'] and line['adjusted'] is True


def test_run_submitted_before_close_keeps_month_even_if_persisted_after():
    from src.services.billing import close,explain
    early=make_run(1,'early')
    close('2026-11','admin')          # close_seq becomes 2 > submit_seq 1
    bill(early,[row('JS-1')])
    line=explain('2026-11')[0]
    assert line['billing_month']=='2026-11' and line['after_close'] is True
    assert line['adjusted'] is False


def test_open_months_unaffected():
    from src.services.billing import close
    close('2026-11','admin')
    r=make_run(2,'r')
    bill(r,[row('JS-1',month='2026-12')])
    from src.services.billing import list_lines
    assert [l['billing_month'] for l in list_lines()]==['2026-12']


def test_close_is_idempotent_and_snapshots_month():
    from src.services.billing import close,status,month_detail
    r=make_run(1,'r');bill(r,[row('JS-1')])
    first=close('2026-11','admin','monthly')
    assert first['billed_count']==1 and first['close_seq']==2
    with pytest.raises(Exception):close('2026-11','admin')
    detail=month_detail('2026-11')
    assert detail['period']['closed_count']==1 and detail['count']==1
    assert detail['period']['drifted'] is False
    assert status()['closed_months']==['2026-11']


def test_reopen_allows_new_lines_and_flags_drift():
    from src.services.billing import close,reopen,month_detail
    r=make_run(1,'r');bill(r,[row('JS-1')]);close('2026-11','admin')
    reopen('2026-11','admin')
    assert month_detail('2026-11')['period']['status']=='open'
    # After re-opening, a later run is submitted after the (now void) close.
    r2=make_run(2,'r2')
    assert bill(r2,[row('JS-2')])['billed_lines']==1
    assert month_detail('2026-11')['count']==2


# ------------------------------------------------------------------- atomicity
def test_ledger_failure_rolls_back_target_rows_and_lines(monkeypatch):
    from src.models.run import Run,TargetRow
    from src.models.billing import BillingLine
    from src.services import execution
    run_id=make_run(1,'r',rows=[source_row('JS-1')],rules=RULES)
    real=execution.Ledger.ingest
    def boom(self,row_,ingested_at=None):
        real(self,row_,ingested_at)
        raise RuntimeError('ledger exploded')
    monkeypatch.setattr(execution.Ledger,'ingest',boom)
    execution.process(run_id)
    with Session() as s:
        r=s.get(Run,run_id)
        assert r.status=='failed'
        assert s.query(TargetRow).filter_by(run_id=run_id).count()==0
        assert s.query(BillingLine).count()==0


def test_replayed_delivery_is_idempotent():
    from src.services import execution
    from src.services.billing import list_lines
    run_id=make_run(1,'r',rows=[source_row('JS-1'),source_row('JS-2')],rules=RULES)
    execution.process(run_id)
    assert len(list_lines())==2
    # Receipt lost: the queue redelivers the same run. It is no longer queued,
    # so the worker ignores it and no new lines appear.
    with pytest.raises(execution.InvalidRun):execution.process(run_id)
    assert len(list_lines())==2
    # Replaying the billing step for the same rows is also a no-op.
    assert bill(run_id,[row('JS-1'),row('JS-2')])['billed_lines']==0
    assert len(list_lines())==2
    from src.models.run import Run
    with Session() as s:assert s.get(Run,run_id).status=='completed'


def test_duplicate_natural_key_in_one_run_billed_once():
    from src.models.run import Run
    from src.services import execution
    run_id=make_run(1,'r',rows=[source_row('JS-1','2026-11','17'),source_row('JS-1','2026-11','18')],rules=RULES)
    execution.process(run_id)
    with Session() as s:
        r=s.get(Run,run_id)
        assert r.result['accepted_count']==1 and r.result['rejected_count']==1
        assert r.result['billing']['billed_lines']==1


# --------------------------------------------------------------- run creation
def test_create_takes_submit_seq_and_orders_against_close(monkeypatch):
    from src.services import runs,billing
    monkeypatch.setattr(runs.queue,'rpush',lambda *a,**k:1)
    from src.models.batch import Batch
    from src.models.mapping import Mapping
    from src.schemas.business import RunInput
    with Session() as s:
        b=Batch(name='b',source_file='x.json',rows=[row('JS-1')]);s.add(b);s.flush()
        m=Mapping(name='m',batch_id=b.id,rules=RULES,chunk_size=4);s.add(m);s.commit();mid=m.id
    made=runs.create(RunInput(name='first',mapping_id=mid))
    assert made['submit_seq']==1
    billing.close('2026-11','admin')
    after=runs.create(RunInput(name='second',mapping_id=mid))
    assert after['submit_seq']>2
