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
                timeout=15,
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
        message = (
            "V11 Trading Bot\n\n"
            "Telegram Connected\n"
            f"Mode: {mode}\n"
            "Status: ONLINE"
        )
        return self.send_message(message)

    def send_test(self):
        return self.send_message(
            "V11 Trading Bot\n\n"
            "Telegram connection successful.\n"
            "Bot: Techboys77bot\n"
            "Status: ONLINE\n"
            "Mode: DEMO"
        )

    def send_scan_summary(
        self,
        cycle_no,
        scanned,
        longs,
        shorts,
        neutral,
        watching,
        setup_ready,
        entry_ready,
        executed,
        rejected,
    ):
        message = (
            f"V11 SCAN #{cycle_no}\n\n"
            f"Scanned: {scanned}\n"
            f"LONG: {longs} | SHORT: {shorts} | NEUTRAL: {neutral}\n"
            f"Watching: {watching}\n"
            f"Setup-ready: {setup_ready}\n"
            f"Entry-ready: {entry_ready}\n"
            f"Executed: {executed}\n"
            f"Rejected: {rejected}"
        )
        return self.send_message(message)

    def send_setup_ready(
        self,
        symbol,
        side,
        entry,
        sl,
        tp,
        rr,
        confluence,
        reason,
    ):
        message = (
            "V11 SETUP READY\n\n"
            f"Symbol: {symbol}\n"
            f"Side: {side.upper()}\n"
            f"Entry: {entry}\n"
            f"SL: {sl}\n"
            f"TP: {tp}\n"
            f"RR: {rr:.2f}\n"
            f"Confluence: {confluence}\n\n"
            f"Reason: {reason}"
        )
        return self.send_message(message)

    def send_execution(
        self,
        symbol,
        side,
        entry,
        sl,
        tp,
        status,
        order_id=None,
    ):
        message = (
            "V11 EXECUTION\n\n"
            f"Symbol: {symbol}\n"
            f"Side: {side.upper()}\n"
            f"Entry: {entry}\n"
            f"SL: {sl}\n"
            f"TP: {tp}\n"
            f"Status: {status}\n"
            f"Order ID: {order_id or 'N/A'}"
        )
        return self.send_message(message)


telegram = TelegramNotifier()
