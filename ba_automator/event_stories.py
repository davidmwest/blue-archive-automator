"""Clear the reviewed event's story once, before repeatable AP farming.

The current event has narrative episodes and two battle episodes. Progress is
season-specific; a durable pending intent prevents replay after uncertain input.
"""
from . import event_state
from .event_story_vision import StoryVision
from .loot_receipts import REWARD_RECEIPT_TIMEOUT, inspect_receipt
from .spend_ap import VISIT_SECONDS, VISIT_INPUTS


STORY_TIMEOUT = 480
STORY_RESERVE = STORY_TIMEOUT + REWARD_RECEIPT_TIMEOUT + 120


def can_start_story(runner):
    return (VISIT_SECONDS - (runner.clock() - runner.started) > STORY_RESERVE
            and VISIT_INPUTS - runner.actions > 240)


def story_row(runner, stage):
    """Locate a numbered Story row, retaining overlap while scrolling."""
    last, stationary = None, 0
    for _ in range(16):
        frame = runner.wait('story_list')
        rows = frame.screen.rows
        selected = [row for row in rows if row.stage == stage]
        if len(selected) == 1:
            return frame, selected[0]
        visible = tuple(row.stage for row in rows)
        stationary = stationary + 1 if visible == last else 0
        if not visible or stationary >= 3:
            runner.fail(f'Event Story {stage} was not found; AP remains reserved')
        last = visible
        points = ((945,295),(945,535)) if stage < min(visible) else ((945,535),(945,295))
        if (not frame.capture.is_fresh(runner.clock())
                or runner.device.foreground_package() != runner.config.package):
            runner.fail('Event Story list changed before scrolling')
        runner.journal.record('intent', operation='swipe', points=points,
                              detail=f'Find event Story {stage}')
        if not runner.device.swipe(*points, duration_ms=650,
                                   deadline=frame.capture.deadline, monotonic=runner.clock):
            runner.fail('Event Story scroll expired')
        runner.actions += 1
        runner.sleep(1)
    runner.fail(f'Event Story {stage} was not found; AP remains reserved')


def return_to_quests(runner):
    frame = runner.wait('story_list')
    runner.tap(frame, (937,110), 'Return to event Quest tab')
    return runner.wait({'event_page','event_list'})


def finish_story(runner, stage):
    """Bounded, recognized dialogue/battle inputs; never blindly advance."""
    deadline = runner.clock() + STORY_TIMEOUT
    mobilized = False
    while runner.clock() < deadline:
        frame = runner.capture()
        kind = frame.screen.kind
        if kind == 'receipt':
            evidence = runner.run_dir / f'story-{stage}-receipt.png'
            runner.journal.save_image(evidence.name, frame.capture.png)
            frame = inspect_receipt(runner, frame, evidence)
            runner.tap(frame, frame.screen.target, 'Return after recording event Story rewards')
            return runner.wait('story_list', timeout=90)
        if kind in {'story_dialogue','story_menu','story_skip','event_complete','event_bonus'}:
            runner.tap(frame, frame.screen.target, f'Continue event Story {stage}: {kind}')
        elif kind == 'event_formation':
            if mobilized:
                runner.fail('Event Story returned to formation without a verified reward; no replay')
            # Battle stories show their narrative before formation. AP is
            # charged at Mobilize, so verify the floor again after Auto setup.
            if not getattr(frame.screen, 'preset', False):
                runner.tap(frame, (1200,205), 'Open Quick Formation for event Story')
                frame = runner.wait('event_quick')
                runner.tap(frame, (623,593), 'Use default Auto Formation for event Story')
                frame = runner.wait('event_quick')
                runner.tap(frame, (1168,593), 'Confirm event Story Auto Formation')
                frame = runner.wait('event_formation')
            state = event_state.read_state(runner.config)
            pending = state['pending']
            if (not pending or pending.get('mode') != 'story' or pending['stage'] != str(stage)
                    or frame.screen.ap is None or frame.screen.ap < pending['ap_before']
                    or frame.screen.ap-pending['cost'] < runner.config.ap_floor):
                runner.fail('AP changed during event Story formation; no battle started')
            pending['ap_before'] = frame.screen.ap
            event_state.write_state(runner.config, state)
            runner.journal.save_image(f'story-{stage}-formation.png', frame.capture.png)
            runner.tap(frame, (1180,660), f'Mobilize event Story {stage}')
            mobilized = True
        elif kind == 'event_battle' and frame.screen.auto is False:
            runner.tap(frame, (1213,677), 'Enable Auto for event Story battle')
        runner.sleep(1)
    runner.fail(f'Event Story {stage} did not reach a verified reward; inspect its trace before replaying')


