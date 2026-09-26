"""Sales tax by region."""

RATES = {"CA": 0.0725, "NY": 0.04}


def tax_for(region, amount):
    rate = RATES.get(region, 0.0)
    return round(amount * rate)
