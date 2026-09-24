"""Collect both mailbox tabs and log observed rewards, especially Pyroxenes."""
from .actions import record_action
from .locking import InstanceLock
from .shop_runtime import ShopRunner


class MailRunner(ShopRunner):
    task = 'mail'

    def log_receipt(self, frame, before):
        items = list(frame.screen.items)
        after = frame.screen.balances
        gains = {key: after[key] - value for key, value in before.items()
                 if key in after and after[key] >= value}
        label = ', '.join(f'+{item["quantity"]:,} {item["name"]}' for item in items)
        pyro = gains.get('pyroxenes')
        if pyro is not None and pyro > 0 and not any('pyroxene' in i['name'].lower() for i in items):
            label = f'{label}; ' if label else ''
            label += f'+{pyro:,} Pyroxenes (balance verified)'
        receipt = f'receipt-{self.actions:03d}.png'
        self.journal.save_image(receipt, frame.capture.png)
        detail = f'Received {label}' if label else 'Mail claimed; item labels unreadable. Receipt saved locally for review'
        record_action(self.config, 'mail_received', detail, task='mail', items=items,
                      pyroxenes=pyro, balance_gains=gains, evidence=str(self.run_dir / receipt))
        self.phase(detail)

    def run(self):
        frame = self.wait('home')
        if not frame.screen.red_dot:
            self.sleep(1)
            frame = self.wait('home')
            if not frame.screen.red_dot:
                self.phase('No mailbox notification; mail skipped')
                return self.finish()
        # Entering the mailbox can initially show a black/loading frame.
        for _ in range(3):
            frame = self.wait({'home', 'mail', 'mail_empty'})
            if frame.screen.kind != 'home':
                break
            self.tap(frame, (1156, 35), 'Open mailbox')
            self.sleep(2)
        frame = self.wait({'mail', 'mail_empty'})
        return self.collect_from_mail(frame)

    def collect_from_mail(self, frame):
        # Purchased packs must be claimed to start their duration. Then revisit
        # ordinary mail in case activating a product delivered another gift.
        for tab, target in (('product', (105, 218)), ('unclaimed', (105, 138))):
            if frame.screen.tab != tab:
                self.tap(frame, target, f'Open {tab} mail')
            frame = self.wait({'mail', 'mail_empty'}, predicate=lambda s: s.tab == tab)
            while frame.screen.kind == 'mail':
                before = frame.screen.balances
                record_action(self.config, 'mail_claim_requested', f'Claiming one item from {tab} mail', task='mail')
                self.tap(frame, frame.screen.target, f'Claim {tab} mail')
                result = self.wait({'claim_confirm', 'receipt'})
                if result.screen.kind == 'claim_confirm':
                    self.tap(result, result.screen.target, 'Confirm mailbox claim')
                    result = self.wait('receipt')
                self.log_receipt(result, before)
                self.tap(result, result.screen.target, 'Close reward receipt')
                frame = self.wait({'mail', 'mail_empty'}, predicate=lambda s: s.tab == tab)
            # Require a second empty observation before declaring this tab done.
            self.sleep(1)
            frame = self.wait('mail_empty', predicate=lambda s: s.tab == tab)
        self.tap(frame, (1237, 23), 'Return home from mail')
        self.home()
        self.phase('Product and ordinary mail collected')
        return self.finish()


def run_mail(config, device, startup, **kwargs):
    with InstanceLock(config):
        runner = MailRunner(config, device, startup, **kwargs)
        try:
            device.connect()
            device.verify_package()
            return runner.run()
        except Exception as exc:
            runner.journal.record('finished', status='failed', detail=str(exc))
            raise
        finally:
            runner.journal.close()
