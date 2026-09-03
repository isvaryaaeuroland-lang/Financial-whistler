"""Search-by-name company lookup for Qatar Stock Exchange (QSE).

QSE's public /listed-companies page renders the full directory as plain
sector-grouped text (ticker then company name, repeated), not a clean
JSON API or a DOM structure that reliably pairs symbol-to-name via generic
selectors -- unlike Tadawul's typeahead endpoint. Rather than depending on
fragile text-parsing of that page, this ships the same 54-company directory
that was verified live against qe.com.qa on 2026-08-25 and used to
originally populate companies.xlsx's Qatar rows. No live network call is
needed for this lookup at all.

To refresh (if QSE lists/delists companies): revisit
https://www.qe.com.qa/listed-companies, which groups companies by sector as
plain text (ticker, then name, repeated), and update the list below.
"""

PROFILE_URL_TMPL = (
    "https://www.qe.com.qa/web/guest/company-profile?InformationCategory=Company"
    "&InformationType=News&FromLocalSite=N&MoreNewsTitle=1&CompanyCode={code}"
)

# (ticker, company name) -- captured from qe.com.qa/listed-companies, 2026-08-25
_DIRECTORY = [
    ("QNBK", "QNB"), ("QIBK", "Qatar Islamic Bank"), ("CBQK", "Commercial Bank of Qatar"),
    ("DHBK", "Doha Bank"), ("ABQK", "Ahli Bank"), ("QIIK", "International Islamic Bank"),
    ("MARK", "Al Rayan"), ("QFBQ", "Lesha Bank (QFC)"), ("DUBK", "Dukhan Bank"),
    ("NLCS", "National Leasing"), ("DBIS", "Dlala"), ("QOIS", "Qatar Oman Investment"),
    ("IHGS", "Inma Holding"), ("ZHCD", "Zad Holding Company"),
    ("QGMD", "Qatar German Co. for Medical Devices"), ("SIIS", "Salam International Investment"),
    ("BLDN", "Baladna"), ("MCGS", "Medicare Group"), ("QCFS", "Qatar Cinema & Film Distribution"),
    ("QFLS", "Qatar Fuel (WOQOD)"), ("WDAM", "Widam Food Company"), ("MCCS", "Mannai Corporation"),
    ("MERS", "Al Meera Consumer Goods"), ("MKDM", "Mekdam Holding"), ("MEZA", "MEEZA QSTP"),
    ("FALH", "Faleh Educational Holding"), ("MHAR", "Al Mahhar Holding"), ("MFMS", "Mosanada Holding"),
    ("QAMC", "Qatar Aluminium Manufacturing (QAMCO)"), ("QIMD", "Industries Qatar Manufacturing Co."),
    ("QNCD", "National Cement Company"), ("IQCD", "Industries Qatar"), ("QIGD", "The Investors"),
    ("QEWS", "Nebras Energy"), ("AHCS", "Aamal Company"), ("GISS", "Gulf International Services"),
    ("MPHC", "Mesaieed Petrochemical Holding"), ("IGRD", "Estithmar Holding"),
    ("QATI", "Qatar Insurance Company"), ("DOHI", "Doha Insurance Group"),
    ("QLMI", "QLM Life & Medical Insurance"), ("QGRI", "Qatar General Insurance & Reinsurance"),
    ("AKHI", "Alkhaleej Takaful Insurance"), ("QISI", "Qatar Islamic Insurance"),
    ("BEMA", "Damaan Islamic Insurance (Beema)"), ("UDCD", "United Development Company"),
    ("BRES", "Barwa Real Estate"), ("ERES", "Ezdan Holding Group"), ("MRDS", "Mazaya Qatar Real Estate"),
    ("ORDS", "Ooredoo"), ("VFQS", "Vodafone Qatar"), ("QNNS", "Qatar Navigation (Milaha)"),
    ("GWCS", "Gulf Warehousing Company"), ("QGTS", "Qatar Gas Transport (Nakilat)"),
]


def search_companies(query: str, limit: int = 8) -> list[dict]:
    query = (query or "").strip().lower()
    if len(query) < 2:
        return []

    starts, contains = [], []
    for ticker, name in _DIRECTORY:
        name_l = name.lower()
        if name_l.startswith(query) or ticker.lower().startswith(query):
            starts.append((ticker, name))
        elif query in name_l:
            contains.append((ticker, name))

    results = (starts + contains)[:limit]
    return [
        {
            "name": name,
            "ticker": ticker,
            "market_label": "Qatar (QSE)",
            "profile_url": PROFILE_URL_TMPL.format(code=ticker),
        }
        for ticker, name in results
    ]
