"""Build the GUI; Node and FFmpeg remain separately installed tools."""
import os
from pathlib import Path
import subprocess
import sys
import tkinter

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'src'


def main():
    if sys.platform != 'win32':
        raise SystemExit('Build the desktop application on Windows.')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onefile', '--windowed',
               '--noupx', '--name', 'QQMusicDownloader', '--distpath', str(ROOT / 'dist'),
               '--workpath', str(ROOT / 'build/work'), '--specpath', str(ROOT / 'build'),
               '--paths', str(SOURCE), '--paths', str(SOURCE / 'lib'), '--add-data', f'{SOURCE / "qmc_decrypt.cjs"};.',
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
    print('Install Node.js and FFmpeg separately, or place their executables in a local bin directory.')


if __name__ == '__main__':
    main()
