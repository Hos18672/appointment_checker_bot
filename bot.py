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
import warnings
from aiogram import Bot, Dispatcher, types

from dotenv import load_dotenv
import os
import sys

# Suppress deprecation warning for google.generativeai (still functional)
warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")

try:
    import google.generativeai as genai
    from PIL import Image
    GEMINI_AVAILABLE = True
except ImportError as e:
    GEMINI_AVAILABLE = False
    logging.warning(f"Gemini or PIL not installed. CAPTCHA solving will be disabled. Error: {e}")

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
                # Use a shorter timeout to fail faster
                short_wait = WebDriverWait(self.driver, 3)
                el = short_wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, css_selector)))
                el.click()
                return True
            except StaleElementReferenceException:
                continue
            except TimeoutException:
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

    def _format_date_for_form(self, date_str: str) -> str:
        # Accepts MM/DD/YYYY or M/D/YYYY or YYYY-MM-DD, returns DD.MM.YYYY
        if not date_str:
            return ""
        # Try MM/DD/YYYY
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", date_str)
        if m:
            mm, dd, yyyy = m.group(1), m.group(2), m.group(3)
            return f"{int(dd):02d}.{int(mm):02d}.{yyyy}"
        # Try YYYY-MM-DD
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", date_str)
        if m:
            yyyy, mm, dd = m.group(1), m.group(2), m.group(3)
            return f"{int(dd):02d}.{int(mm):02d}.{yyyy}"
        # If already in some other format, return as-is
        return date_str

    def _get_available_gemini_models(self) -> list:
        """Get list of available Gemini models that support generateContent."""
        try:
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                logging.error("GOOGLE_API_KEY environment variable not set")
                return []
            
            logging.info("Attempting to discover available Gemini models...")
            genai.configure(api_key=api_key)
            available = []
            
            try:
                for model in genai.list_models():
                    logging.debug(f"Found model: {model.name}, methods: {model.supported_generation_methods}")
                    if 'generateContent' in model.supported_generation_methods:
                        # Extract clean model name (e.g., "gemini-1.5-flash" from "models/gemini-1.5-flash")
                        model_name = model.name.replace("models/", "")
                        available.append(model_name)
                        logging.info(f"✓ Available model found: {model_name}")
            except Exception as e:
                logging.warning(f"Could not list models: {e}")
            
            # If discovery failed, use fallback list of common models
            if not available:
                logging.warning("No models found via discovery, using fallback list...")
                fallback_models = [
                    "gemini-2.0-flash",
                    "gemini-1.5-flash-latest",
                    "gemini-1.5-pro-latest",
                    "gemini-1.5-flash",
                    "gemini-pro-vision",
                ]
                logging.info(f"Using fallback models: {fallback_models}")
                return fallback_models
            
            logging.info(f"Total available models: {len(available)}")
            return available
        except Exception as e:
            logging.error(f"Error in model discovery: {e}")
            logging.warning("Falling back to default model list...")
            return ["gemini-1.5-flash", "gemini-pro-vision"]

    def _extract_captcha_text_gemini(self, image_path: str) -> str:
        """Extract CAPTCHA text from image using Google Gemini API (single attempt)."""
        logging.info(f"GEMINI_AVAILABLE status: {GEMINI_AVAILABLE}")
        if not GEMINI_AVAILABLE:
            logging.error("Gemini or PIL not available. Cannot extract CAPTCHA.")
            return ""
        
        try:
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                logging.error("GOOGLE_API_KEY environment variable not set")
                return ""
            
            logging.info("Configuring Gemini API...")
            genai.configure(api_key=api_key)
            
            if not os.path.exists(image_path):
                logging.error(f"CAPTCHA image not found at {image_path}")
                return ""
            
            logging.info(f"Opening image: {image_path}")
            image = Image.open(image_path)
            
            # Get available models dynamically
            logging.info("Getting available Gemini models...")
            available_models = self._get_available_gemini_models()
            if not available_models:
                logging.error("No available Gemini models found")
                return ""
            
            logging.info(f"Will try {len(available_models)} models: {available_models}")
            
            prompt = """Extract ALL text/characters from this CAPTCHA image.
Return ONLY the characters you see, nothing else.
No explanations, no formatting, just the raw text/characters."""
            
            last_error = None
            for model_name in available_models:
                try:
                    logging.info(f"Trying model: {model_name}")
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content([prompt, image])
                    extracted = response.text.strip()
                    logging.info(f"✓ CAPTCHA extracted using {model_name}: '{extracted}'")
                    return extracted
                except Exception as e:
                    last_error = e
                    logging.warning(f"✗ Model {model_name} failed: {e}")
                    continue
            
            # All models failed
            if last_error:
                logging.error(f"✗ All Gemini models failed. Last error: {last_error}")
            else:
                logging.error("✗ No models available to try")
            return ""
        except Exception as e:
            logging.error(f"✗ Fatal error extracting CAPTCHA with Gemini: {e}", exc_info=True)
            return ""

    def _refresh_captcha(self) -> bool:
        """Click the CAPTCHA refresh button to get a new CAPTCHA."""
        try:
            # Try to find and click the reload button
            reload_btn = self.driver.find_element(By.ID, "Captcha_ReloadLink")
            self.driver.execute_script("arguments[0].scrollIntoView(true);", reload_btn)
            reload_btn.click()
            logging.info("CAPTCHA refreshed")
            time.sleep(1)  # Wait for new CAPTCHA to load
            return True
        except Exception as e:
            logging.error(f"Failed to refresh CAPTCHA: {e}")
            return False

    def _verify_captcha_text(self, image_path: str, max_retries: int = 5) -> str:
        """Verify CAPTCHA text by extracting it twice and comparing results.
        If verification fails, refresh CAPTCHA and retry.
        Only returns text if both extractions match exactly.
        """
        attempt = 0
        while attempt < max_retries:
            attempt += 1
            try:
                logging.info(f"CAPTCHA verification attempt {attempt}/{max_retries}...")
                
                # First extraction
                text1 = self._extract_captcha_text_gemini(image_path)
                if not text1:
                    logging.warning(f"Attempt {attempt}: First CAPTCHA extraction failed or returned empty")
                    if attempt < max_retries:
                        if self._refresh_captcha():
                            # Take new screenshot after refresh
                            try:
                                captcha_elem = self.driver.find_element(By.ID, "Captcha_CaptchaImage")
                                time.sleep(0.5)
                                captcha_elem.screenshot(image_path)
                            except Exception as e:
                                logging.warning(f"Failed to re-screenshot CAPTCHA: {e}")
                        continue
                    else:
                        return ""
                
                logging.info(f"Attempt {attempt} - First extraction result: '{text1}'")
                
                # Small delay before second extraction
                time.sleep(0.5)
                
                # Second extraction
                text2 = self._extract_captcha_text_gemini(image_path)
                if not text2:
                    logging.warning(f"Attempt {attempt}: Second CAPTCHA extraction failed or returned empty")
                    if attempt < max_retries:
                        if self._refresh_captcha():
                            try:
                                captcha_elem = self.driver.find_element(By.ID, "Captcha_CaptchaImage")
                                time.sleep(0.5)
                                captcha_elem.screenshot(image_path)
                            except Exception as e:
                                logging.warning(f"Failed to re-screenshot CAPTCHA: {e}")
                        continue
                    else:
                        return ""
                
                logging.info(f"Attempt {attempt} - Second extraction result: '{text2}'")
                
                # Compare both extractions
                if text1.upper() == text2.upper():
                    logging.info(f"✓ CAPTCHA verification PASSED on attempt {attempt} - Both extractions match: '{text1}'")
                    return text1
                else:
                    logging.warning(f"✗ CAPTCHA verification FAILED on attempt {attempt} - Extractions do not match:")
                    logging.warning(f"  First:  '{text1}'")
                    logging.warning(f"  Second: '{text2}'")
                    
                    # Refresh and retry if not last attempt
                    if attempt < max_retries:
                        logging.info(f"Refreshing CAPTCHA and retrying... ({attempt}/{max_retries})")
                        if self._refresh_captcha():
                            try:
                                captcha_elem = self.driver.find_element(By.ID, "Captcha_CaptchaImage")
                                time.sleep(0.5)
                                captcha_elem.screenshot(image_path)
                            except Exception as e:
                                logging.warning(f"Failed to re-screenshot CAPTCHA: {e}")
                        continue
                    else:
                        return ""
            except Exception as e:
                logging.error(f"Error verifying CAPTCHA text on attempt {attempt}: {e}")
                if attempt < max_retries:
                    if self._refresh_captcha():
                        try:
                            captcha_elem = self.driver.find_element(By.ID, "Captcha_CaptchaImage")
                            time.sleep(0.5)
                            captcha_elem.screenshot(image_path)
                        except Exception as ex:
                            logging.warning(f"Failed to re-screenshot CAPTCHA: {ex}")
                    continue
                else:
                    return ""
        
        logging.error(f"CAPTCHA verification failed after {max_retries} attempts")
        return ""

    def fill_personal_form(self) -> bool:
        """Fill the personal data form using the hardcoded user data provided.
        Extracts and fills CAPTCHA using Gemini API, then takes a screenshot of the completed form.
        """
        try:
            import sys
            logging.info("=== FILL_PERSONAL_FORM STARTED ===")
            sys.stdout.flush()
            sys.stderr.flush()
            
            logging.info("Starting fill_personal_form()...")
            sys.stdout.flush()
            
            # Log available elements on page to debug form fields
            try:
                all_inputs = self.driver.find_elements(By.TAG_NAME, "input")
                logging.info(f"Found {len(all_inputs)} input elements on page")
                for inp in all_inputs[:10]:  # Log first 10 inputs
                    elem_id = inp.get_attribute("id")
                    elem_name = inp.get_attribute("name")
                    elem_type = inp.get_attribute("type")
                    logging.debug(f"  Input: id={elem_id}, name={elem_name}, type={elem_type}")
            except Exception as e:
                logging.warning(f"Could not log input elements: {e}")
            
            # Data provided by the user (dates in MM/DD/YYYY format for proper conversion)
            data = {
                "Lastname": "Rezaei",
                "Firstname": "Firouzeh",
                "DateOfBirth": "3/20/1996",
                "TraveldocumentNumber": "P06128950",
                "Sex": "2",  # Female -> value 2
                "Street": "Shora",
                "Postcode": "3313778468",
                "City": "Teheran",
                "Country": "102",  # IRAN, ISLAMIC REPUBLIC OF
                "Telephone": "+989963669985",
                "Email": "rezahosseiniafg@gmail.com",
                "LastnameAtBirth": "Rezaei",
                "NationalityAtBirth": "1",  # AFGHANISTAN
                "CountryOfBirth": "1",  # AFGHANISTAN
                "PlaceOfBirth": "Daikondi",
                "NationalityForApplication": "1",  # AFGHANISTAN
                "TraveldocumentDateOfIssue": "04/23/2024",
                "TraveldocumentValidUntil": "04/23/2029",
                "TraveldocumentIssuingAuthority": "1",  # AFGHANISTAN
            }

            # Wait for at least Lastname input to be present
            logging.info("Waiting for Lastname field to appear (10 second timeout)...")
            try:
                self.wait.until(EC.presence_of_element_located((By.ID, "Lastname")))
                logging.info("✓ Lastname field found - form page loaded")
            except TimeoutException:
                logging.error("✗ FORM PAGE TIMEOUT: Lastname field not found after 10 seconds")
                logging.error("Form page did not load after clicking Weiter button")
                # Log page source for debugging
                try:
                    page_source = self.driver.page_source
                    logging.error(f"Page source length: {len(page_source)} chars")
                    # Log first 500 chars of page to see what's there
                    logging.error(f"Page source preview: {page_source[:500]}")
                except Exception as e:
                    logging.error(f"Could not capture page source: {e}")
                return False

            # Fill text inputs
            elems = [
                ("Lastname", data["Lastname"]),
                ("Firstname", data["Firstname"]),
                ("DateOfBirth", self._format_date_for_form(data["DateOfBirth"])),
                ("TraveldocumentNumber", data["TraveldocumentNumber"]),
                ("Street", data["Street"]),
                ("Postcode", data["Postcode"]),
                ("City", data["City"]),
                ("Telephone", data["Telephone"]),
                ("Email", data["Email"]),
                ("LastnameAtBirth", data["LastnameAtBirth"]),
                ("PlaceOfBirth", data["PlaceOfBirth"]),
                ("TraveldocumentDateOfIssue", self._format_date_for_form(data["TraveldocumentDateOfIssue"])),
                ("TraveldocumentValidUntil", self._format_date_for_form(data["TraveldocumentValidUntil"])),
            ]

            for elem_id, value in elems:
                try:
                    el = self.driver.find_element(By.ID, elem_id)
                    el.clear()
                    el.send_keys(value)
                except Exception:
                    logging.exception(f"Failed to fill field {elem_id}")

            # Selects
            try:
                Select(self.driver.find_element(By.ID, "Sex")).select_by_value(data["Sex"])
            except Exception:
                logging.exception("Failed to select Sex")

            try:
                Select(self.driver.find_element(By.ID, "Country")).select_by_value(data["Country"])
            except Exception:
                logging.exception("Failed to select Country")

            # Nationality and birth country selects
            for sel_id, val in [("NationalityAtBirth", data["NationalityAtBirth"]),
                                ("CountryOfBirth", data["CountryOfBirth"]),
                                ("NationalityForApplication", data["NationalityForApplication"]),
                                ("TraveldocumentIssuingAuthority", data["TraveldocumentIssuingAuthority"])]:
                try:
                    Select(self.driver.find_element(By.ID, sel_id)).select_by_value(val)
                except Exception:
                    logging.exception(f"Failed to select {sel_id}")

            # Accept DSGVO checkbox if present
            try:
                # Use JS to make sure both checkbox and hidden input are set
                self.driver.execute_script("document.getElementById('DSGVOAccepted').checked = true;var h=document.querySelector('input[name=DSGVOAccepted][type=hidden]'); if(h) h.value='true';")
            except Exception:
                logging.exception("Failed to check DSGVOAccepted")

            # Extract and fill CAPTCHA using Gemini API with verification
            try:
                logging.info("Attempting to extract and verify CAPTCHA using Gemini API...")
                captcha_img_path = "captcha_screenshot.png"
                try:
                    captcha_elem = self.driver.find_element(By.ID, "Captcha_CaptchaImage")
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", captcha_elem)
                    time.sleep(0.5)
                    captcha_elem.screenshot(captcha_img_path)
                    logging.info(f"CAPTCHA screenshot saved to {captcha_img_path}")
                except Exception as e:
                    logging.warning(f"Could not screenshot CAPTCHA: {e}")
                    captcha_img_path = None
                
                # Extract and verify CAPTCHA text using dual-pass verification
                if captcha_img_path and os.path.exists(captcha_img_path):
                    captcha_text = self._verify_captcha_text(captcha_img_path)
                    
                    if captcha_text:
                        try:
                            captcha_input = self.driver.find_element(By.ID, "CaptchaText")
                            captcha_input.clear()
                            captcha_input.send_keys(captcha_text.upper())
                            logging.info(f"Filled CAPTCHA with verified text: {captcha_text}")
                        except Exception as e:
                            logging.error(f"Failed to fill CAPTCHA input: {e}")
                    else:
                        logging.warning("CAPTCHA verification failed - text extraction mismatch, leaving CAPTCHA blank")
                else:
                    logging.warning("Could not capture CAPTCHA image for extraction")
            except Exception as e:
                logging.error(f"Error processing CAPTCHA: {e}")

            # Take final screenshot of the filled form
            try:
                final_screenshot_path = "filled_form_with_captcha.png"
                self.driver.save_screenshot(final_screenshot_path)
                logging.info(f"Filled form screenshot saved to {final_screenshot_path}")
            except Exception as e:
                logging.error(f"Failed to take final screenshot: {e}")

            logging.info("Personal form filled with CAPTCHA. Ready for submission.")
            return True
        except Exception as e:
            logging.error(f"Error filling personal form: {e}")
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
            if not self._select_option_fuzzy_with_retry("Office", "BAKU"):  #  TEHERAN
                return False, []
            logging.info("Selected office: BAKU")

            # Check for iframes
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            if iframes:
                logging.info("Iframes detected on page")

            # Click Next
            if not self._click_css_any_context(btn_value):
                raise StaleElementReferenceException("Failed to click Next button due to stale element")
            logging.info("Selected office and proceeded to next step")

            # Step 2: Select Visa Type
            visa_value ="5539799"# "13713913"
            visa_text = "Antrag Aufenthaltstitel / application permanent residence" #"Residence permit - NO STUDENTS / PUPILS but including dependents (spouses and children) of students"
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
            logging.info("Step 4: Looking for available appointments (radio buttons)...")
            for attempt in range(3):
                try:
                    logging.info(f"Attempt {attempt + 1}/3 to find radio buttons...")
                    try:
                        radio_buttons = self.wait.until(
                            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input[type='radio']"))
                        )
                    except TimeoutException:
                        logging.warning(f"⏱ Timeout waiting for radio buttons on attempt {attempt + 1}")
                        if attempt == 2:
                            logging.warning("No appointments currently available")
                            return False, []
                        continue

                    logging.info(f"✓ Found {len(radio_buttons)} radio buttons")
                    if not radio_buttons:
                        logging.warning("No radio buttons found")
                        return False, []

                    # Click first available radio and proceed to next page
                    logging.info("Clicking first radio button...")
                    first_radio = radio_buttons[0]
                    
                    # Capture appointment details BEFORE any interactions (to avoid stale element)
                    appointment_details = "Selected first available appointment"
                    try:
                        radio_id = first_radio.get_attribute("id")
                        radio_value = first_radio.get_attribute("value")
                        label = self.driver.find_element(By.CSS_SELECTOR, f"label[for='{radio_id}']")
                        appointment_details = f"{label.text} on {radio_value}"
                        logging.info(f"Captured appointment: {appointment_details}")
                    except Exception as e:
                        logging.warning(f"Could not capture appointment details upfront: {e}")
                    
                    try:
                        self.driver.execute_script("arguments[0].scrollIntoView(true);", first_radio)
                        first_radio.click()
                        logging.info("✓ First radio button clicked via JavaScript")
                    except Exception as e:
                        logging.warning(f"JavaScript click failed, trying label click: {e}")
                        try:
                            # Try clicking label
                            r_id = first_radio.get_attribute('id')
                            lbl = self.driver.find_element(By.CSS_SELECTOR, f"label[for='{r_id}']")
                            lbl.click()
                            logging.info("✓ Radio button clicked via label")
                        except Exception as e2:
                            logging.error(f"Failed to click first radio: {e2}")

                    # Wait for page to respond after radio selection
                    logging.info("Waiting 3 seconds for page to respond after radio selection...")
                    time.sleep(3)

                    # Click Weiter to go to personal data page
                    logging.info("Clicking Weiter button to proceed to form...")
                    try:
                        weiter_clicked = self._click_css_any_context("input[type='submit'][value='Weiter']")
                    except TimeoutException as weiter_timeout:
                        logging.error(f"✗ TIMEOUT trying to click Weiter button: {weiter_timeout}")
                        logging.error("Weiter button not clickable after radio selection")
                        return False, []
                    
                    if not weiter_clicked:
                        # fallback strategies when normal click didn't work
                        try:
                            nb = self.driver.find_element(By.ID, "nextButton")
                            nb.click()
                            weiter_clicked = True
                            logging.info("✓ Clicked Weiter via nextButton ID")
                        except Exception as e:
                            logging.info(f"nextButton not found/clickable: {e}")
                            # Try clicking common submit selectors via JS (document + iframes)
                            try:
                                js_click = '''
                                (function(){
                                  var selectors = ['input[type=submit][value="Weiter"]','input[type=submit][value="Next"]','button[type=submit]'];
                                  for(var i=0;i<selectors.length;i++){
                                    var el=document.querySelector(selectors[i]);
                                    if(el){ el.click(); return true; }
                                  }
                                  var iframes = document.getElementsByTagName('iframe');
                                  for(var j=0;j<iframes.length;j++){
                                    try{
                                      var doc = iframes[j].contentDocument || iframes[j].contentWindow.document;
                                      for(var i=0;i<selectors.length;i++){
                                        var el = doc.querySelector(selectors[i]);
                                        if(el){ el.click(); return true; }
                                      }
                                    }catch(e){}
                                  }
                                  return false;
                                })();
                                '''
                                clicked = self.driver.execute_script(js_click)
                                if clicked:
                                    weiter_clicked = True
                                    logging.info("✓ Clicked Weiter via JS selector")
                                else:
                                    logging.info("JS selector click did not find element")
                            except Exception as e2:
                                logging.warning(f"JS click attempt failed: {e2}")

                            # If still not clicked, try submitting the enclosing form of the selected radio
                            if not weiter_clicked:
                                try:
                                    submitted = self.driver.execute_script(
                                        "return (function(el){var f = el.closest('form'); if(f){f.submit(); return true;} return false;})(arguments[0]);",
                                        first_radio,
                                    )
                                    if submitted:
                                        weiter_clicked = True
                                        logging.info("✓ Submitted enclosing form via JS")
                                except Exception as e3:
                                    logging.warning(f"Form submit attempt failed: {e3}")

                        if not weiter_clicked:
                            logging.error("Failed to click Weiter after selecting appointment using all fallbacks")
                            try:
                                self.driver.save_screenshot("weiter_click_failed.png")
                                logging.error("Saved screenshot: weiter_click_failed.png")
                            except Exception as e4:
                                logging.error(f"Failed to save screenshot: {e4}")

                            # Additional diagnostics: count matching selectors and save trimmed page HTML
                            try:
                                diag = self.driver.execute_script('''
                                    return {
                                      weiter_count: document.querySelectorAll('input[type=submit][value="Weiter"]').length,
                                      next_count: document.querySelectorAll('input[type=submit][value="Next"]').length,
                                      btn_count: document.querySelectorAll('button[type=submit]').length,
                                      radio_count: document.querySelectorAll('input[type=radio]').length,
                                      title: document.title || '',
                                      url: document.location.href || '',
                                      html_snippet: document.documentElement.outerHTML.slice(0,2000)
                                    };
                                ''')
                                logging.error(f"Weiter selector count: {diag.get('weiter_count')}, Next: {diag.get('next_count')}, buttons: {diag.get('btn_count')}, radios: {diag.get('radio_count')}")
                                logging.error(f"Page title: {diag.get('title')}")
                                logging.error(f"Page URL: {diag.get('url')}")
                                # Save HTML snippet to file for inspection
                                try:
                                    with open('weiter_click_page_preview.html', 'w', encoding='utf-8') as f:
                                        f.write(diag.get('html_snippet', ''))
                                    logging.error('Saved page preview: weiter_click_page_preview.html')
                                except Exception as e5:
                                    logging.error(f"Failed to save page preview: {e5}")
                            except Exception as e6:
                                logging.error(f"Failed to collect JS diagnostics: {e6}")
                    else:
                        logging.info("✓ Clicked Weiter button")

                    if not weiter_clicked:
                        logging.error("Failed to click Weiter button - cannot proceed to form")
                        return False, []
                    
                    # Wait for page navigation to complete and check for iframes
                    logging.info("Waiting for page navigation...")
                    time.sleep(2)
                    
                    # Check if we're in an iframe
                    try:
                        iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
                        if iframes:
                            logging.info(f"Found {len(iframes)} iframes on page")
                            for i, frame in enumerate(iframes):
                                try:
                                    self.driver.switch_to.frame(frame)
                                    logging.info(f"Switched to iframe {i}")
                                    break
                                except:
                                    pass
                        else:
                            self.driver.switch_to.default_content()
                            logging.info("No iframes, using default content")
                    except Exception as e:
                        logging.warning(f"Error checking iframes: {e}")
                        self.driver.switch_to.default_content()
                    
                    # Save screenshot to see what page we're on
                    try:
                        self.driver.save_screenshot("after_weiter_click.png")
                        logging.info("Screenshot saved: after_weiter_click.png")
                    except Exception as e:
                        logging.warning(f"Failed to save screenshot: {e}")
                    
                    # Log current page title and URL to debug
                    try:
                        logging.info(f"Current page title: {self.driver.title}")
                        logging.info(f"Current URL: {self.driver.current_url}")
                    except Exception as e:
                        logging.warning(f"Could not get page info: {e}")

                    # Wait for the form to appear and attempt to fill it
                    logging.info("Form page reached, attempting to fill personal form...")
                    
                    try:
                        filled = self.fill_personal_form()
                    except TimeoutException as form_timeout:
                        logging.error(f"✗ FORM TIMEOUT in fill_personal_form(): {form_timeout}")
                        logging.error("The form page did not load or Lastname field was not found")
                        return False, []
                    except Exception as form_error:
                        logging.error(f"✗ ERROR in fill_personal_form(): {form_error}", exc_info=True)
                        return False, []

                    # Use pre-captured appointment details
                    available_times = [appointment_details]
                    logging.info(f"✓ Appointment booked: {available_times[0]}")

                    return filled, available_times
                except StaleElementReferenceException as e:
                    logging.warning(f"Attempt {attempt + 1}: StaleElementReferenceException - {e}")
                    continue

            logging.warning("Failed to find radio buttons after 3 attempts")
            return False, []

        except Exception as e:
            logging.error(f"✗ Error during appointment check: {str(e)}", exc_info=True)
            try:
                self.driver.save_screenshot("error_screenshot.png")
                logging.error(f"Error screenshot saved to error_screenshot.png")
            except Exception as e2:
                logging.error(f"Could not save error screenshot: {e2}")
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
    """Single run of the appointment checker. Exits after one check."""
    logging.info("=== APPOINTMENT CHECKER STARTED ===")
    checker = AppointmentChecker()
    
    try:
        has_appointments, available_times = await checker.check_appointments()

        if has_appointments:
            await send_notification(available_times)
            logging.info("✓ Found appointments and sent notification")
            return 0
        else:
            logging.info("No appointments available this run")
            return 1

    except Exception as e:
        logging.error(f"✗ Error during appointment check: {str(e)}", exc_info=True)
        return 2
    
    finally:
        logging.info("Cleaning up...")
        checker.cleanup()
        logging.info("=== APPOINTMENT CHECKER FINISHED ===")

# Flask app removed - not needed for scheduled GitHub Actions runs

if __name__ == "__main__":
    # Create a new event loop and run main() once
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        exit_code = loop.run_until_complete(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logging.info("Program terminated by user")
        sys.exit(130)
    except Exception as e:
        logging.error(f"Program crashed: {str(e)}", exc_info=True)
        sys.exit(1)
