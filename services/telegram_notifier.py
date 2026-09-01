import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class TelegramNotifier:

    def __init__(self):
        self.enabled = os.getenv("TELEGRAM_ENABLED", "false").lower() == "true"
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

        if not self.token or not self.chat_id:
            self.enabled = False

        self.api_url = (
            f"https://api.telegram.org/bot{self.token}"
            if self.token
            else ""
        )

    def send_message(self, message):
        if not self.enabled:
            logger.warning("Telegram notifier is disabled.")
            return False

        try:
            response = requests.post(
                f"{self.api_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                },
                timeout=10,
            )

            if response.ok:
                logger.info("Telegram message sent successfully.")
                return True

            logger.error(
                "Telegram API error: %s %s",
                response.status_code,
                response.text,
            )
            return False

        except Exception as exc:
            logger.error("Telegram notification failed: %s", exc)
            return False

    def send_startup(self, mode="DEMO"):
        message = "V11 Trading Bot - Telegram Connected - Mode: " + mode + " - Status: ONLINE"
        return self.send_message(message)

    def send_test(self):
        return self.send_message(
            "V11 Trading Bot\n\n"
            "Telegram connection successful.\n"
            "Bot: Techboys77bot\n"
            "Status: ONLINE\n"
            "Mode: DEMO"
        )


telegram = TelegramNotifier()
