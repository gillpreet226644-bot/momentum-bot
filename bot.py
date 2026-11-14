"""
Momentum Mover Bot  -  a beginner-friendly automated trading bot for Alpaca.

What it does, in plain English:
  1. Every few minutes it scans the whole US stock market and all crypto
     coins on Alpaca for the biggest "movers" (things going up today).
  2. It keeps only the healthy-looking ones: real companies (price $5+),
     enough trading volume, still near today's high, not a crazy spike.
  3. It buys the best few, using a fixed slice of your account each time.
  4. It sells automatically when a trade is down 5% (stop-loss)
     or up 10% (take-profit).
  5. If the account drops 3% in one day, it stops buying until tomorrow.

It starts in PAPER mode (fake money). Nothing real is at risk until you
change LIVE_TRADING to True AND type a confirmation when it starts.

Run it:   python bot.py          (keeps running, checks every few minutes)
          python bot.py once     (one check then exits - used by GitHub)
          python bot.py test     (buys + sells $10 of Bitcoin to prove it works)
Stop it:  press Ctrl+C   (or create a file called STOP in this folder)
"""

import os
import sys
import time
import math
import logging
import json
from datetime import datetime, date, timedelta, timezone

import requests

# =====================================================================
#  SETTINGS  -  the only part you normally need to change
# =====================================================================

LIVE_TRADING = False          # False = fake money (paper). Keep False while learning!

TRADE_STOCKS = True           # scan + trade US stocks
TRADE_CRYPTO = True           # scan + trade crypto (24/7)

# "Balanced" risk profile
MONEY_PER_TRADE_PCT = 10      # each trade uses 10% of your account
MAX_OPEN_TRADES = 5           # never hold more than 5 things at once
STOP_LOSS_PCT = 5             # sell if a trade falls 5%
TAKE_PROFIT_PCT = 10          # sell if a trade rises 10%
DAILY_LOSS_LIMIT_PCT = 3      # stop buying for the day if account is down 3%

# What counts as a "good mover"
MIN_GAIN_PCT = 3              # up at least 3% today ...
MAX_GAIN_PCT = 15             # ... but not more than 15% (avoid wild spikes)
MIN_STOCK_PRICE = 5           # skip penny stocks under $5
MIN_STOCK_VOLUME = 50_000     # shares traded today (IEX feed) - skip illiquid stocks
NEAR_HIGH_PCT = 2             # price must be within 2% of today's high

CHECK_EVERY_MINUTES = 5       # how often the bot wakes up

# =====================================================================
#  (You don't need to edit below this line)
# =====================================================================

HERE = os.path.dirname(os.path.abspath(__file__))
STOP_FILE = os.path.join(HERE, "STOP")
DATA_URL = "https://data.alpaca.markets"
TRADE_URL = ("https://api.alpaca.markets" if LIVE_TRADING
             else "https://paper-api.alpaca.markets")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%d %b %H:%M",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(os.path.join(HERE, "trades.log"), encoding="utf-8")],
)


LOG_LINES = []      # this run's messages, shown on the dashboard
RESULTS = []        # trades closed during this run


def say(msg):
    logging.info(msg)
    LOG_LINES.append({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "msg": msg})
    summary = os.getenv("GITHUB_STEP_SUMMARY")   # shows on the GitHub run page
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"- {datetime.now(timezone.utc):%d %b %H:%M} UTC  {msg}\n")


# ---------------------------------------------------------------------
#  Keys
# ---------------------------------------------------------------------
def load_keys():
    """Read API keys from the .env file next to this script."""
    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    key, secret = os.getenv("ALPACA_KEY"), os.getenv("ALPACA_SECRET")
    if not key or not secret or "paste" in key.lower():
        sys.exit("\n❌ No API keys found. Open the .env file and paste your Alpaca "
                 "keys in (see README.md, step 2).\n")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


HEADERS = {}


def api(method, base, path, **kw):
    r = requests.request(method, base + path, headers=HEADERS, timeout=20, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:200]}")
    return r.json() if r.text else {}


# ---------------------------------------------------------------------
#  Scanning for movers
# ---------------------------------------------------------------------
def looks_good(price, day_open, day_high, gain_pct):
    """The 'is this a healthy mover?' test, shared by stocks and crypto."""
    if not (MIN_GAIN_PCT <= gain_pct <= MAX_GAIN_PCT):
        return False
    if price <= day_open:                              # must be up on the day
        return False
    if price < day_high * (1 - NEAR_HIGH_PCT / 100):   # must still be near the high
        return False
    return True


