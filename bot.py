from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
import time
import asyncio
import logging
import re
import difflib
from aiogram import Bot, Dispatcher, types
from flask import Flask
import threading

from dotenv import load_dotenv
import os

# Load the .env file
load_dotenv()


# Configure logging------------------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(levelname)s - %(message)s')

# Configure logging------------------------------------------------------------------------------------

# Telegram Bot Setup
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
bot = Bot(token=TOKEN)
dp = Dispatcher(bot=bot)  # Corrected Dispatcher initialization

class AppointmentChecker:
    def __init__(self):
        self.url = "https://appointment.bmeia.gv.at"
        self.setup_driver()
        self.wait = WebDriverWait(self.driver, 10)  # 10-second timeout

    def _click_css_with_retry(self, css_selector: str, attempts: int = 3) -> bool:
        for _ in range(attempts):
            try:
                el = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, css_selector)))
                el.click()
                return True
            except StaleElementReferenceException:
                continue
        return False

    def _click_css_any_context(self, css_selector: str, attempts: int = 3) -> bool:
        for _ in range(attempts):
            try:
                self.driver.switch_to.default_content()
                if self._click_css_with_retry(css_selector, attempts=1):
                    return True

                iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
                for frame in iframes:
                    try:
                        self.driver.switch_to.default_content()
                        self.driver.switch_to.frame(frame)
                        if self._click_css_with_retry(css_selector, attempts=1):
                            return True
                    except StaleElementReferenceException:
                        continue
                    finally:
                        self.driver.switch_to.default_content()
            except StaleElementReferenceException:
                continue
        return False

    def _get_select_by_id_with_retry(self, element_id: str, attempts: int = 3) -> Select:
        last_exc = None
        for _ in range(attempts):
            try:
                el = self.wait.until(EC.presence_of_element_located((By.ID, element_id)))
                self.wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, f"#{element_id} option")) > 1)
                return Select(el)
            except StaleElementReferenceException as e:
                last_exc = e
                continue
        raise last_exc if last_exc is not None else StaleElementReferenceException(
            f"Unable to locate stable select element #{element_id}"
        )

    def _select_option_fuzzy_with_retry(self, element_id: str, target_text: str, attempts: int = 3) -> bool:
        for _ in range(attempts):
            try:
                select = self._get_select_by_id_with_retry(element_id, attempts=1)
                return self._select_option_fuzzy(select, target_text)
            except StaleElementReferenceException:
                continue
        return False

    def _normalize_visible_text(self, text: str) -> str:
        if text is None:
            return ""
        text = text.replace("\u2013", "-").replace("\u2014", "-")
        return re.sub(r"\s+", " ", text).strip().upper()

    def _select_option_fuzzy(self, select: Select, target_text: str) -> bool:
        target_norm = self._normalize_visible_text(target_text)

        for option in select.options:
            if self._normalize_visible_text(option.text) == target_norm:
                select.select_by_visible_text(option.text)
                return True

        for option in select.options:
            opt_norm = self._normalize_visible_text(option.text)
            if target_norm and (target_norm in opt_norm or opt_norm in target_norm):
                select.select_by_visible_text(option.text)
                return True

        normalized_to_original = {}
        for option in select.options:
            opt_norm = self._normalize_visible_text(option.text)
            if opt_norm and opt_norm not in normalized_to_original:
                normalized_to_original[opt_norm] = option.text

        normalized_options = list(normalized_to_original.keys())
        close = difflib.get_close_matches(target_norm, normalized_options, n=3, cutoff=0.8)
        if close:
            chosen_norm = close[0]
            chosen_text = normalized_to_original[chosen_norm]
            logging.warning(
                "Office option '%s' not found exactly; selecting closest match '%s'",
                target_text,
                chosen_text,
            )
            select.select_by_visible_text(chosen_text)
            return True

        available = [opt.text for opt in select.options if (opt.text or "").strip()]
        logging.error(
            "Office option '%s' not found. Available Office options: %s",
            target_text,
            available[:50],
        )
        return False

    def setup_driver(self):
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-software-rasterizer")
        chrome_options.add_argument("--log-level=3")  # Suppress most logs
        
        # Auto-detect ChromeDriver path for Fly.io
        service = Service()
        self.driver = webdriver.Chrome(service=service, options=chrome_options)

    async def check_appointments(self):
        try:
            btn_value = "input[type='submit'][value='Next'], input[type='submit'][value='Weiter']"

            # Navigate to website
            self.driver.get(self.url)
            logging.info("Navigated to appointment website")

            # Step 1: Select Office (TEHERAN in this case)
            self.driver.switch_to.default_content()
            if not self._select_option_fuzzy_with_retry("Office", "TEHERAN"):  # BAKU
                return False, []
            logging.info("Selected office: TEHERAN")

            # Check for iframes
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            if iframes:
                logging.info("Iframes detected on page")

            # Click Next
            if not self._click_css_any_context(btn_value):
                raise StaleElementReferenceException("Failed to click Next button due to stale element")
            logging.info("Selected office and proceeded to next step")

            # Step 2: Select Visa Type
            visa_value = "13713913"
            visa_text = "Residence permit - NO STUDENTS / PUPILS but including dependents (spouses and children) of students"
            visa_select = self._get_select_by_id_with_retry("CalendarId")
            try:
                has_value = any((opt.get_attribute("value") == visa_value) for opt in visa_select.options)
            except StaleElementReferenceException:
                has_value = False

            if has_value:
                for _ in range(3):
                    try:
                        self._get_select_by_id_with_retry("CalendarId", attempts=1).select_by_value(visa_value)
                        break
                    except StaleElementReferenceException:
                        continue
                else:
                    return False, []
            else:
                if not self._select_option_fuzzy_with_retry(
                    "CalendarId",
                    visa_text,
                ): #Antrag Aufenthaltstitel / application permanent residence
                    return False, []
            logging.info("Selected visa type: Residence permit")

            # Click Next
            if not self._click_css_any_context(btn_value):
                raise StaleElementReferenceException("Failed to click Next button due to stale element")
            logging.info("Selected visa type and proceeded to next step")

            # Step 3: Click through pages
            if not self._click_css_any_context(btn_value):
                raise StaleElementReferenceException("Failed to click Next button due to stale element")
            logging.info("Proceeded through Number person page")

            if not self._click_css_any_context(btn_value):
                raise StaleElementReferenceException("Failed to click Next button due to stale element")
            logging.info("Proceeded through information page")

            # Step 4: Check for available appointments
            try:
                for _ in range(3):
                    try:
                        radio_buttons = self.wait.until(
                            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input[type='radio']"))
                        )

                        if not radio_buttons:
                            return False, []

                        available_times = []
                        for radio in radio_buttons:
                            radio_id = radio.get_attribute("id")
                            radio_value = radio.get_attribute("value")
                            label = self.driver.find_element(By.CSS_SELECTOR, f"label[for='{radio_id}']")
                            available_times.append(f"{label.text} on {radio_value}")

                        return True, available_times
                    except StaleElementReferenceException:
                        continue

                return False, []

            except TimeoutException:
                logging.info("No appointments found")
                return False, []

        except Exception as e:
            logging.error(f"Error during appointment check: {str(e)}")
            logging.info(f"Page source: {self.driver.page_source}")  # Log the page source
            self.driver.save_screenshot("error_screenshot.png")  # Take a screenshot
            return False, []
    
    def cleanup(self):
        self.driver.quit()

