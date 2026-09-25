"""Deterministic relationship-first selection in the Cafe MomoTalk list."""
from importlib.resources import files
import re

import cv2
import numpy as np

from .invitations import parse_invitation_rows, choose_invitation
from .runtime import FRAME_MAX_AGE


def sort_direction(frame):
    crop = frame[137:169, 804:854]
    scores = []
    for direction in ('ascending', 'descending'):
        path = files('ba_automator').joinpath(f'assets/invitation-sort-{direction}.png')
        template = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), 1)
        scores.append((float(np.abs(crop.astype(float) - template).mean()), direction))
    scores.sort()
    return scores[0][1] if scores[0][0] < 10 and scores[1][0] - scores[0][0] > 3 else None


def search_state(frame):
    """Recognize the search toggle without mistaking a filtered list for all students."""
    crop = frame[137:169, 616:655]
    scores = []
    for state in ('collapsed', 'expanded'):
        path = files('ba_automator').joinpath(f'assets/invitation-search-{state}.png')
        template = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), 1)
        scores.append((float(np.abs(crop.astype(float) - template).mean()), state))
    scores.sort()
    return scores[0][1] if scores[0][0] < 10 and scores[1][0] - scores[0][0] > 3 else None


def scrollbar(frame):
    """Read the fixed list's gray thumb; None means unverified, never top."""
    strip = frame[198:590, 858:863].astype(float)
    thumb = (np.abs(strip - np.array([195, 183, 166])).max(axis=2) < 12).sum(axis=1) >= 4
    hits = np.flatnonzero(thumb)
    if len(hits) < 12 or np.any(np.diff(hits) > 1):
        return None
    return int(hits[0]) + 198, int(hits[-1]) + 198


def relationship_sort(words):
    return any(w.normalized == 'relationship rank' and 665 <= w.center[0] <= 790
               and 130 <= w.center[1] <= 173 for w in words)


def sort_menu(words):
    return all(any(w.normalized == text and box[0] <= w.center[0] <= box[2]
                   and box[1] <= w.center[1] <= box[3] for w in words)
               for text, box in [('sort', (410, 185, 490, 230)),
                                 ('relationship', (455, 290, 610, 335)),
                                 ('confirm', (540, 375, 745, 419))])


def scroll(runner, screen, *, down):
    start, end = ((650, 545), (650, 310)) if down else ((650, 265), (650, 545))
    runner.journal.record('intent', operation='invitation_scroll', start=list(start), end=list(end))
    sent = runner.device.swipe(start, end, duration_ms=900,
                              deadline=screen[0] + FRAME_MAX_AGE, monotonic=runner.clock)
    runner.journal.record('outcome', operation='invitation_scroll', result='ok' if sent else 'skipped_stale')
    if not sent:
        runner.fail('Invitation list frame expired before scrolling')
    runner.sleep(1.5)


def prepare_list(runner, screen, wait_list):
    # Inspect the actual field even when collapsed: hiding search is not proof
    # that no previously entered name is still filtering the candidate list.
    state = search_state(screen[2])
    if state == 'collapsed':
        runner.tap(screen, (631, 151), 'Check invitation search is empty')
        runner.sleep(1)
        screen = wait_list()
    if search_state(screen[2]) != 'expanded' or not any(
            w.normalized == 'enter student name' and w.confidence >= .85
            and 475 <= w.center[0] <= 815 and 198 <= w.center[1] <= 243
            for w in screen[3]):
        runner.fail('Invitation search filter is not verified empty; clear the student search field')
    runner.tap(screen, (631, 151), 'Hide the empty invitation search field')
    runner.sleep(1)
    screen = wait_list()
    if search_state(screen[2]) != 'collapsed':
        runner.fail('Invitation search field did not close')
    if not relationship_sort(screen[3]):
        runner.tap(screen, (727, 151), 'Open invitation sorting')
        screen = runner.wait_words(sort_menu, failure='Invitation Sort controls were not recognized')
        runner.tap(screen, (531, 320), 'Sort invitations by relationship rank')
        runner.sleep(.6)
        screen = runner.wait_words(sort_menu)
        runner.tap(screen, (638, 396), 'Apply relationship sorting')
        runner.sleep(1)
        screen = wait_list()
    if not relationship_sort(screen[3]):
        runner.fail('Invitation relationship sorting could not be verified')
    direction = sort_direction(screen[2])
    if direction == 'ascending':
        runner.tap(screen, (830, 152), 'Put highest relationship students first')
        runner.sleep(1)
        screen = runner.wait_words(lambda w: relationship_sort(w))
        # OCR may return the old frame while the sort is animating.
        for _ in range(6):
            if sort_direction(screen[2]) == 'descending':
                break
            runner.sleep(.5)
            screen = wait_list()
    if sort_direction(screen[2]) != 'descending':
        runner.fail('Invitation descending sort direction could not be verified')
    for _ in range(40):
        thumb = scrollbar(screen[2])
        if thumb is None:
            runner.fail('Invitation scrollbar could not be verified; clear any student search filter')
        if thumb[0] <= 203:
            return screen
        scroll(runner, screen, down=False)
        screen = wait_list()
    runner.fail('Invitation list did not reach the top of its bounded scan')


