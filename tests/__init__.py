"""Allow standard unittest discovery to import the source tree without installation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
