"""Bounded assistant fallback; selection never authorizes a paid entry."""
from dataclasses import asdict, replace

from .assault_assistant_vision import (
    FILTER_TARGETS, read_assistant_filter, read_assistant_page, verify_assistant_preview,
    verify_owned_quick, verify_retained_assistant,
)
from .assault_policy import choose_assistant, least_damage_striker, replace_striker, team_fingerprint
from .shop_vision import text_in
from .vision import decode_frame


def best_at_observed_ceiling(page, damage_type, remaining_team):
    """Prove global optimality from the list's observed descending level order.

    Five is the maximum base rarity. At the top of a verified level-descending
    list, a five-star candidate matching its first readable level cannot be
    beaten by a later candidate. No hardcoded current level cap is needed.
    """
    if not page.at_top or not page.level_descending or not page.cards:
        return None
    first = min(page.cards, key=lambda card: (card.bounds[1], card.bounds[0]))
    if any((box[1], box[0]) < (first.bounds[1], first.bounds[0]) for box in page.incomplete):
        return None
    if first.bounds[1] > 250:
        return None
    cap = first.member.level
    if any(card.member.level > cap for card in page.cards):
        return None
    candidates = [card.member for card in page.cards if card.member.stars == 5 and card.member.level == cap]
    best = choose_assistant(candidates, damage_type, remaining_team)
    return next((card for card in page.cards if card.member == best), None)


def empty_assistant_slot(words, slot):
    """Require one explicitly empty slot, in the mock-tested striker position."""
    empty = [word for word in words if word.normalized == 'empty'
             and 23 <= word.center[0] < 563 and 560 <= word.center[1] < 635]
    return (len(empty) == 1 and empty[0].confidence >= .9
            and 23 + 90*slot <= empty[0].center[0] < 112 + 90*slot)


