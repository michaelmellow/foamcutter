#! python3.12
"""Double-click to open the slicer window (no console window).

Same as `python -m foamdxf --gui` run from this folder. A .foamproj file
dropped on this script (or given as an argument) is opened.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from foamdxf.gui import main  # noqa: E402

raise SystemExit(main(sys.argv[1:]))
