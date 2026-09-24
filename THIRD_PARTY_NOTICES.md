# Third-party notices

The [MIT license](LICENSE) applies to this project's original source code,
documentation, dashboard UI, and original schematic demo illustration. It does
not grant rights to the game-related material listed below.

## Game-derived images and fan character artwork — LicenseRef-Game-Assets

`ba_automator/assets/*.png` contains recognition templates cropped from Blue
Archive. `tests/fixtures/*.png` contains sanitized game screenshots and crops
used for regression testing. Their provenance is recorded in
[`tests/fixtures/README.md`](tests/fixtures/README.md).

`ba_automator/web/maid-arisu.png` is newly generated fan artwork depicting
Maid Arisu, a Blue Archive character. It is a dashboard mascot, not a game
recognition template or an official illustration. Its character design remains
the property of the game's rights holders. The character artwork is excluded
from the MIT grant even though the surrounding dashboard code and layout are
original. Screenshots containing the mascot inherit that same limitation.

These images are excluded from the project's MIT grant. Blue Archive artwork,
UI imagery, names, and trademarks belong to their respective rights holders.
This project does not grant a license to that material or claim affiliation
with or endorsement by the game's developers or publishers. Redistribution
or reuse of those assets requires its own basis; the MIT license for the code
does not supply one.

The package's SPDX expression is `MIT AND LicenseRef-Game-Assets` because the
distribution includes both original code and game-derived recognition images.
`LicenseRef-Game-Assets` refers to this notice and the exclusion above; it is
not a permissive license for those images.

## Dependencies and reference projects

Python dependencies, including NumPy, OpenCV, RapidOCR, and ONNX Runtime,
retain their own licenses. They are installed separately rather than relicensed
by this project. Consult their installed distributions for license texts and
model notices.

[AzurLaneAutoScript (ALAS)](https://github.com/LmeSzinc/AzurLaneAutoScript)
inspired the screen-driven architecture. References to it do not extend this
project's MIT license to ALAS code or assets.

The account-free demo uses fictional data and original schematic game-frame
artwork. The mascot appears in the same dashboard and has the exclusion above.
