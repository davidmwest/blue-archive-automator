# Third-party notices

The [MIT license](LICENSE) applies to this project's original source code,
documentation, dashboard, and original demo illustrations. It does not grant
rights to third-party material listed below.

## Game-derived images — LicenseRef-Game-Assets

`ba_automator/assets/*.png` contains recognition templates cropped from Blue
Archive. `tests/fixtures/*.png` contains sanitized game screenshots and crops
used for regression testing. Their provenance is recorded in
[`tests/fixtures/README.md`](tests/fixtures/README.md).

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

The account-free demo uses fictional data and original schematic artwork.
Its screenshots in `docs/images/` are covered by the project's MIT license.
