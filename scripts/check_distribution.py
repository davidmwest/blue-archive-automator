"""Check release contents without importing the application or touching a device.

Run after ``python -m build``. This is also used by CI before installing the wheel
in a clean environment, so source-tree imports cannot hide missing package data.
"""

from email.parser import BytesParser
from pathlib import Path, PurePosixPath
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def single(pattern: str) -> Path:
    matches = list((ROOT / "dist").glob(pattern))
    require(len(matches) == 1, f"Expected one dist/{pattern}; use a clean dist directory.")
    return matches[0]


def main() -> None:
    with zipfile.ZipFile(single("*.whl")) as archive:
        wheel = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    with tarfile.open(single("*.tar.gz")) as archive:
        source = {}
        for member in archive.getmembers():
            if member.isfile():
                file = archive.extractfile(member)
                require(file is not None, f"Unreadable source member: {member.name}")
                source[member.name.split("/", 1)[1]] = file.read()

    package_files = [
        path for path in (ROOT / "ba_automator").rglob("*")
        if path.is_file() and path.suffix in {".py", ".png", ".json", ".html", ".js", ".css"}
    ]
    for path in package_files:
        name = path.relative_to(ROOT).as_posix()
        expected = path.read_bytes()
        require(wheel.get(name) == expected, f"Wheel omits or changes package file: {name}")
        require(source.get(name) == expected, f"Source archive omits or changes package file: {name}")

    source_files = [ROOT / "config/example.toml", ROOT / "CONTRIBUTING.md"]
    for directory in ("tests", "docs", "scripts"):
        source_files.extend(
            path for path in (ROOT / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.name != ".DS_Store"
        )
    source_files.extend((ROOT / "config/events").glob("*.json"))
    for path in source_files:
        name = path.relative_to(ROOT).as_posix()
        require(source.get(name) == path.read_bytes(), f"Source archive omits or changes: {name}")

    metadata_names = [name for name in wheel if name.endswith(".dist-info/METADATA")]
    require(len(metadata_names) == 1, "Expected one wheel metadata file.")
    metadata_name = metadata_names[0]
    metadata = BytesParser().parsebytes(wheel[metadata_name])
    require(metadata["License-Expression"] == "MIT AND LicenseRef-Game-Assets", "Incorrect license scope.")
    require(set(metadata.get_all("License-File", [])) == {"LICENSE", "THIRD_PARTY_NOTICES.md"}, "Missing license metadata.")
    prefix = metadata_name.rsplit("/", 1)[0]
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        expected = (ROOT / name).read_bytes()
        require(wheel.get(f"{prefix}/licenses/{name}") == expected, f"Missing wheel notice: {name}")
        require(source.get(name) == expected, f"Missing source notice: {name}")

    for name in set(wheel) | set(source):
        parts = PurePosixPath(name).parts
        require(not any(part in {"work", "outputs", "data", ".venv", ".git", "__pycache__"} for part in parts), f"Local data in archive: {name}")
        require(not name.startswith(("config/local.", "config/profiles/")), f"Local configuration in archive: {name}")
        require(PurePosixPath(name).suffix not in {".log", ".sqlite", ".sqlite3", ".db", ".pyc"}, f"Runtime file in archive: {name}")

    print(f"Verified wheel/source assets ({len(package_files)} package files), fixtures, docs, and license notices.")


if __name__ == "__main__":
    main()
