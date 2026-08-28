"""
Scraper class registry. Each county gets its own module here (one per
DISTINCT portal platform — Texas probate courts commonly run on Tyler
Technologies' Odyssey Public Access or a Tyler "cloud portal" product, so
several counties may end up sharing a scraper class the way publicsearch.us
does for foreclosure in the sister repo. Verify per-county before assuming
identical behavior, per SYSTEM_GUIDE.md §3 / §7).

Add each new county's import + name below as its scraper is completed:

    from .collin import CollinCountyScraper
    from .bexar import BexarCountyScraper
    ...

    __all__ = ['CollinCountyScraper', 'BexarCountyScraper', ...]
"""

from .counties import DentonCountyScraper
from .harris import HarrisCountyScraper
from .ellis import EllisCountyScraper

__all__ = ['DentonCountyScraper', 'HarrisCountyScraper', 'EllisCountyScraper']
