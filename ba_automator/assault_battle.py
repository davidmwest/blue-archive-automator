"""Bounded Auto battle driver with evidence preserved before result dismissal."""
from dataclasses import asdict, replace

from . import assault_state
from .assault_policy import MockResult, team_fingerprint, validate_team
from .club import game_day


# Observed seasonal staging battle, September 25, 2026. A timer ceiling is
# specific to the boss and difficulty, never a default for an unfamiliar raid.
BATTLE_DURATION_SECONDS = {("drumbarka", "hardcore"): 270.0}
BATTLE_TIMEOUT_SECONDS = 1800
BOSS_MISMATCH_LIMIT = 3
BOSS_AMBIGUITY_SECONDS = 15
CONFIRMATION_SETTLEMENT_SECONDS = 30


def observed_team_fingerprint(team):
    """Compare formation metadata while retaining lender provenance in the run.

    The formation nameplates expose the selected student's identity and stats,
    but not the exact lender. The caller must establish and persist that lender
    while selecting an assistant, before passing its qualified team here.
    """
    return team_fingerprint(tuple(replace(member, assistant=False, assistant_id=None)
                                  for member in validate_team(team)))


class AssaultBattleMixin:
    def fight(self, formation, team, *, mock=True):
        """Mobilize a verified team and return ``(MockResult, result_frame)``.

        The result screen is left open. Unknown victory or damage observations
        never become permission for a paid entry. Ticket entry and reconciliation
        belong to the outer raid runner, not this combat-only driver.
        """
        team = validate_team(team)
        if type(mock) is not bool:
            self.fail("Total Assault battle mode must be explicit")
        if self.context is None:
            self.fail("Total Assault battle has no verified event context")
        if not formation.capture.is_fresh(self.clock()):
            formation = self.wait("formation", predicate=lambda screen: len(screen.team) == 6)
        if (formation.screen.kind != "formation" or formation.screen.target is None
                or observed_team_fingerprint(formation.screen.team) != observed_team_fingerprint(team)):
            self.fail("Total Assault formation changed before Mobilize")
        self.journal.record("total_assault_battle_start", mock=mock,
                            context=asdict(self.context), fingerprint=team_fingerprint(team),
                            team=[asdict(member) for member in team])
        prefix = "mock" if mock else "real"
        self.journal.save_image(f"{prefix}-formation.png", formation.capture.png)
        self.tap(formation, formation.screen.target, f"Mobilize {'mock' if mock else 'real'} Total Assault team")
        return self.observe_battle(team, mock=mock)

    def confirm_entry_notice(self, frame, team, *, mock):
        """Confirm only the exact one-ticket entry already persisted by this run."""
        if mock is not False:
            self.fail("A free Total Assault mock unexpectedly asked to use a ticket")
        team = validate_team(team)
        screen = frame.screen
        if screen.kind != "entry_confirm" or screen.count != 1 or screen.confirm_target is None:
            self.fail("Total Assault ticket confirmation is not fully recognized")
        pending = self._matching_entry_intent(team, "ticket confirmation")
        if (screen.difficulty != self.context.difficulty or screen.tickets != pending["tickets_before"]
                or screen.after_tickets != screen.tickets - 1):
            self.fail("Total Assault ticket confirmation changed difficulty or ticket budget")
        confirmed = getattr(self, "_assault_ticket_confirmed_intents", set())
        if pending["id"] in confirmed:
            self.fail("Total Assault repeated its ticket confirmation; inspect before retrying")
        self.journal.save_image("real-ticket-confirmation.png", frame.capture.png)
        self.journal.record("total_assault_ticket_confirmation", intent_id=pending["id"],
                            context=asdict(self.context), team_fingerprint=pending["team"])
        self._assault_ticket_confirmed_intents = confirmed | {pending["id"]}
        self._remember_confirmation("entry_confirm", pending["id"])
        self.tap(frame, screen.confirm_target, "Confirm the persisted one-ticket Total Assault entry")

    def _matching_entry_intent(self, team, action):
        """Every paid confirmation stays bound to this exact qualified entry."""
        state = assault_state.read_state(self.config)
        pending = state["pending"]
        if (state["blocked_reason"] or self.context is None or pending is None
                or pending["kind"] != "entry" or pending["count"] != 1
                or pending["context"] != asdict(self.context)
                or pending["team"] != team_fingerprint(team)
                or pending["run_id"] != self.run_dir.name
                or game_day(self.wall_clock()) != self.context.day_key):
            self.fail(f"Total Assault {action} has no matching persisted entry intent")
        return pending

    def confirm_assistant_notice(self, frame, team, *, mock):
        """Authorize the observed 40,000-credit fee for one qualified assistant."""
        if mock is not False:
            self.fail("A free Total Assault mock unexpectedly asked to pay an assistant fee")
        team = validate_team(team)
        assistants = [member for member in team if member.assistant]
        screen = frame.screen
        if (screen.kind != "assistant_confirm" or screen.credit_fee != 40000
                or screen.confirm_target is None or len(assistants) != 1
                or not assistants[0].assistant_id or assistants[0].role != "striker"
                or assistants[0].level != screen.assistant_level
                or assistants[0].stars != screen.assistant_stars):
            self.fail("Total Assault assistant fee or qualified assistant was not fully recognized")
        pending = self._matching_entry_intent(team, "assistant confirmation")
        confirmed = getattr(self, "_assault_assistant_confirmed_intents", set())
        if pending["id"] in confirmed:
            self.fail("Total Assault repeated its assistant fee confirmation; inspect before retrying")
        self.journal.save_image("real-assistant-confirmation.png", frame.capture.png)
        self.journal.record("total_assault_assistant_fee_intent", intent_id=pending["id"],
                            credit_cost=screen.credit_fee, assistant=asdict(assistants[0]),
                            team_fingerprint=pending["team"])
        # Latch before sending the input: uncertain input delivery must never be
        # retried from this same live runner, even if observation is resumed.
        self._assault_assistant_confirmed_intents = confirmed | {pending["id"]}
        self._remember_confirmation("assistant_confirm", pending["id"])
        self.tap(frame, screen.confirm_target, "Confirm the qualified assistant for 40,000 credits")
        self.important("total_assault_assistant_fee", "Confirmed the assistant's 40,000-credit fee",
                       credit_cost=screen.credit_fee, intent_id=pending["id"],
                       assistant=assistants[0].student_id, status="confirmation_sent")

    def _remember_confirmation(self, kind, intent_id):
        if not hasattr(self, "_assault_confirmation_times"):
            self._assault_confirmation_times = {}
        self._assault_confirmation_times[(kind, intent_id)] = self.clock()

    def _settling_confirmation(self, frame, team, *, mock):
        """Wait for acknowledged input to settle, never send its Confirm twice."""
        if mock is not False:
            return False  # The dedicated handler reports the unexpected cost.
        kind = frame.screen.kind
        pending = self._matching_entry_intent(team, "confirmation settlement")
        attribute = ("_assault_assistant_confirmed_intents" if kind == "assistant_confirm"
                     else "_assault_ticket_confirmed_intents")
        if pending["id"] not in getattr(self, attribute, set()):
            return False
        if not hasattr(self, "_assault_confirmation_times"):
            self._assault_confirmation_times = {}
        since = self._assault_confirmation_times.setdefault((kind, pending["id"]), self.clock())
        if self.clock() - since >= CONFIRMATION_SETTLEMENT_SECONDS:
            label = "assistant fee" if kind == "assistant_confirm" else "ticket"
            self.fail(f"Total Assault repeated its {label} confirmation for 30 seconds; inspect before retrying")
        self.sleep(1)
        return True

    def observe_battle(self, team, *, mock=True):
        """Observe an already mobilized battle; this method never taps Mobilize.

        A debugger may resume observation in the same locked run after handling
        a newly captured notice. The exact team/context remain required, and any
        ticket confirmation independently checks this run's persisted intent.
        """
        team = validate_team(team)
        if type(mock) is not bool or self.context is None:
            self.fail("Total Assault observation requires an explicit mode and event context")
        prefix = "mock" if mock else "real"
        self.journal.record("total_assault_combat_observation", mock=mock,
                            context=asdict(self.context), fingerprint=team_fingerprint(team))
        end = self.clock() + BATTLE_TIMEOUT_SECONDS
        maximum_clock = None
        boss_zero = False
        known_survivors = None
        auto_requested = False
        auto_verified = False
        previous = None
        last_unknown_log = self.clock()
        boss_ambiguous_since = None
        unexpected_boss = None
        unexpected_boss_count = 0
        while self.clock() < end:
            frame = self.capture()
            screen = frame.screen
            if screen.kind in {"assistant_confirm", "entry_confirm"}:
                if self._settling_confirmation(frame, team, mock=mock):
                    continue
                if screen.kind == "assistant_confirm":
                    self.confirm_assistant_notice(frame, team, mock=mock)
                else:
                    self.confirm_entry_notice(frame, team, mock=mock)
                continue
            if screen.kind == "result":
                self.journal.save_image(f"{prefix}-result.png", frame.capture.png)
                if boss_ambiguous_since is not None:
                    self.fail("Total Assault result arrived before the battle boss could be re-verified")
                break
            if screen.kind == "battle":
                if screen.boss != self.context.boss:
                    if boss_ambiguous_since is None:
                        boss_ambiguous_since = self.clock()
                    unexpected_boss_count = unexpected_boss_count + 1 if screen.boss == unexpected_boss else 1
                    unexpected_boss = screen.boss
                    self.journal.record("total_assault_boss_ambiguous", expected=self.context.boss,
                                        observed=screen.boss, consecutive=unexpected_boss_count)
                    if (unexpected_boss_count >= BOSS_MISMATCH_LIMIT
                            or self.clock() - boss_ambiguous_since >= BOSS_AMBIGUITY_SECONDS):
                        self.fail("Total Assault battle boss changed or remained unreadable")
                    # Skill effects can corrupt a single name read. Do not use
                    # that frame to tap Auto or establish combat/result proof.
                    self.sleep(1.5)
                    continue
                boss_ambiguous_since = None
                unexpected_boss = None
                unexpected_boss_count = 0
                if mock is False and screen.mock is True:
                    self.fail("Expected real Total Assault battle but observed Mock Battle")
                clock = screen.remaining_seconds
                if clock is not None:
                    maximum_clock = max(maximum_clock or 0, clock)
                if screen.boss_hp == 0 and screen.boss_max_hp:
                    boss_zero = True
                    self.journal.save_image(f"{prefix}-boss-defeated.png", frame.capture.png)
                if screen.surviving_striker_ids is not None:
                    known_survivors = screen.surviving_striker_ids
                signature = (screen.boss_hp, clock, screen.auto_on)
                if signature != previous:
                    self.journal.record("total_assault_combat", mock=mock, boss=screen.boss,
                                        hp=screen.boss_hp, max_hp=screen.boss_max_hp,
                                        remaining_seconds=clock, auto_on=screen.auto_on)
                    previous = signature
                if screen.auto_on is True:
                    auto_verified = True
                elif screen.auto_on is False and auto_verified:
                    self.fail("Total Assault Auto unexpectedly turned off")
                elif screen.auto_on is False and not auto_requested:
                    if screen.auto_target is None:
                        self.fail("Total Assault Auto control has no verified target")
                    self.tap(frame, screen.auto_target, "Enable Auto battle")
                    auto_requested = True
                    continue
            elif screen.kind not in {"unknown", "formation"}:
                self.fail(f"Unexpected Total Assault battle screen: {screen.kind}")
            elif boss_ambiguous_since is not None and self.clock() - boss_ambiguous_since >= BOSS_AMBIGUITY_SECONDS:
                self.fail("Total Assault battle boss remained unreadable after an ambiguous observation")
            elif self.clock() - last_unknown_log >= 30:
                self.journal.record("total_assault_combat_wait", mock=mock, screen=screen.kind)
                last_unknown_log = self.clock()
            self.sleep(1.5)
        else:
            self.fail("Total Assault battle exceeded its 30-minute startup and combat limit")

        result_screen = frame.screen
        elapsed = result_screen.elapsed_seconds
        duration = BATTLE_DURATION_SECONDS.get((self.context.boss.lower(), self.context.difficulty))
        source = "observed_boss_duration" if duration is not None else "observed_clock_lower_bound"
        if duration is None:
            duration = maximum_clock
        remaining = max(0.0, duration - elapsed) if duration is not None and elapsed is not None else None
        # Zero HP is useful trace evidence, but a transition or boss phase can
        # briefly show zero. Only the recognized victory result qualifies a win.
        won = result_screen.won
        if type(won) is not bool:
            self.fail("Total Assault result outcome was not verified; leave any pending entry unresolved")
        if result_screen.surviving_striker_ids is not None:
            known_survivors = result_screen.surviving_striker_ids
        damage, frame = self.battle_damage(frame, team, prefix=prefix)
        result = MockResult(won, remaining, known_survivors, damage)
        self.journal.record("total_assault_battle_result", mock=mock, result=asdict(result),
                            elapsed_seconds=elapsed, duration_seconds=duration,
                            duration_source=source, boss_zero_observed=boss_zero,
                            auto_verified=auto_verified)
        self.important("total_assault_mock_result" if mock else "total_assault_real_result",
                       f"{'Mock' if mock else 'Real'} Total Assault result recorded",
                       result=asdict(result), elapsed_seconds=elapsed,
                       victory_verified=won, auto_verified=auto_verified)
        if not auto_verified:
            self.fail("Total Assault Auto was not visually verified enabled; inspect the saved battle before continuing")
        return result, frame

    def battle_damage(self, frame, team, *, prefix):
        """Read the complete report, close it, and re-observe the result screen."""
        if not frame.capture.is_fresh(self.clock()):
            frame = self.wait("result")
        if frame.screen.damage_target is None:
            self.fail("Total Assault result has no verified Damage Report control")
        self.tap(frame, frame.screen.damage_target, "Read Total Assault damage by student")
        expected = {member.student_id for member in team}
        # Damage bars and their figures animate after the report opens. Observe
        # until all six names and amounts are readable; never reopen or dismiss an
        # incomplete report merely because its first frame was readable.
        complete = lambda screen: (set(screen.damage_by_student) == expected and all(
            type(value) is int and value >= 0 for value in screen.damage_by_student.values()))
        report = self.wait("damage", timeout=10, predicate=complete)
        self.journal.save_image(f"{prefix}-damage.png", report.capture.png)
        damage = report.screen.damage_by_student
        if (set(damage) != expected or any(type(value) is not int or value < 0 for value in damage.values())):
            self.fail("Total Assault Damage Report did not identify every selected student")
        self.journal.record("total_assault_damage", mock=prefix == "mock", damage=damage)
        self.tap(report, report.screen.target, "Close the verified Damage Report")
        return dict(damage), self.wait("result")
