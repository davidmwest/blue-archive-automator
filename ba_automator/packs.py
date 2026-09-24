"""Optional renewal of the three permanent daily-benefit packs."""
from .actions import record_action
from .locking import InstanceLock
from .packs_state import PACKS, enabled, next_reset, read_state, write_state
from .shop_runtime import ShopRunner


class PacksRunner(ShopRunner):
    task = 'packs'
    allows_billing = True

    def __init__(self, *args, allow_retry=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.allow_retry = allow_retry

    def settle_store(self):
        previous = self.wait('store', predicate=lambda s: set(s.cards) == set(PACKS))
        for _ in range(5):
            self.sleep(1)
            current = self.wait('store', predicate=lambda s: set(s.cards) == set(PACKS))
            if previous.screen.cards == current.screen.cards:
                return current
            previous = current
        self.fail('Pack availability did not settle; purchases stopped')

    def purchase(self, frame, key):
        card = frame.screen.cards[key]
        maximum = getattr(self.config, f'packs_{key}_max_cents')
        if key not in enabled(self.config):
            self.fail('This pack is not enabled for paid purchases')
        if card['state'] != 'available' or not 0 < card['cents'] <= maximum:
            self.fail('Pack availability or USD price does not match its spending limit')
        self.tap(frame, card['target'], f'Inspect {PACKS[key][0]}')
        confirmation = self.wait('purchase_confirm')
        if confirmation.screen.pack != key or confirmation.screen.cents != card['cents']:
            self.fail('In-game purchase details changed; purchase stopped')
        self.tap(confirmation, confirmation.screen.target, 'Open Google Play checkout')
        checkout = self.wait('checkout', timeout=60)
        if checkout.screen.pack != key or checkout.screen.cents != card['cents']:
            self.fail('Google Play product or price differs from the approved game product')
        # Persist BEFORE the charge. Any interruption after this point requires
        # observable delivery/active status before another purchase is possible.
        self.state['pending'] = {'pack': key, 'cents': card['cents'], 'time': self.wall_clock().isoformat()}
        write_state(self.config, self.state)
        title, cents = PACKS[key][0], card['cents']
        record_action(self.config, 'pack_purchase_requested', f'Buying {title}: USD {cents / 100:.2f}',
                      task='packs', pack=key, currency='USD', price_cents=cents)
        self.tap(checkout, checkout.screen.target, f'Buy {title} for USD {cents / 100:.2f}')
        outcome = self.wait({'delivered', 'backup_prompt'}, timeout=90)
        if outcome.screen.kind == 'backup_prompt':
            self.tap(outcome, outcome.screen.target, 'Dismiss optional backup payment setup')
            outcome = self.wait('delivered', timeout=60)
        self.journal.save_image(f'{key}-delivered.png', outcome.capture.png)
        record_action(self.config, 'pack_purchased', f'Bought {title}: USD {cents / 100:.2f}; delivered to mail',
                      task='packs', pack=key, currency='USD', price_cents=cents)
        self.state['pending'] = None
        write_state(self.config, self.state)
        self.tap(outcome, outcome.screen.target, 'Acknowledge confirmed delivery')
        frame = self.settle_store()
        if frame.screen.cards[key]['state'] not in {'mail', 'active'}:
            self.state['pending'] = {'pack': key, 'cents': cents, 'time': self.wall_clock().isoformat()}
            write_state(self.config, self.state)
            self.fail('Delivery notice appeared but pack ownership is unclear; automatic purchases blocked')
        return frame

    def run(self):
        self.state = read_state(self.config)
        if not self.allow_retry and (self.state['blocked_reason'] or self.state['pending']):
            self.fail('Paid pack job is disabled after a failure or unresolved charge; review it and explicitly retry the pack check')
        self.navigate('home', 'store', (139, 263))
        frame = self.wait('store')
        if set(frame.screen.cards) != set(PACKS):
            self.tap(frame, (640, 180), 'Select permanent Pyroxene packs')
        frame = self.settle_store()
        pending = self.state['pending']
        if pending:
            state = frame.screen.cards[pending['pack']]['state']
            if state not in {'active', 'mail'}:
                self.fail('An earlier charge is unresolved. Check Google Play purchase history; no second charge will be attempted')
            record_action(self.config, 'pack_purchase_reconciled',
                          f'{PACKS[pending["pack"]][0]} is now {state}; prior attempt will not be repeated', task='packs')
            self.state['pending'] = None
        write_state(self.config, self.state)
        for key in PACKS:
            frame = self.settle_store()
            card = frame.screen.cards[key]
            self.state['observed'][key] = {k: card[k] for k in ('state', 'days', 'cents')}
            write_state(self.config, self.state)
            if key in enabled(self.config) and card['state'] == 'available':
                frame = self.purchase(frame, key)
                card = frame.screen.cards[key]
                self.state['observed'][key] = {k: card[k] for k in ('state', 'days', 'cents')}
            elif key in enabled(self.config) and card['state'] == 'unknown':
                self.fail(f'{PACKS[key][0]} status is unreadable; purchases stopped')
            suffix = f' ({card["days"]} days remaining)' if card['days'] is not None else ''
            self.phase(f'{PACKS[key][0]}: {card["state"]}{suffix}')
        self.state['next_check_at'] = next_reset(self.wall_clock())
        self.state['blocked_reason'] = None
        write_state(self.config, self.state)
        self.tap(frame, (1009, 113), 'Close pack store')
        self.home()
        self.phase('Pack check complete; collect mail next')
        return self.finish()


def run_packs(config, device, startup, **kwargs):
    with InstanceLock(config):
        runner = PacksRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except Exception as exc:
            if hasattr(runner, 'state'):
                runner.state['blocked_reason'] = str(exc)
                write_state(config, runner.state)
            record_action(config, 'packs_blocked', str(exc), task='packs')
            runner.journal.record('finished', status='failed', detail=str(exc))
            raise
        finally:
            runner.journal.close()
