<a href="https://pixi.sh">
    <img
      src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/prefix-dev/pixi/main/assets/badge/v0.json"
      alt="Pixi Badge"
      align="right"
    />
</a>


# Telegram Timer Bot <u>created by AI</u>

A simple Telegram bot `fully built with AI`. Set a timer, cancel it, or view your recent commands. When a timer expires, the bot sends: `Время вышло!` (“Time is up!”).

Built with Python 3.11, aiogram 3, and asyncio. Dependencies and the isolated environment are managed by Pixi. No database or external scheduler is required.

## Commands

| Command | Description | Example |
| --- | --- | --- |
| `/start` | Show the welcome message and available commands. | `/start` |
| `/help` | Show commands and usage examples. | `/help` |
| `/timer <duration>` | Start a timer, replacing your previous one. | `/timer 10m` |
| `/cancel` | Cancel your active timer. | `/cancel` |
| `/logs <n>` | Show your recent commands, their status, results, and errors. | `/logs 5` |

Supported units: `s` (seconds), `m` (minutes), `h` (hours), and `d` (days). Spaces are ignored: `/timer 10 m` works.

Durations must be positive whole numbers. The limit is 24 hours for `s`, `m`, and `h`, or 7 days for `d`. For example, `/timer 25h` is rejected, while `/timer 2d` is accepted.

Each user can have one active timer. Different users’ timers are independent.

## Installation

### Linux

1. Install [Pixi](https://pixi.prefix.dev/) if needed, then restart your terminal:

   ```bash
   curl -fsSL https://pixi.sh/install.sh | sh
   ```

2. Clone this repository and open its directory:

   ```bash
   git clone <repository-url>
   cd <repository-directory>
   ```

3. Create a bot through [@BotFather](https://t.me/BotFather) using `/newbot`. Save its token in a `.env` file next to `bot.py`:

   ```dotenv
   BOT_TOKEN=your_telegram_bot_token
   ```

4. Install dependencies and start the bot:

   ```bash
   pixi install
   pixi run start
   ```

Open your bot in Telegram and press **Start**. Stop the process with `Ctrl+C`.

<details>
<summary>Windows installation</summary>

1. Install Pixi from PowerShell, then restart your terminal:

   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm -useb https://pixi.sh/install.ps1 | iex"
   ```

2. Clone this repository and open its directory:

   ```powershell
   git clone <repository-url>
   cd <repository-directory>
   ```

3. Create a bot with [@BotFather](https://t.me/BotFather) using `/newbot`. Create a file named `.env` next to `bot.py` and add:

   ```dotenv
   BOT_TOKEN=your_telegram_bot_token
   ```

4. Install dependencies and start the bot:

   ```powershell
   pixi install
   pixi run start
   ```

Open your bot in Telegram and press Start. Stop the process with `Ctrl+C`.

</details>

## Configuration and Privacy

- Keep your bot token private. Never commit `.env` to Git.
- An existing `BOT_TOKEN` environment variable takes priority over `.env`.
- Timers and command history are stored only in memory, not written to disk.
- Each user can view only their own command history.
- Invalid arguments and unknown commands are not stored verbatim. Console logs include user IDs for troubleshooting.

## Limitations

- Restarting or stopping the bot removes all timers and command history.
- Pending Telegram updates are discarded when the bot starts.
- Notifications require the bot to remain running and connected to Telegram. Failed notifications are logged but not retried.
- History is limited to 100 commands per user; `/logs` displays up to 20 entries per request.
- Run only one polling instance per bot token.

## Disclaimer

This project was created fully with AI and is provided as-is, without warranties. Anyone may use it at their own risk. Review the code before running it, and do not rely on this bot for critical or time-sensitive reminders.