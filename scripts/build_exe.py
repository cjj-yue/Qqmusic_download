"""Build one Windows EXE containing Python, Tk, Node and FFmpeg."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tkinter

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'src'


def main():
    if sys.platform != 'win32':
        raise SystemExit('Build the desktop application on Windows.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', default=os.environ.get('QQMUSIC_NODE') or shutil.which('node'))
    parser.add_argument('--ffmpeg', default=os.environ.get('QQMUSIC_FFMPEG') or shutil.which('ffmpeg'))
    args = parser.parse_args()
    bundled = ROOT / 'build' / 'bundled-bin'
    bundled.mkdir(parents=True, exist_ok=True)
    for name in ('node', 'ffmpeg'):
        value = getattr(args, name)
        if not value or not Path(value).is_file():
            raise SystemExit(f'Provide --{name} with the build-time executable path.')
        shutil.copy2(value, bundled / f'{name}.exe')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed',
               '--noupx', '--name', 'QQMusicDownloader', '--distpath', str(ROOT / 'dist'),
               '--workpath', str(ROOT / 'build/work'), '--specpath', str(ROOT / 'build'),
               '--paths', str(SOURCE), '--paths', str(SOURCE / 'lib'), '--add-data', f'{SOURCE / "qmc_decrypt.cjs"};.',
               '--add-binary', f'{bundled / "node.exe"};bin',
               '--add-binary', f'{bundled / "ffmpeg.exe"};bin',
               '--add-data', f'{ROOT / "LICENSE"};.', '--add-data', f'{ROOT / "docs/THIRD_PARTY_NOTICES.md"};docs',
               '--add-data', f'{ROOT / "licenses"};licenses']
    # Include the exact interpreter's redistribution notices when packaging it.
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise SystemExit('Python LICENSE.txt is required for packaging.')
    command += ['--add-data', f'{python_license};licenses/python']
    interpreter = tkinter.Tcl()
    tcl_patch = interpreter.eval('info patchlevel')
    for component in ('tcl8.6', 'tk8.6'):
        base = Path(os.environ.get('TCL_LIBRARY' if component.startswith('tcl') else 'TK_LIBRARY',
                                   str(Path(sys.base_prefix) / 'tcl' / component)))
        license_file = base / 'license.terms'
        if not license_file.is_file() and component == 'tcl8.6':
            license_file = ROOT / 'licenses' / f'tcl{tcl_patch}-license.terms'
        if not license_file.is_file():
            raise SystemExit(f'{component} license.terms is required for packaging.')
        command += ['--add-data', f'{license_file};licenses/{component}']
    command.append(str(SOURCE / 'music_gui.py'))
    env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(ROOT / 'build/pyinstaller-cache'))
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    print('Built:', ROOT / 'dist/QQMusicDownloader.exe')
    print('Standalone EXE includes Python, Tcl/Tk, Node.js and FFmpeg.')


if __name__ == '__main__':
    main()
