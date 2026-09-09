# Third-party notices and provenance

The combined project is distributed under GPL-3.0-or-later. This notice does not replace or relicense third-party components. The project's original source changes are contributed under GPL-3.0-or-later; no exclusive authorship of the upstream components is claimed.

## QMC2 Python modules — MIT declaration

`src/lib/crypto.py` and `src/lib/rc4.py` were obtained from:

- Repository: https://github.com/jeff-0717/qq-music-decoder
- Pinned commit: `b09abe65ead438e43c2c6f124d27631e36f6f57b`
- Original paths: `src/qmdec/crypto.py`, `src/qmdec/rc4.py`
- License evidence: the README at that commit explicitly declares MIT. The inspected source headers contain no individual copyright notice. A separate upstream LICENSE file was not present in the inspected repository root; the README declaration is recorded here without inventing a copyright holder or year.
- That repository is a fork of https://github.com/Sophomoresty/qmdec ; the original repository README at `c4b22f0e5683f99fda079f1cd9e9f6cdd39d1ce9` also declares MIT.
- A copy of the MIT permission and warranty text is provided in `licenses/qmdec-MIT.txt`; existing source notices are retained.

Before adding provenance comments, `crypto.py` was byte-for-byte equivalent after line-ending normalization. `rc4.py` changes the hash multiply to unsigned 32-bit wrapping and removes the pre-wrap overflow check. Upstream SHA-256 values:

| Source | SHA-256 |
|---|---|
| crypto.py | `8f9993b74bcc16b89d6c83dcc13b11458d7645be80bf2e4790e99a47e08fbeb6` |
| rc4.py | `45c055f67afea65a1907cf3227cdd1dff0b75c931b0f2a96777dce4dfc5a00ff` |

## QMC2 Rust reference — GPL-3.0-or-later

- Repository: https://github.com/ownlight6/qmc-decoder
- Pinned commit: `293bdc262a2f59ab6d45fa7149ce6c3f4ebfba54`
- Reference: `src/qmc2.rs`
- Copyright notice: `Copyright (C) 2026 ownlight6`, reproduced from the upstream LICENSE.
- The unsigned-32-bit hash behavior and EncV2 handling were checked against this reference during development. The JavaScript streaming transform and Python exporter implement the same format. The full original LICENSE is retained in `licenses/qmc-decoder-GPL-3.0.txt`.
- The overall GPL-3.0-or-later selection accommodates this reference and potential adapted expression; citing an algorithm alone is not being treated as proof that all referencing code is necessarily derivative.

## External tools

FFmpeg and Node.js are invoked as external processes and are not included in the program package.

- FFmpeg: https://ffmpeg.org/ ; license information: https://ffmpeg.org/legal.html .
- Node.js: https://nodejs.org/ ; license information: https://github.com/nodejs/node/blob/main/LICENSE .

## Python, Tcl/Tk and PyInstaller

Python and Tcl/Tk are runtime dependencies: https://docs.python.org/3/license.html . The build includes the exact interpreter's `LICENSE.txt` and Tcl/Tk license files from that interpreter's installation. Distribution must retain the relevant bundled component notices.

For Python installations missing a Tcl notice, an exact-version fallback is provided for Tcl 8.6.12 only: `licenses/tcl8.6.12-license.terms`, obtained from https://github.com/tcltk/tcl/blob/core-8-6-12/license.terms . The build checks the actual Tcl patch level before using this fallback. Other missing versions cause a build error rather than substituting an unrelated license.

PyInstaller is a build dependency. Its GPL exception permits distribution of generated applications under their chosen license, subject to dependencies: https://pyinstaller.org/en/stable/license.html . PyInstaller itself is not modified here.

No third-party permission above grants rights to music recordings, account sessions, service access, trademarks or circumvention of technical measures.
