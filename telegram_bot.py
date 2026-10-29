
import asyncio
import os
import logging
import re
import sys
import threading
import time
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram_inbox import record_message
from stock_data import fetch_price_board
from stock_history import get_average_volumes, get_moving_averages, get_rsi14

load_dotenv(dotenv_path=Path(__file__).with_name("Phong_telegram.env"))
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DATA_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
STATUS_FILE = Path(__file__).with_name(".telegram_bot_status")
STOP_REQUEST_FILE = Path(__file__).with_name(".telegram_bot_stop")
INBOX_DATABASE = Path(__file__).with_name("telegram_inbox.db")


def write_status_heartbeat(stop_event):
    temporary_file = STATUS_FILE.with_name(f"{STATUS_FILE.name}.{os.getpid()}.tmp")
    while not stop_event.is_set():
        try:
            temporary_file.write_text(str(time.time()), encoding="ascii")
            os.replace(temporary_file, STATUS_FILE)
        except OSError:
            logging.exception("Could not update Telegram bot status heartbeat")
        stop_event.wait(5)


async def monitor_stop_request(application):
    while True:
        await asyncio.sleep(1)
        if not STOP_REQUEST_FILE.exists():
            continue
        try:
            STOP_REQUEST_FILE.unlink()
        except OSError:
            logging.exception("Could not clear Telegram bot stop request")
            continue
        application.stop_running()
        return


async def start_stop_request_monitor(application):
    application.create_task(monitor_stop_request(application))


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    record_incoming_message(update)
    await reply_to_user(
        update,
        "Welcome to StockBoard!\n"
        "Use /analyze AAPL to request a stock analysis."
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    record_incoming_message(update)
    await reply_to_user(
        update,
        "Available commands:\n"
        "/start - Start the bot\n"
        "/help - Show help\n"
        "/chatid - Show this chat's numeric ID\n"
        "/ck TICKER - Check Vietcap price, change, and volume\n"
        "/analyze SYMBOL - Analyze a stock"
    )


async def chat_id_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_message and update.effective_chat:
        record_incoming_message(update)
        await reply_to_user(
            update,
            f"This chat's numeric ID is: {update.effective_chat.id}"
        )


async def analyze(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    record_incoming_message(update)
    if not context.args:
        await reply_to_user(
            update,
            "Usage: /analyze AAPL"
        )
        return

    symbol = context.args[0].upper()

    # TODO: Connect this to StockBoard's actual
    # stock analysis / Ichimoku function.
    await reply_to_user(
        update,
        f"Received request for {symbol}.\n"
        "Analysis engine integration is not configured yet."
    )


async def check_ticker(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    record_incoming_message(update)
    if len(context.args) != 1:
        await reply_to_user(update, "Usage: /ck TICKER (example: /ck MML)")
        return

    ticker = context.args[0].strip().upper()
    if not re.fullmatch(r"[A-Z0-9.]{1,10}", ticker):
        await reply_to_user(update, "Invalid ticker. Use letters and numbers only.")
        return

    try:
        quotes, _ = await asyncio.to_thread(fetch_price_board, [ticker])
    except requests.RequestException:
        await reply_to_user(update, "Could not reach Vietcap's price API. Please try again.")
        return
    except (ValueError, TypeError, KeyError):
        logging.exception("Could not parse Vietcap ticker response")
        await reply_to_user(update, "Vietcap returned an invalid response. Please try again.")
        return

    quote = quotes.get(ticker)
    if quote is None:
        await reply_to_user(update, f"Ticker {ticker} was not found on Vietcap's price board.")
        return

    history_database = DATA_DIR / "data" / "stock_history.sqlite3"
    as_of_date = date.today()
    average_volumes = get_average_volumes(history_database, [ticker], as_of_date, 10)
    average_volume = average_volumes.get(ticker)
    rsi14 = get_rsi14(history_database, ticker, quote["price"], as_of_date)
    moving_averages = get_moving_averages(
        history_database,
        ticker,
        quote["price"],
        as_of_date,
        (5, 8, 21, 55, 129),
    )
    average_volume_text = f"{average_volume:,.0f}" if average_volume is not None else "N/A"
    rsi14_text = f"{rsi14:.1f}" if rsi14 is not None else "N/A"
    moving_average_text = "\n".join(
        f"MA{period}: {value:.2f}" if value is not None else f"MA{period}: N/A"
        for period, value in moving_averages.items()
    )
    await reply_to_user(
        update,
        f"Ma CK: {ticker}\n"
        f"Gia: {quote['price']:.2f}\n"
        f"Thay Doi: {quote['change']:+.2f} | % {quote['pct']:+.2f}%\n"
        f"Khoi Luong: {quote['volume']:,.0f}\n"
        f"Khoi Luong TB 10 Phien: {average_volume_text}\n"
        f"RSI14: {rsi14_text}\n"
        f"{moving_average_text}",
    )


def record_incoming_message(update):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat or not message.text:
        return
    record_message(
        INBOX_DATABASE,
        chat.id,
        "incoming",
        message.text,
        username=user.username if user else None,
        display_name=user.full_name if user else None,
        created_at=message.date.isoformat(),
    )


async def reply_to_user(update, text):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat:
        return
    sent_message = await message.reply_text(text)
    record_message(
        INBOX_DATABASE,
        chat.id,
        "outgoing",
        sent_message.text or text,
        username=user.username if user else None,
        display_name=user.full_name if user else None,
        created_at=sent_message.date.isoformat(),
    )


async def log_text_message(update, context: ContextTypes.DEFAULT_TYPE):
    record_incoming_message(update)


def main():
    if not TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing from .env"
        )

    app = ApplicationBuilder().token(TOKEN).post_init(start_stop_request_monitor).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("chatid", chat_id_command))
    app.add_handler(CommandHandler("ck", check_ticker))
    app.add_handler(CommandHandler("analyze", analyze))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_text_message))

    stop_heartbeat = threading.Event()
    heartbeat_thread = threading.Thread(
        target=write_status_heartbeat,
        args=(stop_heartbeat,),
        daemon=True,
    )
    heartbeat_thread.start()
    print("StockBoard Telegram bot is running...")
    try:
        app.run_polling()
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=2)
        try:
            STATUS_FILE.unlink(missing_ok=True)
        except OSError:
            logging.exception("Could not remove Telegram bot status heartbeat")


if __name__ == "__main__":
    main()
