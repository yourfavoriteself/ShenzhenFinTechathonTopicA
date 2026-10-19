"""Money is integer cents everywhere to avoid floating-point errors."""

from decimal import ROUND_HALF_UP, Decimal

# Bank of Russia official rate for 20 Oct 2026 (cbr-xml-daily.ru). The synthetic bank world was
# first built in rubles; every amount of it is converted once, through from_rub(), at this rate.
RUB_PER_USD = Decimal("82.8504")


def cents(dollars: float | int) -> int:
    return int(round(dollars * 100))


def from_rub(rubles: float | int) -> int:
    """Cents for an amount of the original ruble world: round(rubles / RUB_PER_USD, 2) dollars."""
    dollars = (Decimal(str(rubles)) / RUB_PER_USD).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(dollars * 100)


def usd(amount: int) -> str:
    """US style: $1,750 for whole dollars, $14.95 otherwise, -$5 for negatives."""
    sign = "-" if amount < 0 else ""
    c = abs(amount)
    whole = f"{c // 100:,}"
    return f"{sign}${whole}" if c % 100 == 0 else f"{sign}${whole}.{c % 100:02d}"
