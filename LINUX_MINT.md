# StockBoard on Linux Mint

Copy the StockBoard project folder to the Linux Mint PC. Include `app.py`, the other Python modules, `requirements.txt`, `sectors.json`, `telegram_alerts.json`, `StockData/`, and `data/`. Do not copy a Windows `.venv`; the installer creates a separate Linux environment.

If Telegram alerts are enabled, also copy `Phong_telegram.env` securely; it contains the bot token and should not be shared publicly.

Install Python 3.10 or newer and its virtual environment support if needed:

```sh
sudo apt update
sudo apt install python3 python3-venv python3-pip
```

From the copied project folder, install StockBoard for the current Linux user:

```sh
bash install_linux.sh
```

If the shell cannot find `stock`, add `~/.local/bin` to `PATH` as shown by the installer, then open a new terminal.

```sh
stock start
stock start --port 8989
stock start -port 8989
stock status
stock stop
```

The default port is 8989. `stock start` starts the Streamlit app and, when `TELEGRAM_BOT_TOKEN` is configured, the Telegram bot. `stock stop` stops both, and `stock status` reports them separately. Open the URL printed by `start` or `status` on the Linux Mint PC. Runtime output is written to `stockboard.log` and `telegram_bot.log` in the project folder. The older `stockboard` command remains available as an alias. Keep the project folder in place while services are running; its database and settings are stored there.

To start StockBoard automatically when the PC boots, enable its per-user systemd service:

```sh
stock autostart enable
stock autostart status
```

This also enables systemd lingering so the service can start without a desktop login. If Linux requests administrator permission, run the `sudo loginctl enable-linger <username>` command shown in the error, then retry. To remove automatic startup:

```sh
stock autostart disable
```

Use `stock autostart enable --port 8989` to choose a non-default port. Disabling the service does not turn off systemd lingering, which may be used by other services for this user.