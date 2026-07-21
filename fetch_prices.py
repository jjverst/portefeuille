#!/usr/bin/env python3
"""
Haalt dagelijkse EUR-slotkoersen op voor de ETF's en schrijft:
  - prices.json   : laatste koers per ISIN (leest de app bij het laden)
  - history.json  : volledige dagreeks per ISIN (voor de waardeverloop-grafiek)

Draait in GitHub Actions (daar is er vrije netwerktoegang, geen CORS).
Geen externe libraries nodig — enkel de Python-standaardbibliotheek.

Bij de eerste run (lege history) haalt het script ~2 jaar geschiedenis op.
Daarna voegt elke run enkel de nieuwste dagen toe.
"""

import json, os, sys, time, urllib.request
from datetime import datetime, timezone

# ISIN -> kandidaat Yahoo-tickers (EUR-noteringen). Meerdere = fallback als de eerste faalt.
INSTRUMENTS = {
    "IE00B4L5Y983": {"symbol": "IWDA", "yahoo": ["IWDA.AS"]},                 # iShares Core MSCI World (Amsterdam)
    "IE00BCBJG560": {"symbol": "ZPRS", "yahoo": ["ZPRS.DE", "ZPRS.F"]},       # SPDR MSCI World Small Cap (XETRA/Frankfurt)
    "IE00BKM4GZ66": {"symbol": "EMIM", "yahoo": ["EMIM.AS"]},                 # iShares Core MSCI EM IMI (Amsterdam)
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES_PATH = os.path.join(ROOT, "prices.json")
HISTORY_PATH = os.path.join(ROOT, "history.json")

UA = {"User-Agent": "Mozilla/5.0 (portfolio-price-bot)"}


def fetch_chart(symbol, rng):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range={rng}&interval=1d")
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def parse_chart(data):
    """Geeft (currency, [(datum, close), ...]) terug, of None bij lege data."""
    res = (data.get("chart") or {}).get("result")
    if not res:
        return None
    res = res[0]
    meta = res.get("meta", {})
    currency = meta.get("currency")
    ts = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    points = []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        points.append((d, round(float(c), 4)))
    return currency, points


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def merge_series(existing, new_points):
    """Voegt nieuwe (datum, koers)-punten samen met de bestaande reeks, ontdubbeld en gesorteerd."""
    by_date = {d: p for d, p in existing}
    for d, p in new_points:
        by_date[d] = p
    return [[d, by_date[d]] for d in sorted(by_date)]


def main():
    history = load_json(HISTORY_PATH, {})
    prices = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "prices": {}}
    problems = []

    for isin, info in INSTRUMENTS.items():
        have = history.get(isin, [])
        # Lege of korte geschiedenis -> volledige backfill; anders enkel de recente dagen.
        rng = "2y" if len(have) < 30 else "5d"
        points, currency, used = None, None, None

        for sym in info["yahoo"]:
            try:
                parsed = parse_chart(fetch_chart(sym, rng))
                if parsed and parsed[1]:
                    currency, points = parsed
                    used = sym
                    break
            except Exception as e:
                problems.append(f"{info['symbol']} ({sym}): {e}")
            time.sleep(1)  # vriendelijk blijven voor de bron

        def fallback(reason):
            problems.append(reason)
            # val terug op de laatst bekende koers uit de geschiedenis, indien aanwezig,
            # zodat een tijdelijke hapering dit fonds niet blanco laat in prices.json
            if have:
                ld, lp = have[-1]
                prices["prices"][isin] = {"price": lp, "date": ld, "ccy": "EUR",
                                          "symbol": info["yahoo"][0], "stale": True}

        if not points:
            fallback(f"{info['symbol']}: geen koersdata gevonden — laatst bekende koers behouden")
            continue
        if currency and currency != "EUR":
            fallback(f"{info['symbol']} ({used}): koers in {currency}, niet EUR — controleer de ticker! Laatst bekende EUR-koers behouden.")
            continue

        history[isin] = merge_series(have, points)
        last_date, last_price = history[isin][-1]
        prices["prices"][isin] = {
            "price": last_price,
            "date": last_date,
            "ccy": "EUR",
            "symbol": used,
        }
        print(f"  {info['symbol']:5} {used:8} {last_price:>10.2f} EUR  (datum {last_date}, {len(history[isin])} dagen)")

    if not prices["prices"]:
        print("FOUT: geen enkele koers opgehaald.", *problems, sep="\n  ")
        sys.exit(1)

    with open(PRICES_PATH, "w") as f:
        json.dump(prices, f, indent=2, ensure_ascii=False)
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, ensure_ascii=False)

    print(f"\nGeschreven: prices.json ({len(prices['prices'])} fondsen) + history.json")
    if problems:
        print("Aandachtspunten:", *problems, sep="\n  ")


if __name__ == "__main__":
    main()
