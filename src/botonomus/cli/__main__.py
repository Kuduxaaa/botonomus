"""Allow ``python -m botonomus.cli``."""

import sys

from .main import main

sys.exit(main())
