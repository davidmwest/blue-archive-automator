"""Deterministic, opt-in event clears and priority sweeps.

Quest first-clear requests are separate jobs. Enabled event farming completes
the reviewed Stories first, and never falls back after uncertain event progress.
"""
import re
from . import event_state
from .event_priority import available_event
from .event_inspection import enter_event
from .event_vision import QuestVision
from .vision import read_game_words
from .shop_vision import text_in
from .crafting_vision import within
from .spend_ap import APRunner, SweepDeferred
from .ap_policy import sweep_count
from .locking import InstanceLock
from .loot_receipts import inspect_receipt

LISTS = {'event_page', 'event_list'}


def quest_target(words, stage):
    """Pair the numbered quest row with its Enter button; never guess a row."""
    rows = [w for w in within(words, (700,150,772,690))
            if w.confidence >= .9 and re.fullmatch(r'0?[1-9]|1[0-2]', w.text.strip())
            and int(w.text.strip()) == stage]
    if len(rows) != 1:
        return None
    entries = [w for w in within(words, (1060,150,1200,690))
               if w.confidence >= .9 and w.normalized == 'enter'
               and abs(w.center[1] - rows[0].center[1]) < 35]
    return entries[0].center if len(entries) == 1 else None


def quest_detail(runner, stage, *, allow_unavailable=False):
    frame = runner.wait(LISTS)
    runner.tap(frame, (937,110), 'Open event Quest tab')
    last_rows, unchanged = None, 0
    for attempt in range(14):
        frame = runner.wait(LISTS)
        words = read_game_words(frame.capture.png, runner.startup)
        target = quest_target(words, stage)
        if target is not None:
            runner.tap(frame, target, f'Inspect event Quest {stage}')
            return runner.wait('detail', predicate=lambda s: s.strategy == 'event' and s.stage == str(stage))
        visible = [int(w.text.strip()) for w in within(words,(700,150,772,690))
                   if w.confidence >= .9 and re.fullmatch(r'0?[1-9]|1[0-2]',w.text.strip())]
        if not visible:
            runner.fail('Event quest numbers are unreadable; no AP spent')
        # Locked rows may have a number but no Enter button. A farm visit
        # can inspect an earlier stage; a first-clear job must not guess.
        if allow_unavailable and stage in visible:
            return None
        rows = tuple(visible)
        unchanged = unchanged + 1 if rows == last_rows else 0
        last_rows = rows
        if allow_unavailable and unchanged >= 2:
            return None
        points = ((945,295),(945,535)) if stage < min(visible) else ((945,535),(945,295))
        if (not frame.capture.is_fresh(runner.clock())
                or runner.device.foreground_package() != runner.config.package):
            runner.fail('Event quest list changed before scrolling')
        runner.journal.record('intent',operation='swipe',points=points,detail=f'Find event Quest {stage}')
        if not runner.device.swipe(*points,duration_ms=650,deadline=frame.capture.deadline,monotonic=runner.clock):
            runner.fail('Event quest scroll expired')
        runner.actions += 1
        runner.sleep(1)
    runner.fail(f'Event Quest {stage} was not found; stopped without another battle')


def close_detail(runner, frame):
    runner.tap(frame,(1127,139),'Close event mission info')
    return runner.wait(LISTS)


def event_home(runner, frame):
    if frame.screen.kind == 'detail':
        frame = close_detail(runner,frame)
    if frame.screen.kind not in LISTS | {'event_board'}:
        runner.fail('Cannot leave an unrecognized event screen')
    runner.tap(frame,(1237,24),'Return home after event work')
    runner.home()


def battle_cost(frame, startup):
    words = read_game_words(frame.capture.png,startup)
    value = text_in(words,(970,460,1130,512)).replace(' ','')
    match = re.fullmatch(r'(\d+)[→➜](\d+)', value)
    if not match or int(match[1]) != frame.screen.ap:
        return None
    cost = int(match[1])-int(match[2])
    return cost if 0 < cost <= 30 else None


