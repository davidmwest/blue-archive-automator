"""Paid checkout guards and mailbox accounting without real purchases."""
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import cv2
import pytest
from ba_automator.config import Config, ConfigError
from ba_automator.mail import MailRunner
from ba_automator.packs import PacksRunner, run_packs
from ba_automator.packs_state import PACKS, enabled, next_reset, read_state, state_path, write_state
from ba_automator.runtime import Capture, TaskError
from ba_automator.shop_runtime import ShopFrame
from ba_automator.shop_vision import ShopScreen, ShopVision, classify_shop
from ba_automator.tasks import task_plan
from ba_automator.vision import StartupVision

FIXTURES = Path(__file__).parent / 'fixtures'

@pytest.fixture(scope='module')
def vision():
    return ShopVision(StartupVision())

@pytest.mark.parametrize(('name','kind'), [
    ('current','store'), ('ap-checkout','purchase_confirm'), ('payment-current','checkout'),
    ('payment-after','backup_prompt'), ('purchase-game-result','delivered'),
    ('purchased-store','store'), ('product-mail','mail'), ('claim-product','claim_confirm'),
    ('product-receipt','receipt'), ('post-activation-mail','mail_empty'), ('active-pyroxene','store'),
])
def test_sanitized_live_screens(vision, name, kind):
    result=vision.analyze((FIXTURES/f'packs-{name}.png').read_bytes(), billing=name.startswith('payment-'))
    assert result.kind == kind
    if name=='current':
        assert result.cards['monthly']['state']=='active'
        assert result.cards['ap']['state']=='available' and result.cards['ap']['cents']==299
    if name=='active-pyroxene': assert result.cards['ap']['days']==14
    if name=='purchased-store': assert result.cards['ap']['state']=='mail'
    if name=='product-receipt': assert result.items==({'name':'Pyroxenes','quantity':176},)
    if name=='payment-current': assert result.pack=='ap' and result.cents==299

def test_missing_status_ocr_cannot_buy_an_owned_or_unclaimed_pack(vision):
    for name in ('active-pyroxene','purchased-store'):
        frame=cv2.imread(str(FIXTURES/f'packs-{name}.png'))
        words=[w for w in vision.startup.read(frame) if not 385<w.center[1]<447]
        result=classify_shop(frame,words)
        assert all(card['state']!='available' for card in result.cards.values())

def test_dimmed_store_is_not_a_purchase_target(vision):
    frame=cv2.imread(str(FIXTURES/'packs-current.png'))
    assert classify_shop((frame*.5).astype('uint8'),vision.startup.read(frame)).kind=='unknown'

def test_missing_product_or_price_prevents_play_checkout(vision):
    frame=cv2.imread(str(FIXTURES/'packs-payment-current.png'));words=vision.startup.read(frame)
    for excluded in ('$2.99','2-Week AP Pack','Blue Archive','1-tap buy'):
        assert classify_shop(frame,[w for w in words if w.text!=excluded],billing=True).kind=='billing_attention'

@pytest.fixture
def config(tmp_path):
    return Config(serial='127.0.0.1:5695',package='com.nexon.bluearchive',
                  run_dir=tmp_path/'runs',state_dir=tmp_path/'state',lock_dir=tmp_path/'locks')

def test_paid_options_default_off_and_mail_follows_packs(config):
    assert enabled(config)==()
    assert task_plan('packs',config)==('restart','packs','mail','red_dots')
    assert task_plan('mail',config)==('restart','mail','red_dots')
    assert 'packs' not in task_plan('daily',config)
    assert task_plan('daily',replace(config,packs_ap_enabled=True))[:5]==('restart','club','free_pack','packs','mail')
    for key,bad in [('packs_ap_enabled',1),('packs_ap_max_cents',True),('packs_monthly_max_cents',0),('packs_half_monthly_max_cents',10001)]:
        with pytest.raises(ConfigError): replace(config,**{key:bad})

def test_state_survives_restart_and_is_scoped_to_device(config):
    state=read_state(config);state['pending']={'pack':'ap','cents':299};state['blocked_reason']='verification required'
    write_state(config,state)
    assert read_state(config)==state
    other=replace(config,serial='127.0.0.1:5675')
    assert state_path(config)!=state_path(other) and read_state(other)['pending'] is None
    state_path(config).write_text('{')
    with pytest.raises(RuntimeError,match='state'): read_state(config)

