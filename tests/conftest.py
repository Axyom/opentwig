"""pytest bootstrap - make the in-tree src/ layout importable without `pip install -e .`."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
