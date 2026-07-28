# pytest imports test modules without touching sys.path (importlib mode), so
# the shared SVG builders next to them need this entry.

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