def test_reset_uses_utc_and_not_local_day():
    assert next_reset(datetime(2026,9,24,16,tzinfo=timezone.utc))=='2026-09-24T19:01:00+00:00'
    assert next_reset(datetime(2026,9,24,20,tzinfo=timezone.utc))=='2026-09-25T19:01:00+00:00'

def pack_frame(config,state='available',cents=299):
    return ShopFrame(Capture(b'fixture',0,config.package),ShopScreen('store',cards={
        key:{'state':state,'days':None,'cents':cents,'target':(900,500)} for key in PACKS}))

@pytest.fixture
def runner(config):
    r=PacksRunner(replace(config,packs_ap_enabled=True),None,None,vision=object(),monotonic=lambda:0,sleep=lambda _:None)
    r.state=read_state(r.config)
    yield r
    r.journal.close()

def test_no_spending_for_disabled_owned_overpriced_or_uncertain_pack(runner):
    runner.tap=lambda *args:pytest.fail('must not send input')
    for state,cents in [('active',299),('mail',299),('unknown',299),('available',399)]:
        with pytest.raises(TaskError): runner.purchase(pack_frame(runner.config,state,cents),'ap')
    runner.config=replace(runner.config,packs_ap_enabled=False)
    with pytest.raises(TaskError,match='not enabled'): runner.purchase(pack_frame(runner.config),'ap')

@pytest.mark.parametrize(('kind','pack','cents','expected_taps'),[
    ('purchase_confirm','monthly',299,1),('purchase_confirm','ap',399,1),
    ('checkout','monthly',299,2),('checkout','ap',399,2),
])
def test_product_and_price_must_match_both_confirmations(runner,kind,pack,cents,expected_taps):
    valid=ShopFrame(Capture(b'fixture',0,runner.config.package),ShopScreen('purchase_confirm',(760,592),'ap',299))
    bad=ShopFrame(Capture(b'fixture',0,'com.android.vending'),ShopScreen(kind,(360,1215),pack,cents))
    screens=iter([bad] if kind=='purchase_confirm' else [valid,bad])
    runner.wait=lambda *args,**kwargs:next(screens)
    taps=[];runner.tap=lambda *args:taps.append(args)
    with pytest.raises(TaskError): runner.purchase(pack_frame(runner.config),'ap')
    assert len(taps)==expected_taps and read_state(runner.config)['pending'] is None

def test_charge_intent_is_durable_before_input_and_survives_payment_failure(runner):
    screens=iter([
        ShopFrame(Capture(b'fixture',0,runner.config.package),ShopScreen('purchase_confirm',(760,592),'ap',299)),
        ShopFrame(Capture(b'fixture',0,'com.android.vending'),ShopScreen('checkout',(360,1215),'ap',299)),
    ])
    def wait(*args,**kwargs):
        try:return next(screens)
        except StopIteration:runner.fail('Google Play declined payment')
    runner.wait=wait;charges=[]
    def tap(frame,target,detail):
        if frame.screen.kind=='checkout':
            assert read_state(runner.config)['pending']['pack']=='ap'
            charges.append(target)
    runner.tap=tap
    with pytest.raises(TaskError,match='declined'): runner.purchase(pack_frame(runner.config),'ap')
    assert charges==[(360,1215)] and read_state(runner.config)['pending']['pack']=='ap'

def test_any_pack_handler_error_blocks_automatic_schedule(config,monkeypatch):
    device=SimpleNamespace(connect=lambda:None,verify_package=lambda:None)
    def fail(runner):
        runner.state=read_state(config);runner.fail('Google Play needs payment verification')
    monkeypatch.setattr(PacksRunner,'run',fail)
    with pytest.raises(TaskError):run_packs(config,device,None,vision=object())
    assert 'verification' in read_state(config)['blocked_reason']

def test_unresolved_charge_is_never_resubmitted(runner):
    runner.allow_retry=True
    state=read_state(runner.config);state['pending']={'pack':'ap','cents':299};write_state(runner.config,state)
    frame=pack_frame(runner.config)
    runner.navigate=lambda *args:frame;runner.wait=lambda *args,**kwargs:frame;runner.settle_store=lambda:frame
    runner.purchase=lambda *args:pytest.fail('must not retry uncertain charge')
    with pytest.raises(TaskError,match='unresolved'):runner.run()


def test_explicit_recovery_requires_owned_pack_and_does_not_charge_again(runner):
    runner.allow_retry=True
    state=read_state(runner.config);state['pending']={'pack':'ap','cents':299};state['blocked_reason']='interrupted'
    write_state(runner.config,state)
    frame=pack_frame(runner.config,'active')
    runner.navigate=lambda *args:frame;runner.wait=lambda *args,**kwargs:frame;runner.settle_store=lambda:frame
    runner.purchase=lambda *args:pytest.fail('owned pack must not be bought again')
    runner.tap=lambda *args:None;runner.home=lambda:None
    assert runner.run().status=='success'
    saved=read_state(runner.config)
    assert saved['pending'] is None and saved['blocked_reason'] is None

