"""Source-checkout entry point; also suitable for a future frozen launcher."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from kiu.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