def scan_stocks():
    movers = api("GET", DATA_URL, "/v1beta1/screener/stocks/movers",
                 params={"top": 50}).get("gainers", [])
    pre = [m["symbol"] for m in movers
           if m.get("price", 0) >= MIN_STOCK_PRICE
           and MIN_GAIN_PCT <= m.get("percent_change", 0) <= MAX_GAIN_PCT]
    if not pre:
        return []
    snaps = api("GET", DATA_URL, "/v2/stocks/snapshots",
                params={"symbols": ",".join(pre), "feed": "iex"})
    picks = []
    for sym, s in snaps.items():
        try:
            price = s["latestTrade"]["p"]
            day, prev = s["dailyBar"], s["prevDailyBar"]
            gain = (price / prev["c"] - 1) * 100
        except (KeyError, TypeError, ZeroDivisionError):
            continue
        if day.get("v", 0) < MIN_STOCK_VOLUME:
            continue
        if looks_good(price, day["o"], day["h"], gain):
            picks.append({"symbol": sym, "price": price, "gain": gain, "type": "stock"})
    return picks


def scan_crypto():
    assets = api("GET", TRADE_URL, "/v2/assets",
                 params={"asset_class": "crypto", "status": "active"})
    syms = [a["symbol"] for a in assets
            if a.get("tradable") and a["symbol"].endswith("/USD")]
    if not syms:
        return []
    snaps = api("GET", DATA_URL, "/v1beta3/crypto/us/snapshots",
                params={"symbols": ",".join(syms)}).get("snapshots", {})
    picks = []
    for sym, s in snaps.items():
        try:
            price = s["latestTrade"]["p"]
            day, prev = s["dailyBar"], s["prevDailyBar"]
            gain = (price / prev["c"] - 1) * 100
        except (KeyError, TypeError, ZeroDivisionError):
            continue
        if looks_good(price, day["o"], day["h"], gain):
            picks.append({"symbol": sym, "price": price, "gain": gain, "type": "crypto"})
    return picks


# ---------------------------------------------------------------------
#  Trading
# ---------------------------------------------------------------------
def plain(sym):
    return sym.replace("/", "")   # positions list crypto as BTCUSD, not BTC/USD