def test_mail_logs_actual_pyroxene_receipt_and_balance_gain(config):
    runner=MailRunner(config,None,None,vision=object(),monotonic=lambda:0)
    try:
        png=(FIXTURES/'packs-product-receipt.png').read_bytes()
        screen=ShopScreen('receipt',items=({'name':'Pyroxenes','quantity':176},),balances={'pyroxenes':1176,'ap':100})
        runner.log_receipt(ShopFrame(Capture(png,0,config.package),screen),{'pyroxenes':1000,'ap':100})
        action=json.loads((config.state_dir/'important-actions.jsonl').read_text().splitlines()[-1])
        assert action['pyroxenes']==176 and action['items']==[{'name':'Pyroxenes','quantity':176}]
        assert '+176 Pyroxenes' in action['detail'] and Path(action['evidence']).is_file()
    finally:runner.journal.close()

@pytest.mark.parametrize('reason', ['declined', None])
def test_automatic_entry_cannot_bypass_payment_hold(runner, reason):
    state=read_state(runner.config)
    state['blocked_reason']=reason
    if reason is None:state['pending']={'pack':'ap','cents':299}
    write_state(runner.config,state)
    runner.navigate=lambda *args:pytest.fail('blocked jobs must not navigate')
    with pytest.raises(TaskError,match='disabled'):runner.run()
    assert read_state(runner.config)==state


def test_successful_checkout_sends_one_charge_and_confirms_ownership(runner):
    def frame(kind, target, billing=False):
        return ShopFrame(Capture(b'fixture',0,'com.android.vending' if billing else runner.config.package),
                         ShopScreen(kind,target,'ap',299))
    screens=iter([frame('purchase_confirm',(760,592)),frame('checkout',(360,1215),True),
                  frame('backup_prompt',(668,280),True),frame('delivered',(640,505))])
    runner.wait=lambda *args,**kwargs:next(screens)
    runner.settle_store=lambda:pack_frame(runner.config,'mail')
    inputs=[]
    runner.tap=lambda f,*args:inputs.append(f.screen.kind)
    result=runner.purchase(pack_frame(runner.config),'ap')
    assert inputs==['store','purchase_confirm','checkout','backup_prompt','delivered']
    assert result.screen.cards['ap']['state']=='mail'
    assert read_state(runner.config)['pending'] is None


@pytest.mark.parametrize('case', ['valid','expired','wrong_foreground','foreground_changed','rotation','bad_target'])
def test_billing_input_checks_foreground_dimensions_and_deadline(config,monkeypatch,case):
    from ba_automator.adb import AdbDevice,DeviceError
    device=AdbDevice(config);inputs=[]
    foreground=iter(['com.android.vending',config.package] if case=='foreground_changed'
                    else [config.package if case=='wrong_foreground' else 'com.android.vending']*2)
    monkeypatch.setattr(device,'foreground_package',lambda:next(foreground))
    image='packs-current.png' if case=='rotation' else 'packs-payment-current.png'
    monkeypatch.setattr(device,'screenshot',lambda:(FIXTURES/image).read_bytes())
    monkeypatch.setattr(device,'_check_shared_server',lambda:None)
    monkeypatch.setattr(device,'_execute',lambda *args,**kwargs:inputs.append(args))
    def tap():return device.tap_billing(900 if case=='bad_target' else 360,1215,
                                       size=(720,1280),deadline=5,monotonic=lambda:6 if case=='expired' else 0)
    if case in {'valid','expired'}:assert tap() is (case=='valid')
    else:
        with pytest.raises(DeviceError):tap()
    assert len(inputs)==(1 if case=='valid' else 0)


def test_billing_frames_never_enter_saved_trace(runner):
    runner.device=SimpleNamespace(foreground_package=lambda:'com.android.vending',
                                 screenshot=lambda:(FIXTURES/'packs-payment-current.png').read_bytes())
    runner.vision=SimpleNamespace(analyze=lambda *args,**kwargs:ShopScreen('billing_attention'))
    runner.journal.screenshot=lambda *_:pytest.fail('payment screenshot must not be saved')
    assert runner.capture().screen.kind=='billing_attention'
    assert runner.last_frame is None
