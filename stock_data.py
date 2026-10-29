import requests


PRICE_BOARD_URL = "https://trading.vietcap.com.vn/api/price/symbols/getList"
PRICE_BOARD_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/json",
    "Referer": "https://trading.vietcap.com.vn/",
    "Origin": "https://trading.vietcap.com.vn",
}


def classify(price, ref, ceiling, floor):
    if price >= ceiling:
        return "ceil"
    if price <= floor:
        return "floor"
    if price > ref:
        return "up"
    if price < ref:
        return "down"
    return "ref"


def fetch_price_board(tickers):
    response = requests.post(
        PRICE_BOARD_URL,
        json={"symbols": tickers},
        headers=PRICE_BOARD_HEADERS,
        timeout=10,
    )
    response.raise_for_status()
    quotes, session = {}, None
    for item in response.json():
        info, match = item.get("listingInfo") or {}, item.get("matchPrice") or {}
        ref = info.get("refPrice") or 0
        if not ref:
            continue
        price = match.get("matchPrice") or ref
        session = session or match.get("session")
        volume = match.get("accumulatedVolume") or 0
        quotes[info["symbol"]] = {
            "price": price / 1000,
            "pct": round((price - ref) / ref * 100, 2),
            "vol": volume / 1_000,
            "volume": volume,
            "cls": classify(price, ref, info.get("ceiling") or 0, info.get("floor") or 0),
        }
    return quotes, session