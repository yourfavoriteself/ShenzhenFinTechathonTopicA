"""Money is integer kopecks everywhere to avoid floating-point errors."""


def kop(rubles: float | int) -> int:
    return int(round(rubles * 100))


def rub(kopecks: int) -> str:
    sign = "-" if kopecks < 0 else ""
    k = abs(kopecks)
    whole = f"{k // 100:,}".replace(",", " ")
    return f"{sign}{whole} ₽" if k % 100 == 0 else f"{sign}{whole}.{k % 100:02d} ₽"
