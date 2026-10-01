"""Serial, opt-in Treasure Hunt execution with a journaled intent per tile."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from hashlib import sha256
import os
from .actions import record_action
from .event_priority import available_event
from .event_inspection import EventVision, enter_event
from .locking import InstanceLock
from .loot_receipts import inspect_receipt
from .shop_runtime import ShopRunner
from .treasure_policy import TreasureShape, choose_greedy_cell
from .treasure_rounds import round_shapes
from . import treasure_state


class TreasureRunner(ShopRunner):
    task = 'event_treasure'

    def __init__(self, config, device, startup, **kwargs):
        from .treasure_vision import TreasureVision
        super().__init__(config, device, startup, **kwargs)
        self.startup = startup
        self.vision = TreasureVision(startup, EventVision(startup, self.vision))

    def budget(self):
        # One long visit may inspect several complete reward carousels. Tile and
        # round counts are independently bounded; no premium refills exist here.
        if self.clock() - self.started > 3600 or self.actions >= 600:
            self.fail('Treasure visit reached its time or input limit')

    def important(self, action, detail, **extra):
        return record_action(self.config, action, detail, task=self.task, **extra)

    def stable_board(self):
        previous = self.wait({'treasure_board', 'treasure_complete'})
        self.sleep(1)
        current = self.wait({'treasure_board', 'treasure_complete'})
        if previous.screen != current.screen:
            self.sleep(2)
            previous, current = current, self.wait({'treasure_board', 'treasure_complete'})
            if previous.screen != current.screen:
                self.fail('Treasure board is still changing; no currency spent')
        if current.screen.kind == 'treasure_complete':
            current = replace(current, screen=treasure_state.completed_board(self.saved, current.screen))
        return current

    def receipt_checkpoint(self, capture, evidence):
        """Save a validated carousel view without changing the receipt identity."""
        pending = self.saved.get('pending')
        if (not pending or pending.get('version') != 2 or pending.get('kind') != 'reveal'
                or Path(pending['evidence']) != Path(evidence)):
            return
        # Immutable files mean a crash cannot overwrite the image referenced by
        # the previous durable checkpoint. Store alongside the original receipt.
        digest = sha256(capture.png).hexdigest()[:20]
        target = Path(evidence).with_name(f'{Path(evidence).stem}-checkpoint-{digest}.png')
        if not target.exists():
            with target.open('wb') as stream:
                stream.write(capture.png)
                stream.flush()
                os.fsync(stream.fileno())
        pending['receipt_checkpoint'] = str(target)
        treasure_state.write_state(self.config, self.saved)

    def reconcile_pending(self):
        """Finish an observed old transaction; never repeat a spending input."""
        pending = self.saved['pending']
        if pending.get('version') != 2 or 'before' not in pending:
            self.fail('An earlier treasure reveal has an unresolved result; inspect its saved receipt before retrying')
        frame = self.capture()
        if frame.screen.kind in {'home', 'event_page'} and treasure_state.logged_reveal_can_navigate(pending):
            if frame.screen.kind == 'home':
                frame = enter_event(self, self.startup)
            self.tap(frame, (515, 663), 'Reopen Treasure Hunt to verify the logged result')
            frame = self.wait({'treasure_board', 'treasure_complete'})
        if pending['kind'] == 'refresh':
            if frame.screen.kind == 'treasure_refresh_confirm':
                # This exact free-refresh notice is only actionable with a
                # validated intent for an inventory containing no prizes.
                # A disappeared notice is never reopened during recovery.
                self.tap(frame, frame.screen.target, 'Confirm pending free treasure refresh')
                frame = self.wait('treasure_board', predicate=lambda s:
                                  s.round == pending['before']['round'] + 1)
            if frame.screen.kind != 'treasure_board':
                self.fail('Treasure refresh is unresolved; no refresh input will be repeated')
            frame = self.stable_board()
            treasure_state.refresh_result(pending, frame.screen)
            completed = set()
        else:
            evidence = Path(pending['evidence'])
            if frame.screen.kind == 'receipt':
                reference = (pending.get('receipt_ready') or pending.get('receipt_checkpoint')
                             or pending.get('receipt_seen'))
                if not reference or not Path(reference).is_file():
                    self.fail('Treasure receipt has no saved identity; inspect before retrying')
                from .loot_receipts import page, same_receipt_view
                png = Path(reference).read_bytes()
                vision = self.vision.startup
                # Parsing saved native-resolution evidence can take longer than
                # an input frame's lifetime. Do that work before recapturing,
                # then check freshness again after the identity comparison.
                expected = page(png, vision)
                for attempt in range(4):
                    frame = self.capture()
                    matches = (frame.screen.kind == 'receipt'
                               and frame.capture.is_fresh(self.clock())
                               and same_receipt_view(png, frame.capture.png, expected, vision=vision))
                    if matches and frame.capture.is_fresh(self.clock()):
                        break
                    self.journal.record('treasure_receipt_revalidation_failed', attempt=attempt + 1,
                                        reason='expired_capture' if matches else 'changed_or_expired_receipt')
                    if attempt == 3:
                        self.fail('Treasure receipt differs from the saved transaction or expired; no input sent')
                    # A particle or slow OCR may require another read. Never
                    # replace the saved identity with a rejected live frame.
                    self.sleep(.25)
                if not pending.get('receipt_logged'):
                    frame = inspect_receipt(self, frame, evidence)
                    self.receipt_checkpoint(frame.capture, evidence)
                    pending.update(receipt_logged=True, receipt_ready=pending['receipt_checkpoint'])
                    treasure_state.write_state(self.config, self.saved)
                self.tap(frame, (640, 630), 'Close recovered treasure receipt')
                self.sleep(3)
            elif frame.screen.kind not in {'treasure_board', 'treasure_complete'} or not pending.get('receipt_logged'):
                self.fail('Treasure result lacks a logged receipt; no spending will be repeated')
            frame = self.stable_board()
            if pending['completed'] != self.saved['completed']:
                self.fail('Saved completed treasure cells changed during the pending reveal')
            completed = treasure_state.reveal_result(pending, frame.screen,
                        {tuple(c) for c in self.saved['completed']})
        if not any(frame.screen.inventory):
            self.saved['finished_board'] = treasure_state.board_snapshot(frame.screen)
        self.saved.update(round=frame.screen.round,
                          completed=[list(c) for c in sorted(completed)], pending=None)
        treasure_state.write_state(self.config, self.saved)
        self.important('treasure_reconciled', 'Recovered the prior treasure result without repeating spending',
                       round=frame.screen.round, currency_remaining=frame.screen.currency)
        return frame

    def run(self):
        self.saved = treasure_state.read_state(self.config)
        pending = self.saved['pending']
        if not self.config.ap_event_treasure_enabled and not pending:
            self.phase('Automatic event treasure is off')
            return self.finish()
        profile = available_event(self.wall_clock())
        if profile is None:
            if pending:
                self.fail('Pending treasure belongs to an inactive event; inspect its saved receipt')
            self.phase('No supported Treasure Hunt is active')
            return self.finish()
        if pending:
            if self.saved['event_id'] != profile['id']:
                self.fail('Pending treasure is unresolved and belongs to another event; inspect its saved receipt')
            frame = self.reconcile_pending()
            self.tap(frame, (1237, 24), 'Return home after treasure recovery')
            self.home()
            return self.finish()
        frame = self.capture()
        if frame.screen.kind not in {'treasure_board', 'treasure_complete'}:
            frame = enter_event(self, self.startup)
            self.tap(frame, (515, 663), 'Open verified event Treasure Hunt')
        frame = self.stable_board()
        if (self.saved['event_id'], self.saved['round']) != (profile['id'], frame.screen.round):
            self.saved.update(event_id=profile['id'], round=frame.screen.round, completed=[])
        completed = {tuple(c) for c in self.saved['completed']}
        if not completed <= frame.screen.hits:
            self.fail('Saved completed treasure cells disagree with the visible board')
        # Inventory counts describe whole prizes, while the board still displays
        # their dimmed footprints. Without those saved cells the planner would
        # mistake already collected prizes for unfinished ones.
        if frame.screen.round <= profile['target_round'] and any(frame.screen.inventory):
            expected_completed = sum((shape.count - remaining) * shape.height * shape.width
                                     for shape, remaining in
                                     zip(round_shapes(frame.screen.round), frame.screen.inventory))
            if len(completed) != expected_completed:
                self.fail('Treasure inventory requires completed prize footprints missing from saved state; '
                          'reconcile the board before spending more event currency')
        spent = reveals = 0
        for _ in range(138):
            board = frame.screen
            if board.round > profile['target_round']:
                break
            # Reserve time and inputs for a complete 900-second/160-input
            # receipt plus board verification. Yield only between transactions,
            # so a long visit doesn't manufacture an unresolved purchase.
            if self.clock() - self.started >= 2400 or self.actions >= 420:
                self.important('treasure_deferred', 'Treasure visit yielded between tiles; saved currency remains for the next visit',
                               round=board.round, currency_remaining=board.currency)
                break
            if board.selected != 0:
                self.fail('Treasure board has a preselected tile; clear the selection before retrying')
            if not any(board.inventory):
                self.important('treasure_round_completed', f'Treasure round {board.round} completed', round=board.round)
                # Stop at the configured goal without discarding unclaimed prizes.
                # The next round is observed only after a verified free refresh.
                self.saved['pending'] = dict(version=2, kind='refresh', round=board.round,
                                             currency=board.currency, before=treasure_state.board_snapshot(board),
                                             run_dir=str(self.run_dir))
                treasure_state.write_state(self.config, self.saved)
                self.tap(frame, (1148, 155), 'Refresh fully completed treasure board')
                frame = self.wait({'treasure_board', 'treasure_refresh_confirm'},
                                  predicate=lambda s: s.kind == 'treasure_refresh_confirm'
                                  or s.round == board.round + 1)
                if frame.screen.kind == 'treasure_refresh_confirm':
                    self.tap(frame, frame.screen.target, 'Confirm free refresh of completed board')
                    self.wait('treasure_board', predicate=lambda s: s.round == board.round + 1)
                frame = self.stable_board()
                treasure_state.refresh_result(self.saved['pending'], frame.screen)
                completed = set()
                self.saved.update(round=frame.screen.round, completed=[], pending=None)
                treasure_state.write_state(self.config, self.saved)
                continue
            if board.currency < profile['board']['cell_cost']:
                break
            choice = choose_greedy_cell(5, 9, tuple(
                TreasureShape(shape.height, shape.width, count) for shape, count in
                zip(round_shapes(board.round), board.inventory)),
                empty=board.empty, hits=board.hits - completed, completed=completed)
            if choice is None or choice.cell not in board.closed:
                self.fail('No verified unopened treasure cell could be selected')
            cell = choice.cell
            self.phase(f'Treasure round {board.round}: reveal row {cell[0]+1}, column {cell[1]+1}; {board.currency} currency')
            self.journal.record('treasure_choice', cell=cell, score=asdict(choice.scores[cell]), candidates=choice.candidate_count)
            self.tap(frame, (641+69*cell[1], 225+69*cell[0]), 'Select one greedy treasure tile')
            selected = self.wait('treasure_board', predicate=lambda s: s.selected == 1)
            if (any(getattr(selected.screen, key) != getattr(board, key)
                    for key in ('currency', 'round', 'remaining', 'closed', 'hits', 'empty', 'inventory'))
                    or selected.screen.cost != profile['board']['cell_cost']
                    or selected.screen.selected_cells != frozenset({cell})):
                self.fail('Treasure selection cost or balance changed; no confirmation sent')
            self.tap(selected, (920, 608), 'Review one treasure reveal')
            confirmation = self.wait('treasure_confirm')
            filename = f'treasure-reward-{reveals:03d}.png'
            self.saved['pending'] = dict(version=2, kind='reveal', before=treasure_state.board_snapshot(board),
                                         evidence=str(self.run_dir / filename), receipt_logged=False,
                                         completed=[list(c) for c in sorted(completed)],
                                         round=board.round, cell=list(cell), currency=board.currency,
                                         remaining=board.remaining, cost=profile['board']['cell_cost'],
                                         run_dir=str(self.run_dir))
            treasure_state.write_state(self.config, self.saved)
            self.tap(confirmation, confirmation.screen.target, 'Spend event currency on one verified tile')
            # The board appears briefly before rewards. Never interpret it as
            # completion until the delayed reward receipt has been handled.
            receipt = self.wait('receipt', timeout=45)
            self.journal.save_image(filename, receipt.capture.png)
            self.saved['pending']['receipt_seen'] = str(self.run_dir / filename)
            treasure_state.write_state(self.config, self.saved)
            receipt = inspect_receipt(self, receipt, self.run_dir / filename)
            ready = f'treasure-reward-{reveals:03d}-ready.png'
            self.journal.save_image(ready, receipt.capture.png)
            self.saved['pending'].update(receipt_logged=True, receipt_ready=str(self.run_dir / ready))
            treasure_state.write_state(self.config, self.saved)
            self.tap(receipt, (640, 630), 'Close logged treasure rewards')
            self.sleep(3)
            frame = self.stable_board()
            after = frame.screen
            completed = treasure_state.reveal_result(self.saved['pending'], after, completed)
            if not any(after.inventory):
                self.saved['finished_board'] = treasure_state.board_snapshot(after)
            self.saved.update(completed=[list(c) for c in sorted(completed)], pending=None)
            treasure_state.write_state(self.config, self.saved)
            spent += profile['board']['cell_cost']; reveals += 1
            self.important('treasure_revealed', f'Revealed treasure tile ({cell[0]+1}, {cell[1]+1}); spent 200 event currency',
                           round=board.round, cell=cell, currency_spent=200, currency_remaining=after.currency,
                           evidence=str(self.run_dir / filename))
        self.tap(frame, (1237, 24), 'Return home after Treasure Hunt')
        self.home()
        self.phase(f'Treasure checked: {reveals} tiles opened, {spent} event currency spent')
        return self.finish()


def run_event_treasure(config, device, startup, **kwargs):
    # The old idle cleanup could close the game after a logged receipt. Startup
    # owns its own lock and rechecks the durable guard before any input. Release
    # this inspection lock first; the runner re-reads state under a new lock.
    relaunch = False
    with InstanceLock(config):
        saved = treasure_state.read_state(config)
        profile = available_event(kwargs.get('wall_clock', lambda: datetime.now(timezone.utc))())
        if (profile and saved['event_id'] == profile['id']
                and treasure_state.logged_reveal_can_navigate(saved['pending'])):
            device.connect(); device.verify_package()
            relaunch = device.foreground_package() in {
                'com.uncube.launcher3', 'com.android.launcher3', 'com.bluestacks.launcher'}
    if relaunch:
        from .restart import run_restart
        run_restart(config, device, startup, recover_logged_treasure=True,
                    **{key: kwargs[key] for key in ('monotonic', 'sleep') if key in kwargs})
    with InstanceLock(config):
        runner = TreasureRunner(config, device, startup, **kwargs)
        try:
            device.connect(); device.verify_package()
            return runner.run()
        except Exception as exc:
            runner.journal.record('finished', status='failed', detail=str(exc))
            raise
        finally:
            runner.journal.close()
