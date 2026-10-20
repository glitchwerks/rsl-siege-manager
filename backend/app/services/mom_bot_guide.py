"""Officer content loaded server-side, never bundled with the public website."""

import json
from pathlib import Path

MOM_BOT_GUIDE = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))