async def send_notification(available_times):
    message = "🚨 Available Appointments Found!\n\n"
    for time in available_times:
        message += f"📅 {time}\n"
    message += "\n🔗 Book here: https://appointment.bmeia.gv.at"
    
    try:
        await bot.send_message(CHAT_ID, message)
        logging.info("Notification sent successfully")
    except Exception as e:
        logging.error(f"Error sending notification: {str(e)}")

async def main():
    checker = AppointmentChecker()
    error_count = 0

    while True:
        try:
            has_appointments, available_times = await checker.check_appointments()

            if has_appointments:
                await send_notification(available_times)
                logging.info("Found appointments and sent notification")

            error_count = 0  # Reset error count on success

        except Exception as e:
            error_count += 1
            logging.error(f"Error in main loop: {str(e)}")

            if error_count >= 5:
                logging.info("Resetting driver due to multiple errors")
                checker.cleanup()
                checker = AppointmentChecker()
                error_count = 0

        await asyncio.sleep(120)  # Sleep for 15 minutes

# Flask App to Keep Bot Running
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

# Run Flask in a separate thread
def run_flask():
    app.run(host="0.0.0.0", port=8080)  # Fixed for Fly.io

if __name__ == "__main__":
    # Start Flask server in a separate thread
    threading.Thread(target=run_flask, daemon=True).start()

    # Create a new event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(main())  # Run the bot as a background task

    try:
        loop.run_forever()  # Keep the event loop running
    except KeyboardInterrupt:
        logging.info("Program terminated by user")
    except Exception as e:
        logging.error(f"Program crashed: {str(e)}")
