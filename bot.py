from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
import time
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from flask import Flask
import threading

# Configure logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(levelname)s - %(message)s',
                   filename='appointment_checker.log')

# Telegram Bot Setup
TOKEN = "7440542620:AAETXXQdnWB1sxff7dZytowRMup67BUBQWs"
CHAT_ID = "1176238554"
bot = Bot(token=TOKEN)
dp = Dispatcher(bot=bot)  # Corrected Dispatcher initialization

class AppointmentChecker:
    def __init__(self):
        self.url = "https://appointment.bmeia.gv.at"
        self.setup_driver()
        self.wait = WebDriverWait(self.driver, 10)  # 10-second timeout

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
            office_select = Select(self.wait.until(
                EC.presence_of_element_located((By.ID, "Office"))
            ))
            office_select.select_by_visible_text("TEHERAN")
            logging.info("Selected office: TEHERAN")

            # Check for iframes
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            if iframes:
                logging.info("Switching to iframe")
                self.driver.switch_to.frame(iframes[0])  # Switch to the first iframe

            # Click Next
            next_button = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, btn_value)))
            next_button.click()
            logging.info("Selected office and proceeded to next step")

            # Step 2: Select Visa Type
            visa_select = Select(self.wait.until(
                EC.presence_of_element_located((By.ID, "CalendarId"))
            ))
            visa_select.select_by_value("13713913")  # Residence permit
            logging.info("Selected visa type: Residence permit")

            # Click Next
            next_button = self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, btn_value)))
            next_button.click()
            logging.info("Selected visa type and proceeded to next step")

            # Step 3: Click through pages
            self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, btn_value))).click()
            logging.info("Proceeded through Number person page")

            self.wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, btn_value))).click()
            logging.info("Proceeded through information page")

            # Step 4: Check for available appointments
            try:
                radio_buttons = self.wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input[type='radio']")))

                if radio_buttons:
                    available_times = []
                    for radio in radio_buttons:
                        label = self.driver.find_element(By.CSS_SELECTOR, f"label[for='{radio.get_attribute('id')}']")
                        available_times.append(f"{label.text} on {radio.get_attribute('value')}")

                    return True, available_times
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

        await asyncio.sleep(900)  # Sleep for 30 minutes

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
