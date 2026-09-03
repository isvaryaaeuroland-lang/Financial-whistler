from .tadawul import TadawulScraper
from .qatar import QatarScraper
from .india_nse import NseIndiaScraper
from .dfm import DfmScraper
from .oman import OmanScraper
from .kuwait import KuwaitScraper

# Maps the hostname found in a company's profile_url (companies.xlsx) to the
# scraper that knows how to read that exchange's disclosure page. Add an
# entry here (and a new module) as scrapers for other exchanges are built --
# see README for the researched notes on ADX/EGX/etc.
SCRAPER_REGISTRY = {
    "www.saudiexchange.sa": TadawulScraper,
    "saudiexchange.sa": TadawulScraper,
    "www.qe.com.qa": QatarScraper,
    "qe.com.qa": QatarScraper,
    "www.nseindia.com": NseIndiaScraper,
    "nseindia.com": NseIndiaScraper,
    "www.dfm.ae": DfmScraper,
    "dfm.ae": DfmScraper,
    "www.msx.om": OmanScraper,
    "msx.om": OmanScraper,
    "www.boursakuwait.com.kw": KuwaitScraper,
    "boursakuwait.com.kw": KuwaitScraper,
}