class AssaultAssistantMixin:
    """Requires the serial runner's capture, wait, tap, swipe and journal APIs."""

    def verify_owned_quick(self, quick, *, real=False):
        if not verify_owned_quick(decode_frame(quick.capture.png), quick.screen.words, startup=self.vision.startup):
            self.fail('Real formation contains an assistant or unreadable slot; the ticket intent is held' if real else
                      'Auto formation contains an assistant or unreadable slot; no mock proof was established')
        self.assistant = None
        return quick

    def verify_real_owned(self, formation, team):
        """Reconfirm owned provenance even when a borrowed copy has equal stats."""
        if (any(member.assistant for member in team) or len(formation.screen.team) != 6
                or team_fingerprint(formation.screen.team) != team_fingerprint(team)):
            self.fail('Real formation differs from the mock; the ticket intent is held')
        self.tap(formation, formation.screen.quick_target, 'Verify the real formation uses owned students')
        quick = self.verify_owned_quick(self.wait('quick'), real=True)
        self.tap(quick, quick.screen.confirm_target, 'Confirm the verified owned formation')
        formation = self.wait('formation', predicate=lambda screen: len(screen.team) == 6)
        if team_fingerprint(formation.screen.team) != team_fingerprint(team):
            self.fail('The real owned formation changed; the ticket intent is held')
        return formation

    def verify_real_assistant(self, formation, team):
        """Restore a cleared borrowed slot, then verify the exact tested offering.

        Entering for real can clear the mock's borrowed slot. Only an explicitly
        empty expected slot permits selection; an occupied slot is never edited.
        A matching student from another lender is not the qualified offering.
        """
        borrowed = [member for member in team if member.assistant]
        if len(borrowed) != 1:
            self.fail('Real assistant provenance is missing or ambiguous; the ticket intent is held')
        expected = borrowed[0]
        self.tap(formation, formation.screen.quick_target, 'Verify the real formation assistant offering')
        quick = self.wait('quick')
        restore = empty_assistant_slot(quick.screen.words, expected.slot)
        if (not restore and any(word.normalized == 'empty' for word in quick.screen.words)):
            self.fail('The real formation has an unexpected empty slot; the ticket intent is held')
        if not restore:
            expected_owned = tuple(replace(member, assistant=False, assistant_id=None) for member in team)
            if (len(formation.screen.team) != 6
                    or team_fingerprint(formation.screen.team) != team_fingerprint(expected_owned)):
                self.fail('The occupied real formation differs from the mock; the ticket intent is held')
        self.tap(quick, quick.screen.assistant_target, 'Inspect the retained assistant offering')
        frame = self.wait_assistant()
        if restore:
            if not frame.screen.available:
                self.fail('The mock-tested assistant is no longer available; the ticket intent is held')
            frame = self.filter_assistants(frame, expected.damage_type)
        # Inspect the current viewport first: reopening can retain its scroll.
        selected = next((card for card in frame.screen.cards
                         if (restore or card.selected) and card.member.assistant_id == expected.assistant_id), None)
        if selected is None:
            if any(card.selected for card in frame.screen.cards):
                self.fail('A different assistant is selected; the ticket intent is held')
            frame = self._assistant_start(frame)
            previous_scroll = None
            unchanged = 0
            for _ in range(240):
                selected = next((card for card in frame.screen.cards
                                 if (restore or card.selected) and card.member.assistant_id == expected.assistant_id), None)
                if selected or frame.screen.at_bottom:
                    break
                if any(card.selected for card in frame.screen.cards):
                    self.fail('A different assistant is selected; the ticket intent is held')
                if frame.screen.scrollbar is None:
                    self.fail('Assistant scroll position is unreadable; the ticket intent is held')
                # The thumb is the progress signal. OCR or cycling star art may
                # change while the list itself remains stationary.
                unchanged = unchanged + 1 if frame.screen.scrollbar == previous_scroll else 0
                if unchanged >= 3:
                    self.fail('Assistant scrolling stalled; the ticket intent is held')
                previous_scroll = frame.screen.scrollbar
                self.swipe(frame, (1050, 418), (1050, 378), 'Locate the retained assistant offering')
                frame = self.wait_assistant()
        if selected is None or replace(selected.member, slot=expected.slot) != expected:
            self.fail('The exact mock-tested assistant cannot be verified; the ticket intent is held')
        if restore:
            if selected.selected or not empty_assistant_slot(frame.screen.words, expected.slot):
                self.fail('The assistant slot changed before restoration; the ticket intent is held')
            self.tap(frame, selected.target, 'Restore the exact mock-tested assistant offering')
            selected_member = selected.member
            frame = self.wait_assistant(predicate=lambda page: any(
                card.selected and card.member == selected_member for card in page.cards))
            selected = next(card for card in frame.screen.cards
                            if card.selected and card.member == selected_member)
        image = decode_frame(frame.capture.png)
        verified = (verify_assistant_preview(image, frame.screen.words, selected, expected.slot) if restore
                    else verify_retained_assistant(image, selected, expected.slot))
        if not verified:
            self.fail('The exact mock-tested assistant cannot be verified; the ticket intent is held')
        self.tap(frame, frame.screen.confirm_target, 'Keep the verified assistant formation')
        formation = self.wait('formation', predicate=lambda screen: len(screen.team) == 6)
        expected_owned = tuple(replace(member, assistant=False, assistant_id=None) for member in team)
        if team_fingerprint(formation.screen.team) != team_fingerprint(expected_owned):
            self.fail('The real assistant formation changed; the ticket intent is held')
        self.assistant = expected
        return formation

    def assistant_frame(self, frame=None):
        frame = frame or self.capture()
        image = decode_frame(frame.capture.png)
        words = frame.screen.words
        screen = read_assistant_filter(image, words)
        if screen.kind == 'unknown':
            screen = read_assistant_page(image, words, startup=self.vision.startup)
        return replace(frame, screen=screen)

    def wait_assistant(self, kind='assistant', *, predicate=lambda screen: True, timeout=40):
        end = self.clock() + timeout
        while self.clock() < end:
            frame = self.assistant_frame()
            if (frame.screen.kind == kind and predicate(frame.screen)
                    and frame.capture.deadline - self.clock() >= 1):
                return frame
            self.sleep(.7)
        self.fail('Assistant selection is unreadable; choose a lower difficulty or complete Total Assault manually')

    def filter_assistants(self, frame, damage_type):
        self.tap(frame, frame.screen.filter_target, 'Open assistant attack filter')
        filters = self.wait_assistant('assistant_filter')
        self.tap(filters, filters.screen.reset_target, 'Reset assistant display filters')
        filters = self.wait_assistant('assistant_filter', predicate=lambda screen: screen.selected == set(FILTER_TARGETS))
        self.tap(filters, FILTER_TARGETS[damage_type], f'Filter assistant strikers to {damage_type}')
        filters = self.wait_assistant('assistant_filter', predicate=lambda screen: screen.selected == {damage_type})
        self.tap(filters, filters.screen.confirm_target, 'Apply the verified assistant attack filter')
        return self.wait_assistant()

    def _assistant_start(self, frame):
        previous_scroll, unchanged = None, 0
        for _ in range(60):
            if frame.screen.at_top:
                return frame
            if frame.screen.scrollbar is None:
                self.fail('Assistant scroll position is unreadable; lower the difficulty or play manually')
            unchanged = unchanged + 1 if frame.screen.scrollbar == previous_scroll else 0
            if unchanged >= 3:
                self.fail('Assistant scrolling stalled before the top; no selection was made')
            previous_scroll = frame.screen.scrollbar
            self.swipe(frame, (1050, 278), (1050, 358), 'Find the beginning of the assistant list')
            frame = self.wait_assistant()
        self.fail('Could not reach the beginning of the assistant list')

    def _survey_assistants(self, frame, damage_type, remaining_team):
        frame = self._assistant_start(frame)
        ceiling = best_at_observed_ceiling(frame.screen, damage_type, remaining_team)
        if ceiling:
            self.journal.record('assistant_survey', complete=True, proof='maximum_stars_at_highest_sorted_level',
                                level=ceiling.member.level, stars=ceiling.member.stars,
                                student=ceiling.member.student_id)
            return ceiling, frame
        candidates, rows_seen, rows_complete = {}, set(), set()
        preceding_rows = set()
        previous_scroll = None
        unchanged = 0
        for _ in range(240):
            page = frame.screen
            if page.incomplete or page.scrollbar is None:
                self.fail('Some assistant cards could not be read; choose a lower difficulty or play manually')
            current_rows = {key for key, _ in page.rows}
            if (not current_rows or (preceding_rows and not preceding_rows & current_rows)
                    or len(current_rows) != len(page.rows)):
                self.fail('Assistant list overlap is unverified; no optimal candidate was established')
            rows_seen.update(current_rows)
            rows_complete.update(key for key, complete in page.rows if complete)
            preceding_rows = current_rows
            for card in page.cards:
                candidates[card.member.assistant_id] = card.member
            if page.at_bottom:
                if rows_seen != rows_complete:
                    self.fail('Some assistant rows were only partially visible; no optimal candidate was established')
                break
            unchanged = unchanged + 1 if page.scrollbar == previous_scroll else 0
            if unchanged >= 3:
                self.fail('Assistant scrolling stalled before the bottom; no optimal candidate was established')
            previous_scroll = page.scrollbar
            # Slow, overlapping drags keep each 171px card fully exposed in the
            # 278px viewport during the scan. A clipped card is never a candidate.
            self.swipe(frame, (1050, 418), (1050, 378), 'Inspect more assistant strikers')
            frame = self.wait_assistant()
        else:
            self.fail('Assistant survey reached its limit before the bottom')
        selected = choose_assistant(candidates.values(), damage_type, remaining_team)
        if selected is None:
            self.fail('No compatible assistant striker is available; choose a lower difficulty or play manually')
        self.journal.record('assistant_survey', complete=True, proof='bounded_top_to_bottom_scan',
                            candidates=len(candidates), student=selected.student_id,
                            stars=selected.stars, level=selected.level)
        frame = self._assistant_start(frame)
        previous_scroll, unchanged = None, 0
        for _ in range(240):
            found = next((card for card in frame.screen.cards if card.member == selected), None)
            if found:
                return found, frame
            if frame.screen.at_bottom:
                break
            if frame.screen.scrollbar is None:
                self.fail('Assistant scroll position became unreadable while locating the selected offering')
            unchanged = unchanged + 1 if frame.screen.scrollbar == previous_scroll else 0
            if unchanged >= 3:
                self.fail('Assistant scrolling stalled while locating the selected offering; no replacement was selected')
            previous_scroll = frame.screen.scrollbar
            self.swipe(frame, (1050, 418), (1050, 378), 'Locate the selected assistant offering')
            frame = self.wait_assistant()
        self.fail('The selected assistant offering changed; no replacement was selected')

    def choose_assistant_team(self, detail, original_team, mock_result):
        removed = least_damage_striker(original_team, mock_result)
        if removed is None:
            self.fail('Mock damage report is incomplete; choose a lower difficulty or complete Total Assault manually')
        strikers = [member for member in original_team if member.role == 'striker']
        damage_types = {member.damage_type for member in strikers}
        if len(damage_types) != 1 or any(member.assistant for member in original_team):
            self.fail('Auto formation attack type or assistant provenance is ambiguous; select a lower difficulty or play manually')
        damage_type = next(iter(damage_types))
        self.check_context(detail)
        self.tap(detail, detail.screen.mock_target, 'Open free Mock Battle for assistant fallback')
        formation = self.wait('formation', predicate=lambda screen: len(screen.team) == 6)
        if team_fingerprint(formation.screen.team) != team_fingerprint(original_team):
            self.fail('The auto team changed before assistant substitution; run a fresh mock')
        self.tap(formation, formation.screen.quick_target, 'Edit the verified mock team')
        quick = self.wait('quick')
        slot_x = 68 + 90 * removed.slot
        self.tap(quick, (slot_x, 595), f'Remove lowest-damage striker {removed.student_id}')
        quick = self.wait('quick', predicate=lambda screen: text_in(
            screen.words, (23+90*removed.slot, 560, 112+90*removed.slot, 635)).strip().upper() == 'EMPTY')
        self.tap(quick, quick.screen.assistant_target, 'Open available assistant strikers')
        frame = self.wait_assistant()
        if not frame.screen.available:
            self.fail('No assistant striker can be borrowed today; choose a lower difficulty or play manually')
        frame = self.filter_assistants(frame, damage_type)
        remaining = tuple(member for member in original_team if member.slot != removed.slot)
        selected, frame = self._survey_assistants(frame, damage_type, remaining)
        self.tap(frame, selected.target, f'Select assistant {selected.member.student_id}')
        frame = self.wait_assistant(predicate=lambda page: any(
            card.selected and card.member == selected.member for card in page.cards))
        selected = next(card for card in frame.screen.cards if card.selected and card.member == selected.member)
        # Reuse the original OCR observation: the borrowed flag requires both A
        # markers plus selected exact offering, not merely a matching portrait.
        if not verify_assistant_preview(decode_frame(frame.capture.png), frame.screen.words, selected, removed.slot):
            self.fail('Assistant preview or slot marker could not be verified; no paid entry was made')
        team = replace_striker(original_team, removed.student_id, selected.member)
        self.tap(frame, frame.screen.confirm_target, 'Confirm the assistant mock team')
        formation = self.wait('formation', predicate=lambda screen: len(screen.team) == 6)
        expected_owned = tuple(replace(member, assistant=False, assistant_id=None) for member in team)
        if team_fingerprint(formation.screen.team) != team_fingerprint(expected_owned):
            self.fail('Formation differs from the assistant team that was selected; repeat the free mock')
        self.assistant = next(member for member in team if member.assistant)
        self.important('total_assault_assistant', 'Selected an assistant striker for a second free mock',
                       removed=removed.student_id, team=[asdict(member) for member in team],
                       fingerprint=team_fingerprint(team))
        return formation, team