def manage_open_trades(market_open):
    """Sell anything that hit its stop-loss or take-profit."""
    sold = []
    for p in api("GET", TRADE_URL, "/v2/positions"):
        is_crypto = p.get("asset_class") == "crypto"
        if not is_crypto and not market_open:
            continue
        change = float(p["unrealized_plpc"]) * 100
        reason = None
        if change <= -STOP_LOSS_PCT:
            reason = f"stop-loss ({change:.1f}%)"
        elif change >= TAKE_PROFIT_PCT:
            reason = f"take-profit (+{change:.1f}%)"
        if reason:
            api("DELETE", TRADE_URL, f"/v2/positions/{p['symbol']}")
            pl = float(p["unrealized_pl"])
            RESULTS.append({"symbol": p["symbol"], "pl": round(pl, 2), "pct": round(change, 2),
                            "reason": reason.split(" ")[0],
                            "t": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            say(f"💰 SOLD {p['symbol']} - {reason}, profit/loss ${pl:,.2f}")
            sold.append(p["symbol"])
    return sold


def buy(pick, amount):
    sym = pick["symbol"]
    if pick["type"] == "crypto":
        order = {"symbol": sym, "notional": f"{amount:.2f}", "side": "buy",
                 "type": "market", "time_in_force": "gtc"}
    else:
        qty = math.floor(amount / pick["price"])
        if qty < 1:
            return False
        order = {"symbol": sym, "qty": str(qty), "side": "buy",
                 "type": "market", "time_in_force": "day"}
    api("POST", TRADE_URL, "/v2/orders", json=order)
    say(f"🛒 BOUGHT {sym} (~${amount:,.0f}) - up {pick['gain']:.1f}% today at ${pick['price']:,.4g}")
    return True


# ---------------------------------------------------------------------
#  Main loop
# ---------------------------------------------------------------------
def sold_today_on_alpaca():
    """Symbols sold in the last 24h - so the bot doesn't buy straight back in.
    (Asked from Alpaca, so it works even when the bot restarts every few minutes.)"""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    try:
        orders = api("GET", TRADE_URL, "/v2/orders", params={
            "status": "closed", "after": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 200})
    except RuntimeError:
        return set()
    return {plain(o["symbol"]) for o in orders if o.get("side") == "sell"}


def run_once(state):
    acct = api("GET", TRADE_URL, "/v2/account")
    equity, last_eq = float(acct["equity"]), float(acct["last_equity"])
    day_change = (equity / last_eq - 1) * 100 if last_eq else 0
    market_open = api("GET", TRADE_URL, "/v2/clock")["is_open"]

    if state["day"] != date.today():
        state.update(day=date.today(), sold_today=set())

    say(f"— Account ${equity:,.2f} ({day_change:+.2f}% today) | "
        f"stock market {'OPEN' if market_open else 'closed'}")

    state["sold_today"].update(plain(s) for s in manage_open_trades(market_open))
    state["sold_today"] |= sold_today_on_alpaca()

    if day_change <= -DAILY_LOSS_LIMIT_PCT:
        say(f"🛑 Down {day_change:.1f}% today - daily limit hit, no new buys until tomorrow.")
        return

    positions = api("GET", TRADE_URL, "/v2/positions")
    orders = api("GET", TRADE_URL, "/v2/orders", params={"status": "open"})
    held = {p["symbol"] for p in positions} | {plain(o["symbol"]) for o in orders}
    slots = MAX_OPEN_TRADES - len(held)
    if slots <= 0:
        say(f"Holding {len(held)} trades (max {MAX_OPEN_TRADES}). Watching them.")
        return

    picks = []
    if TRADE_STOCKS and market_open:
        picks += scan_stocks()
    if TRADE_CRYPTO:
        picks += scan_crypto()
    picks = [p for p in picks
             if plain(p["symbol"]) not in held and plain(p["symbol"]) not in state["sold_today"]]
    picks.sort(key=lambda p: p["gain"], reverse=True)

    if not picks:
        say("No good movers right now. Waiting.")
        return

    say("Top movers: " + ", ".join(f"{p['symbol']} +{p['gain']:.1f}%" for p in picks[:5]))
    amount = min(equity * MONEY_PER_TRADE_PCT / 100, float(acct["buying_power"]) * 0.95)
    for pick in picks[:slots]:
        if amount < 10:
            break
        try:
            buy(pick, amount)
        except RuntimeError as e:
            say(f"⚠️  Could not buy {pick['symbol']}: {e}")


# ---------------------------------------------------------------------
#  Dashboard: save everything to status.json (the web page reads it)
# ---------------------------------------------------------------------
STATUS_FILE = os.path.join(HERE, "status.json")


def write_status():
    old = {}
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, encoding="utf-8") as f:
                old = json.load(f)
        except (ValueError, OSError):
            old = {}
    st = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
          "mode": "LIVE" if LIVE_TRADING else "PAPER",
          "settings": {"stop_loss": STOP_LOSS_PCT, "take_profit": TAKE_PROFIT_PCT,
                       "per_trade": MONEY_PER_TRADE_PCT, "max_trades": MAX_OPEN_TRADES}}
    try:
        a = api("GET", TRADE_URL, "/v2/account")
        st["equity"], st["last_equity"] = float(a["equity"]), float(a["last_equity"])
        st["cash"] = float(a["cash"])
        st["market_open"] = api("GET", TRADE_URL, "/v2/clock")["is_open"]
        st["positions"] = [{
            "symbol": p["symbol"], "qty": float(p["qty"]),
            "entry": float(p["avg_entry_price"]), "price": float(p["current_price"]),
            "value": float(p["market_value"]), "pl": float(p["unrealized_pl"]),
            "pct": float(p["unrealized_plpc"]) * 100,
        } for p in api("GET", TRADE_URL, "/v2/positions")]
        orders = api("GET", TRADE_URL, "/v2/orders",
                     params={"status": "closed", "limit": 500, "direction": "desc"})
        filled = [o for o in orders if o.get("status") == "filled"]
        st["trade_count"] = len(filled)
        st["orders"] = [{
            "symbol": o["symbol"], "side": o["side"], "qty": float(o["filled_qty"] or 0),
            "price": float(o["filled_avg_price"] or 0), "t": o["filled_at"],
        } for o in filled[:40]]
    except (RuntimeError, requests.RequestException) as e:
        st["error"] = str(e)[:200]
        for k in ("equity", "last_equity", "cash", "market_open", "positions",
                  "trade_count", "orders"):
            if k in old:
                st[k] = old[k]
    try:
        h = api("GET", TRADE_URL, "/v2/account/portfolio/history",
                params={"period": "1M", "timeframe": "1H",
                        "intraday_reporting": "continuous"})
        st["history"] = [[t, e] for t, e in zip(h.get("timestamp", []), h.get("equity", []))
                         if e is not None]
    except (RuntimeError, requests.RequestException):
        st["history"] = old.get("history", [])
    # closed-trade results and the activity log build up over time
    st["closed"] = (RESULTS[::-1] + old.get("closed", []))[:200]
    totals = old.get("totals", {"closed": 0, "wins": 0, "realized": 0.0})
    for r in RESULTS:
        totals["closed"] += 1
        totals["wins"] += r["pl"] > 0
        totals["realized"] = round(totals["realized"] + r["pl"], 2)
    st["totals"] = totals
    st["log"] = (LOG_LINES[::-1] + old.get("log", []))[:80]
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)


