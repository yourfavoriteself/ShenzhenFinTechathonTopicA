"""Fictional merchants known to the sandbox bank. Domains use the reserved `.example` TLD."""

from kernel.merchants import Merchant, MerchantDirectory
from kernel.statements import Category as C


def _m(token, name, kind, category, domain=None, service=None, account="", **kw) -> Merchant:
    return Merchant(token=token, name=name, kind=kind, category=category, domain=domain, service=service,
                    account=account, **kw)


MERCHANTS = [
    _m("tok_melodify", "Melodify", "subscription", C.SUBSCRIPTIONS, "melodify.example", "music", "40702810400000000101"),
    _m("tok_tunewave", "TuneWave", "subscription", C.SUBSCRIPTIONS, "tunewave.example", "music", "40702810400000000102"),
    _m("tok_streambox", "StreamBox", "subscription", C.SUBSCRIPTIONS, "streambox.example", "video",
       "40702810400000000103", cancel_path="/account"),
    _m("tok_pixelplay", "PixelPlay", "subscription", C.SUBSCRIPTIONS, "pixelplay.example", "games",
       "40702810400000000104"),
    _m("tok_newsdaily", "NewsDaily", "subscription", C.SUBSCRIPTIONS, "newsdaily.example", "news",
       "40702810400000000105"),
    _m("tok_cloudnest", "CloudNest", "subscription", C.SUBSCRIPTIONS, "cloudnest.example", "cloud",
       "40702810400000000106", cancel_path="/billing"),
    _m("tok_irongym", "IronGym", "subscription", C.SPORT, "irongym.example", None, "40702810400000000107"),
    _m("tok_mosenergo", "Mosenergo", "biller", C.UTILITIES, "mosenergo.example", None, "40702810900000004321",
       aliases=["Mosenergo Sbyt"]),
    _m("tok_citywater", "CityWater", "biller", C.UTILITIES, "citywater.example", None, "40702810900000004455"),
    _m("tok_telelink", "TeleLink", "biller", C.TELECOM, "telelink.example", None, "40702810900000005501"),
    _m("tok_nethome", "NetHome", "biller", C.TELECOM, "nethome.example", None, "40702810900000005502"),
    _m("tok_northwind", "Northwind LLC", "employer", C.INCOME),
    _m("tok_freshmart", "FreshMart", "merchant", C.GROCERIES),
    _m("tok_greengrocer", "GreenGrocer", "merchant", C.GROCERIES),
    _m("tok_coffeepoint", "Coffee Point", "merchant", C.CAFES),
    _m("tok_bistro21", "Bistro 21", "merchant", C.CAFES),
    _m("tok_nightowl", "Night Owl Bar", "merchant", C.CAFES),
    _m("tok_cinemapark", "CinemaPark", "merchant", C.ENTERTAINMENT),
    _m("tok_metro", "City Metro", "merchant", C.TRANSPORT),
    _m("tok_taxigo", "TaxiGo", "merchant", C.TRANSPORT),
    _m("tok_marketplace", "MarketPlace", "merchant", C.SHOPPING, "marketplace.example"),
    _m("tok_stylehub", "StyleHub", "merchant", C.SHOPPING),
    _m("tok_fixit", "FixIt Service", "merchant", C.SHOPPING),
    _m("tok_pharmaplus", "PharmaPlus", "merchant", C.HEALTH),
    _m("tok_smiledent", "SmileDent", "merchant", C.HEALTH),
    _m("tok_skyair", "SkyAir", "merchant", C.TRAVEL, "skyair.example"),
    _m("tok_staywell", "StayWell Hotels", "merchant", C.TRAVEL, "staywell.example"),
]

BLOCKLIST = {"tok_mule_safe_account", "tok_mule_ring_1"}


def directory() -> MerchantDirectory:
    return MerchantDirectory([m.model_copy() for m in MERCHANTS], BLOCKLIST)
