from fastapi import APIRouter,Depends
from src.middlewares.auth import current,admin
from src.schemas.business import CloseNote,BillingToggle
from src.services import billing
from src.services.audit import log
router=APIRouter()
@router.get('/billing')
def state(u=Depends(current)):return billing.status()
@router.get('/billing/lines')
def lines(month:str|None=None,run_id:int|None=None,u=Depends(current)):return billing.list_lines(month,run_id)
@router.get('/billing/skips')
def skips(run_id:int|None=None,u=Depends(current)):return billing.list_skips(run_id)
@router.get('/billing/months/{month}')
def month(month:str,u=Depends(current)):return billing.month_detail(month)
@router.get('/billing/months/{month}/explain')
def explain(month:str,u=Depends(admin)):return billing.explain(month)
@router.post('/billing/months/{month}/close')
def close(month:str,data:CloseNote,u=Depends(admin)):
 r=billing.close(month,u.email,data.note);log(u.email,'billing.close',f'{month} seq={r["close_seq"]} count={r["billed_count"]}');return r
@router.post('/billing/months/{month}/reopen')
def reopen(month:str,u=Depends(admin)):
 r=billing.reopen(month,u.email);log(u.email,'billing.reopen',month);return r
@router.post('/billing/toggle')
def toggle(data:BillingToggle,u=Depends(admin)):
 r=billing.set_enabled(data.enabled);log(u.email,'billing.toggle',str(r['enabled']));return r
@router.post('/billing/backfill')
def backfill(u=Depends(admin)):
 r=billing.backfill();log(u.email,'billing.backfill',str(r));return r
