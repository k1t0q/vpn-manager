"""Panel entrypoint. Running AWG app on the VPS must remain a separate service."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent / "server"))
from staging_api import app
