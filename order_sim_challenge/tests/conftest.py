from __future__ import annotations

import sys
from pathlib import Path

# Allow `import order_sim` when pytest cwd is repo root or challenge root
_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
