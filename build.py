"""Builds the Windows download: release/Maple-Classic-Exp-Overlay-v<version>.zip

It packs the program, Python and everything it needs into one folder with
"Maple Classic Exp Overlay.exe",
adds Tesseract OCR (for reading map names), and zips it up for a GitHub release.

Needs Python with the packages from requirements.txt plus PyInstaller, and Tesseract
installed (https://github.com/UB-Mannheim/tesseract/wiki):
    py -3 -m pip install -r requirements.txt pyinstaller
    py -3 build.py
Set TESSERACT_DIR if Tesseract isn't in C:\\Program Files\\Tesseract-OCR.
"""
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "Maple Classic Exp Overlay"                 # the folder and the .exe
ZIP_NAME = NAME.replace(" ", "-")                  # GitHub turns spaces in downloads into dots
VERSION = re.search(r'^VERSION = "([^"]+)"', (ROOT / "maple_exp.py").read_text(encoding="utf-8"), re.M)[1]
TESSERACT = Path(os.environ.get("TESSERACT_DIR", r"C:\Program Files\Tesseract-OCR"))
BUILD, RELEASE = ROOT / "build", ROOT / "release"

READ_ME_FIRST = f"""{NAME} v{VERSION}  (beta)

1. Double-click "{NAME}.exe" to start it.
   If Windows says "Windows protected your PC", click "More info", then "Run anyway".
2. Start MapleStory and press Start in the overlay.

Keep this whole folder together: the .exe needs the files next to it.
The full guide is on the GitHub page you downloaded this from.
"""


def main():
    if not (TESSERACT / "tesseract.exe").exists():
        sys.exit(f"Tesseract wasn't found in {TESSERACT}. Install it, or set TESSERACT_DIR.")
    shutil.rmtree(BUILD, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
                    "--onedir", "--name", NAME, "--icon", str(ROOT / "assets" / "icon.ico"),
                    "--add-data", f"{ROOT / 'assets'};assets",
                    "--distpath", str(BUILD / "dist"), "--workpath", str(BUILD / "work"),
                    "--specpath", str(BUILD), str(ROOT / "maple_exp.py")], check=True)
    app = BUILD / "dist" / NAME

    # Tesseract: the program, its DLLs, and the English language data.
    tess = app / "tesseract"
    (tess / "tessdata").mkdir(parents=True)
    for f in TESSERACT.iterdir():
        if f.name.lower() == "tesseract.exe" or f.suffix.lower() == ".dll":
            shutil.copy2(f, tess)
    shutil.copy2(TESSERACT / "tessdata" / "eng.traineddata", tess / "tessdata")
    for folder in ("configs", "tessconfigs"):
        if (TESSERACT / "tessdata" / folder).is_dir():
            shutil.copytree(TESSERACT / "tessdata" / folder, tess / "tessdata" / folder)
    for notice in ("LICENSE", "LICENSE.txt", "doc/LICENSE", "doc/AUTHORS"):
        if (TESSERACT / notice).is_file():
            shutil.copy2(TESSERACT / notice, tess / f"{Path(notice).name}.txt")

    (app / "READ ME FIRST.txt").write_text(READ_ME_FIRST, encoding="utf-8")
    RELEASE.mkdir(exist_ok=True)
    zip_path = RELEASE / f"{ZIP_NAME}-v{VERSION}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(app.rglob("*")):
            z.write(f, Path(NAME) / f.relative_to(app))
    print(f"\nDone: {zip_path} ({zip_path.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
