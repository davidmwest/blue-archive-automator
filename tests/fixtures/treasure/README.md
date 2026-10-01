# Treasure board recognition fixtures

These canonical 1280×720 crops retain only the game title, treasure board, and supply counters from authorized local testing on 2026-09-30. Player balances and character artwork are blanked. JSON sidecars contain local OCR output in canonical coordinates; tests do not require an OCR engine or emulator.

- `selected`: one selected tile at row 2, column 4; 45 closed tiles.
- `two-revealed`: two phone fragments; 43 closed tiles, phone count 5.
- `three-revealed`: completed vertical phone; 42 closed tiles, phone count 4.
- `completed-sunscreen`: seven open cells, including a dimmed sunscreen spanning (2,2)–(2,3), and 38 closed tiles.
- `completed-gun`: completed gun, sunscreen, phone; two verified empty stone slots at (0,1) and (4,1). Gun x1 counter comes from native regional OCR because full-frame OCR omitted it.
- `completed-phone`: all five phone prizes collected. The phone card shows a high-confidence Finish stamp and a dim x0 counter from native regional OCR; neither clue alone can substitute for missing evidence. Four verified empty slots remain distinct from prize art.
- `round-complete`, `final-reveal.json`: shaded round-one completion banner and the last reveal's durable pre-spend board; recovery requires a logged receipt and exact remaining prize footprint.
- `refresh-confirm`: the exact free next-round Notice, accepted only with a completed-board refresh intent.
- `round-two-umbrella`: partially revealed umbrella artwork misread by OCR as low-confidence glyphs; no dialogue covers the grid.
- `round-two`: fresh second-round board with 2,359 event currency and inventory (1, 2, 5); detects layout changes across refresh.
- `board`, `revealed-one`: dialogue overlaps the leftmost tiles. Recognition must wait for a clear board.
- `confirm`: the exact open-selected-slots confirmation.
- `receipt.json`: OCR for the separate `../loot-treasure-single-credits-native.png` sanitized receipt fixture.

Rows and columns are zero-based. The five closed-tile templates and selected-check template in `ba_automator/assets/treasure` are tiny crops from these same observations. Unknown revealed tile art and confirmation wording other than the verified free-refresh Notice are rejected.
