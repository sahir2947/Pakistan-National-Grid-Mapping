"""`python -m pakgrid ...`: the same command-line tools as `pakgrid ...`."""

import sys

from .cli import main

sys.exit(main())
