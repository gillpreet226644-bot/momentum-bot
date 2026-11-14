# Momentum Mover Bot — Beginner Setup

Your bot scans all US stocks and all Alpaca crypto every 5 minutes, buys the healthiest top movers, and sells automatically at −5% (stop-loss) or +10% (take-profit). It uses **fake money** until you change it.

## 1. Make a free Alpaca account
- Go to **alpaca.markets** → Sign up.
- You get a **paper trading** account with $100,000 of fake money. No ID or deposit is needed for that.

## 2. Get your API keys
- Log in → make sure the top-left says **Paper**.
- On the home page find **API Keys** → **Generate New Keys**.
- Copy both keys into the `.env` file in this folder:
  ```
  ALPACA_KEY=PKxxxxxxxxxxxx
  ALPACA_SECRET=xxxxxxxxxxxxxxxxxxxx
  ```
- Never share these keys or post them online.

> The `.env` file starts with a dot, so your computer may hide it. On Mac press **Cmd+Shift+.** in Finder; on Windows tick **View → Hidden items**.

## 3. Install Python (one time)
- Download from **python.org** (Windows: tick **"Add Python to PATH"** during install).

## 4. Start the bot
Open Terminal (Mac) or Command Prompt (Windows), go to this folder, and run:
```
pip install requests
python bot.py
```
(On Mac use `pip3` and `python3` if the first ones don't work.)

You'll see plain-English updates like:
```
14 Nov 15:05  — Account $100,000.00 (+0.00% today) | stock market OPEN
14 Nov 15:05  Top movers: NVDA +4.2%, SOL/USD +6.1%
14 Nov 15:05  🛒 BOUGHT SOL/USD (~$10,000) - up 6.1% today at $182.4
```
Everything is also saved in `trades.log`. You can watch trades in the Alpaca website/app too.

## 5. Stop the bot
Press **Ctrl+C**, or create an empty file named `STOP` in this folder. Open trades stay open — close them in the Alpaca app if you want.

## Changing how it behaves
Open `bot.py` — the **SETTINGS** block at the top has plain-English comments. Common tweaks:
| Setting | Meaning |
|---|---|
| `TRADE_STOCKS` / `TRADE_CRYPTO` | Turn each market on/off |
| `MONEY_PER_TRADE_PCT` | % of account per trade (10) |
| `STOP_LOSS_PCT` / `TAKE_PROFIT_PCT` | When to sell (5 / 10) |
| `MAX_OPEN_TRADES` | Max things held at once (5) |
| `DAILY_LOSS_LIMIT_PCT` | Stop buying after this daily loss (3) |

## Where it runs
It only trades while your computer is on and awake (stocks: Mon–Fri 2:30pm–9pm Irish time; crypto: any time). Start on your own computer while testing. If you later want 24/7, it can run on a small cloud server for a few euro a month.

## Before using real money
- Paper-trade for **at least 4–6 weeks** and check whether it actually made money.
- Live accounts: check Alpaca supports your country, and note crypto may not be offered to non-US residents.
- Accounts under $25,000 (US rule) can only make 3 same-day buy+sell trades per 5 days.
- To go live: make live keys, put them in `.env`, set `LIVE_TRADING = True`. The bot asks you to type a confirmation.
- Only use money you can afford to lose. Momentum strategies lose often and no bot is guaranteed to profit.
