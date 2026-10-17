"""Money is stored as integer cents everywhere to avoid floating-point errors."""


def usd(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


def cents(dollars: float | int) -> int:
    return int(round(dollars * 100))
