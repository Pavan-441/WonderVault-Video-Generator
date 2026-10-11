"""MoneyPrinterTurbo 应用包元数据。"""

import os

__version__ = "1.3.7"


def _enable_complex_text_shaping() -> None:
    """Let Pillow shape Telugu and other complex scripts on Windows.

    Pillow's Windows wheels ship libraqm/HarfBuzz but load FriBiDi at runtime,
    and only when Pillow's font module is first imported. Without it Pillow
    silently uses basic layout, which breaks Telugu conjuncts and vowel signs.
    The bundled DLL in resource/lib must therefore be on the DLL search path
    before anything imports PIL.ImageFont (MoviePy does, via app.services.video).
    """
    if os.name != "nt":
        return
    lib_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resource", "lib")
    if not os.path.isfile(os.path.join(lib_dir, "libfribidi-0.dll")):
        return
    path = os.environ.get("PATH", "")
    if lib_dir.lower() not in (p.lower() for p in path.split(os.pathsep)):
        # Pillow uses LoadLibrary, which searches PATH (not add_dll_directory).
        os.environ["PATH"] = lib_dir + os.pathsep + path


_enable_complex_text_shaping()
