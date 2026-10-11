# Bundled native libraries

## libfribidi-0.dll (Windows x64)

GNU FriBiDi 1.0.17, from the MSYS2 package `mingw-w64-x86_64-fribidi-1.0.17-1`
(https://packages.msys2.org/base/mingw-w64-fribidi). Unmodified. License: LGPL-2.1
(see `FRIBIDI-LICENSE.txt`); source: https://github.com/fribidi/fribidi.

Pillow's Windows wheels include libraqm and HarfBuzz but load FriBiDi at runtime.
Without this DLL Pillow falls back to basic text layout, which breaks Telugu and
other Indic scripts (detached vottulu/conjuncts, misplaced vowel signs) in
subtitles and thumbnails. `app/__init__.py` adds this folder to the DLL search
path before Pillow's font module loads. Linux/Docker installs `libfribidi0` instead.