def read_rows(runner, screen):
    rows = parse_invitation_rows(screen[3])
    for row in rows:
        if row['rank'] is not None:
            continue
        # Tiny heart digits often disappear in whole-screen OCR. Read just that
        # row at 4x, keeping digit coordinates separate from student names.
        y = row['target'][1]
        if y + 39 >= 605:
            continue
        # A high-resolution retry fills a missing rank only. Conflicting digits,
        # a weak name/control, or malformed text already in the heart remain
        # unresolved instead of being overwritten by a single favorable read.
        if any(478 <= w.center[0] <= 535 and 6 <= w.center[1] - y <= 36
               for w in screen[3]):
            continue
        if any(w.confidence < .85 and abs(w.center[1] - y) <= 36
               and (488 <= w.center[0] <= 704 or 745 <= w.center[0] <= 835)
               for w in screen[3]):
            continue
        crop = screen[2][y+4:y+39, 486:527]
        found = runner.startup.read(cv2.resize(crop, None, fx=4, fy=4))
        values = {int(w.text.strip()) if w.confidence >= .85
                  and re.fullmatch(r'[0-9]{1,3}', w.text.strip())
                  and 1 <= int(w.text.strip()) <= 100 else None for w in found}
        if len(values) == 1:
            row['rank'] = values.pop()
    return rows


def select_automatic(runner, screen, wait_list):
    """Return (fresh list screenshot, candidate) or None when all are capped."""
    from .cafe import invitation_rows, floor_from_switch
    from .invitation_roster import read_current_stars

    stars = {}
    screen = prepare_list(runner, screen, wait_list)
    seen = set()
    for _ in range(60):
        rows = read_rows(runner, screen)
        ranks = [row['rank'] for row in rows if row['rank'] is not None]
        if not rows or any(a < b for a, b in zip(ranks, ranks[1:])):
            runner.fail('Invitation relationship rows were incomplete or out of order')
        identities = {row['identity'] for row in rows}
        if seen and not identities & seen:
            runner.fail('Invitation scrolling lost row overlap; highest eligible student is not proven')
        choice = choose_invitation(rows, stars)
        if choice.status == 'unreadable':
            runner.fail(choice.reason)
        if choice.status == 'need_rarity':
            row = choice.row
            floor = runner.floor
            runner.tap(screen, (844, 95), 'Close invitations to verify current student rarity')
            cap = runner.wait_cafe()
            runner.navigate(cap, (1237, 24), 'Return home for current rarity lookup',
                            source='cafe', destination='home')
            stars[row['identity']] = read_current_stars(runner, row['name'])
            runner.enter()
            cap = runner.wait_floor()
            runner.floor = floor_from_switch(cap[3])
            runner.move_to_floor(floor)
            runner.tap(runner.wait_cafe(), (883, 652), 'Reopen free invitations after rarity lookup')
            screen = prepare_list(runner, wait_list(), wait_list)
            seen.clear()
            continue
        if choice.status == 'selected':
            # Crop OCR may outlive the input deadline. Re-read the same recipient
            # in a fresh frame before allowing the caller to press Invite.
            fresh = wait_list()
            if (search_state(fresh[2]) != 'collapsed' or not relationship_sort(fresh[3])
                    or sort_direction(fresh[2]) != 'descending'):
                runner.fail('Invitation list controls changed before selection')
            matches = [r for r in invitation_rows(fresh[3]) if r['identity'] == choice.row['identity'] and r['enabled']]
            if len(matches) != 1:
                runner.fail('Automatic invitation recipient changed before selection')
            selected = dict(choice.row, target=matches[0]['target'])
            runner.journal.record('invitation_selection', strategy='highest_non_maxed_relationship',
                                  student=selected['name'], relationship_rank=selected['rank'],
                                  current_stars=stars.get(selected['identity']))
            return fresh, selected
        thumb = scrollbar(screen[2])
        if thumb is None:
            runner.fail('Invitation scroll position became unreadable')
        if thumb[1] >= 586:
            return None
        seen.update(identities)
        scroll(runner, screen, down=True)
        screen = wait_list()
    runner.fail('Automatic invitation exceeded its bounded student scan')