class EventClearRunner(APRunner):
    task = 'clear_event'

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.vision = QuestVision(self.startup,self.vision)

    def budget(self):
        if self.clock()-self.started > 14400 or self.actions >= 3500:
            self.fail('Event clear reached its time or input limit')

    def finish_battle(self, stage):
        deadline = self.clock()+360
        while self.clock() < deadline:
            frame = self.capture()
            if frame.screen.kind == 'event_complete':
                self.journal.save_image(f'quest-{stage}-result.png',frame.capture.png)
                self.tap(frame,frame.screen.target,'Confirm completed event battle')
                break
            if frame.screen.kind == 'event_battle' and frame.screen.auto is False:
                self.tap(frame,(1213,677),'Enable Auto for event battle')
            self.sleep(2)
        else:
            self.fail('Event battle did not reach a verified victory; stopped without replaying it')
        frame = self.wait({'event_bonus','receipt'},timeout=90)
        if frame.screen.kind == 'event_bonus':
            self.tap(frame,frame.screen.target,'Confirm event currency bonuses')
            frame = self.wait('receipt',timeout=90)
        path = self.run_dir / f'quest-{stage}-receipt.png'
        self.journal.save_image(path.name,frame.capture.png)
        frame = inspect_receipt(self,frame,path)
        self.tap(frame,frame.screen.target,'Return to event after recording drops')
        self.wait(LISTS)
        return quest_detail(self,stage)

    def run(self):
        event_state.ensure_safe(self.config)
        from .ap_state import read_state
        if read_state(self.config)['pending']:
            self.fail('An AP sweep is unresolved; clear its receipt before event battles')
        profile = available_event(self.wall_clock())
        if profile is None:
            self.fail('No supported event is currently playable')
        enter_event(self,self.startup)
        state = event_state.observe(self.config,profile)
        summary = 'All event quests have three-star clears'
        for stage in range(1,profile['quest_count']+1):
            frame = quest_detail(self,stage)
            if frame.screen.stars == 3:
                state['clears'][str(stage)] = 3
                event_state.write_state(self.config,state)
                close_detail(self,frame)
                continue
            cost = battle_cost(frame,self.startup)
            if cost is None:
                self.fail('Could not verify event battle AP cost; no battle started')
            if frame.screen.ap-cost < self.config.ap_floor:
                summary = f'Event clear stopped before Quest {stage}: keeping {self.config.ap_floor} AP'
                break
            before = frame.screen.ap
            self.tap(frame,(936,534),f'Set up event Quest {stage}')
            frame = self.wait('event_formation')
            self.tap(frame,(1200,205),'Open Quick Formation for this quest')
            frame = self.wait('event_quick')
            self.tap(frame,(623,593),'Use default Auto Formation')
            frame = self.wait('event_quick')
            self.tap(frame,(1168,593),'Confirm Auto Formation')
            frame = self.wait('event_formation')
            if frame.screen.ap is None or frame.screen.ap < before or frame.screen.ap-cost < self.config.ap_floor:
                self.fail('AP changed during event formation; no battle started')
            self.journal.save_image(f'quest-{stage}-auto-formation.png',frame.capture.png)
            state['pending'] = dict(stage=str(stage),ap_before=frame.screen.ap,cost=cost,run_dir=str(self.run_dir))
            event_state.write_state(self.config,state)
            self.important('event_battle_requested',f'Event Quest {stage}: default Auto Formation; {cost} AP',stage=str(stage),ap_cost=cost)
            self.tap(frame,(1180,660),f'Mobilize event Quest {stage}')
            frame = self.finish_battle(stage)
            if frame.screen.ap is None or frame.screen.ap < before-cost:
                self.fail('Event result AP did not verify; inspect the receipt')
            stars = frame.screen.stars
            self.journal.save_image(f'quest-{stage}-stars.png', frame.capture.png)
            state['clears'][str(stage)] = stars
            state['pending'] = None
            event_state.write_state(self.config,state)
            self.important('event_quest_cleared',f'Event Quest {stage}: {stars}/3 stars; rewards recorded',stage=str(stage),stars=stars)
            if stars != 3:
                summary = f'Stopped at event Quest {stage}: {stars}/3 stars'
                break
            close_detail(self,frame)
        else:
            frame = self.wait(LISTS)
        state['summary'] = summary
        state['declined'] = True  # one prompt per detected event; manual run can continue
        event_state.write_state(self.config,state)
        self.important('event_clear_finished',summary)
        event_home(self,frame if frame.screen.kind == 'detail' else self.wait(LISTS))
        return self.finish()


def run_clear_event(config,device,startup,**kwargs):
    with InstanceLock(config):
        runner = EventClearRunner(config,device,startup,**kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except BaseException as exc:
            runner.journal.record('finished',status='failed',detail=str(exc))
            raise
        finally:
            runner.journal.close()


def farm_event(runner, profile):
    """Return None only on verified goal completion; otherwise reserve this AP."""
    event_state.ensure_safe(runner.config)
    previous = runner.vision
    runner.vision = QuestVision(runner.startup,previous)
    try:
        frame = enter_event(runner,runner.startup)
        from .event_stories import complete_stories
        story_summary = complete_stories(runner, profile, frame)
        frame = runner.wait(LISTS)
        if story_summary is not None:
            event_home(runner, frame)
            return story_summary
        runner.tap(frame,(515,663),'Check event farming goal')
        board = runner.wait('event_board')
        if board.screen.round > profile['target_round']:
            event_home(runner,board)
            return None
        runner.tap(board,(55,25),'Return to event quests')
        runner.wait(LISTS)
        for stage in profile['farm_order']:
            frame = quest_detail(runner,stage,allow_unavailable=True)
            if frame is None:
                continue
            if frame.screen.stars == 3:
                if frame.screen.ap is None or not frame.screen.cost:
                    # Unreadable quantity/cost is not evidence that AP is low.
                    # Keep the event reservation and retry this same detail.
                    frame = runner.wait('detail', predicate=lambda s: (
                        s.strategy == 'event' and str(s.stage) == str(stage)
                        and s.stars == 3 and s.ap is not None and bool(s.cost)))
                count = sweep_count(frame.screen.ap,runner.config.ap_floor,frame.screen.cost)
                if count:
                    try:
                        frame = runner.sweep(frame,count)
                    except SweepDeferred as deferred:
                        frame = deferred.frame
                        summary = 'Event sweep deferred to the next AP visit; normal farming remains on hold'
                    else:
                        summary = f'Farmed event Quest {stage} ×{count}; keeping at least {runner.config.ap_floor} AP'
                else:
                    summary = 'Event farming: remaining AP cannot cover a sweep above the floor'
                event_home(runner,frame)
                return summary
            close_detail(runner,frame)
        event_home(runner,runner.wait(LISTS))
        return 'Event needs three-star quest clears; use the clear-event prompt. Normal AP farming remains on hold.'
    finally:
        runner.vision = previous
