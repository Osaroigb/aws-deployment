from bot.telegram_bot import setup_bot

if __name__ == "__main__":
    bot = setup_bot()
    bot.start_polling()
    bot.idle()