def test_trade():
    """Buy $10 of Bitcoin, show it, then sell it - proves everything works."""
    say("🧪 TEST MODE: buying $10 of Bitcoin (BTC/USD) to prove the bot can trade...")
    api("POST", TRADE_URL, "/v2/orders", json={
        "symbol": "BTC/USD", "notional": "10", "side": "buy",
        "type": "market", "time_in_force": "gtc"})
    pos = None
    for _ in range(15):
        time.sleep(2)
        try:
            pos = api("GET", TRADE_URL, "/v2/positions/BTCUSD")
            break
        except RuntimeError:
            pass
    if not pos:
        say("⚠️  Order sent but not filled yet. Check the Alpaca app - it should appear soon.")
        return
    say(f"✅ BOUGHT {float(pos['qty']):.8f} BTC for ${float(pos['cost_basis']):,.2f}  "
        f"(price ${float(pos['avg_entry_price']):,.2f})")
    say("Holding for 10 seconds...")
    time.sleep(10)
    api("DELETE", TRADE_URL, "/v2/positions/BTCUSD")
    say("✅ SOLD it again. Test passed - buying and selling both work!")
    say("Look in the Alpaca app -> Orders: you'll see the buy and the sell there too.")
    say("Now run   py bot.py   (without 'test') to start the real bot.")


def confirm_live():
    if os.getenv("CONFIRM_LIVE") == "I UNDERSTAND":   # for GitHub (no keyboard there)
        return
    if not sys.stdin.isatty():
        sys.exit("LIVE_TRADING is on: add a CONFIRM_LIVE secret saying I UNDERSTAND.")
    print("\n⚠️  LIVE TRADING IS ON - this bot will use REAL money.")
    if input('Type  I UNDERSTAND  to continue: ').strip() != "I UNDERSTAND":
        sys.exit("Cancelled. Set LIVE_TRADING = False to use paper trading.")


def main():
    HEADERS.update(load_keys())
    if LIVE_TRADING:
        confirm_live()
    if len(sys.argv) > 1 and sys.argv[1].lower() == "test":
        if LIVE_TRADING:
            sys.exit("Test mode only runs on paper (fake money). Set LIVE_TRADING = False.")
        try:
            test_trade()
        finally:
            write_status()
        return
    if len(sys.argv) > 1 and sys.argv[1].lower() == "once":   # used by GitHub
        try:
            run_once({"day": date.today(), "sold_today": set()})
        finally:
            write_status()
        return
    say(f"🤖 Bot started in {'LIVE 💵' if LIVE_TRADING else 'PAPER (fake money)'} mode. "
        f"Checking every {CHECK_EVERY_MINUTES} min. Ctrl+C to stop.")
    state = {"day": date.today(), "sold_today": set()}
    while True:
        if os.path.exists(STOP_FILE):
            say("STOP file found - shutting down. (Open trades are left as they are.)")
            break
        try:
            run_once(state)
            write_status()
            LOG_LINES.clear(); RESULTS.clear()
        except requests.RequestException as e:
            say(f"⚠️  Internet hiccup: {e}. Will retry.")
        except RuntimeError as e:
            say(f"⚠️  Alpaca said: {e}")
            if "401" in str(e) or "403" in str(e):
                sys.exit("Your API keys look wrong. Check the .env file.")
        time.sleep(CHECK_EVERY_MINUTES * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("👋 Bot stopped by you. Open trades are left as they are.")