def complete_stories(runner, profile, frame):
    """Leave on Quest tab. None permits quest farming; text reserves this AP."""
    state = event_state.observe(runner.config, profile)
    count = profile['story_count']
    if all(state['stories'].get(str(stage)) for stage in range(1, count+1)):
        return None
    previous = runner.vision
    runner.vision = StoryVision(runner.startup, previous)
    try:
        runner.tap(frame, (758,110), 'Clear event Stories before farming quests')
        for stage in range(1, count+1):
            if state['stories'].get(str(stage)):
                continue
            frame, row = story_row(runner, stage)
            if row.cleared:
                state['stories'][str(stage)] = True
                event_state.write_state(runner.config, state)
                continue
            if row.target is None:
                runner.fail(f'Event Story {stage} is locked; AP remains reserved for stories')
            if not can_start_story(runner):
                runner.event_deferred = True
                return_to_quests(runner)
                return 'Event Stories will continue next visit; quest and normal farming remain on hold'
            runner.tap(frame, row.target, f'Inspect event Story {stage}')
            frame = runner.wait({'story_detail','detail'}, predicate=lambda s: str(s.stage) == str(stage))
            if frame.screen.kind == 'story_detail':
                cost = frame.screen.cost
                close = (915,162)
            else:
                from .event_quests import battle_cost
                cost = battle_cost(frame,runner.startup)
                close = (1127,139)
            before = frame.screen.ap
            if cost != profile['story_ap_cost'] or before is None:
                runner.fail('Could not verify event Story AP cost; no episode started')
            if before-cost < runner.config.ap_floor:
                runner.tap(frame, close, 'Keep the AP floor; leave event Story unstarted')
                return_to_quests(runner)
                return (f'Event Story {stage} needs {cost} AP above the {runner.config.ap_floor} AP floor; '
                        'quest and normal farming remain on hold')
            if frame.screen.kind == 'detail':
                target = (936,534)
            else:
                target = frame.screen.target
            runner.journal.save_image(f'story-{stage}-before.png',frame.capture.png)
            state['pending'] = dict(mode='story', stage=str(stage), ap_before=before,
                                    cost=cost, run_dir=str(runner.run_dir))
            event_state.write_state(runner.config, state)
            runner.important('event_story_requested', f'Event Story {stage}: {cost} AP', stage=str(stage), ap_cost=cost)
            runner.tap(frame, target, f'Start event Story {stage}')
            finish_story(runner, stage)
            # Battle formation can span an AP regeneration tick; use the
            # balance persisted immediately before Mobilize for the receipt.
            before = event_state.read_state(runner.config)['pending']['ap_before']
            frame, row = story_row(runner, stage)
            if not row.cleared or frame.screen.ap is None or frame.screen.ap < before-cost:
                runner.fail('Event Story completion did not verify; inspect the saved receipt')
            state['stories'][str(stage)] = True
            state['pending'] = None
            event_state.write_state(runner.config, state)
            runner.journal.save_image(f'story-{stage}-cleared.png',frame.capture.png)
            runner.important('ap_spent', f'Cleared event Story {stage}: {cost} AP spent; {frame.screen.ap} AP left',
                             strategy='event_story',stage=str(stage),count=1,ap_spent=cost,
                             ap_before=before,ap_after=frame.screen.ap,
                             evidence=str(runner.run_dir / f'story-{stage}-receipt.png'))
        return_to_quests(runner)
        runner.important('event_stories_completed', f'All {count} event Stories are cleared')
        return None
    finally:
        runner.vision = previous
