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
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InputFile, FSInputFile, Message
from aiogram.filters import Command

from dotenv import load_dotenv
import os
import sys

warnings.filterwarnings("ignore", category=FutureWarning, module="google.generativeai")

try:
    import google.generativeai as genai
    from PIL import Image
    GEMINI_AVAILABLE = True
except ImportError as e:
    GEMINI_AVAILABLE = False
    logging.warning(f"Gemini or PIL not installed. CAPTCHA solving will be disabled. Error: {e}")

load_dotenv()

logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(levelname)s - %(message)s')

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
bot = Bot(token=TOKEN)
dp = Dispatcher()

TEST_FILL_ONLY_CAPTCHA = os.getenv("TEST_FILL_ONLY_CAPTCHA", "false").lower() in ("1", "true", "yes")

checker_instance = None
main_loop = None

# ─── Polling interval in seconds ───
CHECK_INTERVAL_SECONDS = 30  # 30 seconds between checks when no appointment is found


def parse_and_format_date(raw: str) -> str:
    if not raw or not raw.strip():
        return ""

    raw = raw.strip()
    day = month = year = None

    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", raw)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))

    if day is None:
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", raw)
        if m:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))

    if day is None:
        m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", raw)
        if m:
            a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if a > 12:
                day, month = a, b
            elif b > 12:
                month, day = a, b
            else:
                day, month = a, b

    if day is None:
        m = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", raw)
        if m:
            a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if a > 12:
                day, month = a, b
            elif b > 12:
                month, day = a, b
            else:
                day, month = a, b

    if day is None or month is None or year is None:
        raise ValueError(f"Cannot parse date '{raw}' – unrecognised format")

    if not (1 <= month <= 12):
        raise ValueError(
            f"Invalid month {month} in date '{raw}'. "
            f"Parsed day={day}, month={month}, year={year}."
        )
    if not (1 <= day <= 31):
        raise ValueError(
            f"Invalid day {day} in date '{raw}'. "
            f"Parsed day={day}, month={month}, year={year}."
        )

    days_in_month = {
        1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30,
        7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31,
    }
    if day > days_in_month.get(month, 31):
        raise ValueError(f"Day {day} is too large for month {month} in date '{raw}'.")

    formatted = f"{day:02d}.{month:02d}.{year}"
    if formatted != raw:
        logging.info(f"Date converted: '{raw}' → '{formatted}'")
    return formatted


class AppointmentChecker:
    def __init__(self):
        self.url = "https://appointment.bmeia.gv.at"
        self.setup_driver()
        self.wait = WebDriverWait(self.driver, 10)
        self.screenshot_path = "filled_form_with_captcha.png"
        self.confirmation_screenshot_path = "confirmation_page.png"
        self.manual_captcha_queue = asyncio.Queue()
        self.waiting_for_manual_captcha = False
        self.current_person_index = 0
        # ─── Track which persons have been booked ───
        self.persons_booked = []  # list of booleans, one per person
        self.booking_results = []  # store results per person
        self.check_count = 0  # how many polling cycles so far

    # ─── PERSONAL DATA FOR PERSON 1 ──────────────────────────────────────
    PERSONAL_DATA_Test = {
        "Lastname":                       "Rez1",
        "Firstname":                      "Foru",
        "DateOfBirth":                    "3/24/1998",
        "TraveldocumentNumber":           "A05526751",
        "Sex":                            "2",
        "Street":                         "Taster Street 123",
        "Postcode":                       "1312378458",
        "City":                           "Teheran",
        "Country":                        "102",
        "Telephone":                      "+989933664545",
        "Email":                          "rezahosseiniafg3@gmail.com",
        "LastnameAtBirth":                "Rez1",
        "NationalityAtBirth":             "1",
        "CountryOfBirth":                 "1",
        "PlaceOfBirth":                   "Teheran",
        "NationalityForApplication":      "1",
        "TraveldocumentDateOfIssue":      "02/13/2022",
        "TraveldocumentValidUntil":       "02/13/2030",
        "TraveldocumentIssuingAuthority": "1",
    }

    PERSONAL_DATA_1 = {
        "Lastname":                       "Rezaei",
        "Firstname":                      "Firouzeh",
        "DateOfBirth":                    "3/20/1996",
        "TraveldocumentNumber":           "P06128950",
        "Sex":                            "2",
        "Street":                         "Shora",
        "Postcode":                       "3313778468",
        "City":                           "Teheran",
        "Country":                        "102",
        "Telephone":                      "+989963669985",
        "Email":                          "rezahosseiniafg@gmail.com",
        "LastnameAtBirth":                "Rezaei",
        "NationalityAtBirth":             "1",
        "CountryOfBirth":                 "102",
        "PlaceOfBirth":                   "Teheran",
        "NationalityForApplication":      "1",
        "TraveldocumentDateOfIssue":      "04/23/2024",
        "TraveldocumentValidUntil":       "04/23/2029",
        "TraveldocumentIssuingAuthority": "1",
    }

    # ─── PERSONAL DATA FOR PERSON 2 ──────────────────────────────────────
    PERSONAL_DATA_2 = {
        "Lastname":                       "AHMADI",
        "Firstname":                      "RAZIA",
        "DateOfBirth":                    "18/05/1998",
        "TraveldocumentNumber":           "P06128382",
        "Sex":                            "2",       
        "Street":                         "Shahid Ali Koohkhil St, Plack 0",
        "Postcode":                       "3161679743",
        "City":                           "Alborz",
        "Country":                        "102",
        "Telephone":                      "+989020656955",
        "Email":                          "hassan.nazary@gmx.at",
        "LastnameAtBirth":                "AHMADI",
        "NationalityAtBirth":             "1",
        "CountryOfBirth":                 "1",
        "PlaceOfBirth":                   "Daykundi",
        "NationalityForApplication":      "1",
        "TraveldocumentDateOfIssue":      "18/03/2024",
        "TraveldocumentValidUntil":       "18/03/2029",
        "TraveldocumentIssuingAuthority": "1",
    }

    ALL_PERSONS = [PERSONAL_DATA_1, PERSONAL_DATA_2]

    def _get_person_label(self, index: int = None) -> str:
        if index is None:
            index = self.current_person_index
        data = self.ALL_PERSONS[index]
        return f"Person {index + 1} ({data['Firstname']} {data['Lastname']})"

    def _all_persons_booked(self) -> bool:
        """Return True when every person has been successfully booked."""
        return len(self.persons_booked) == len(self.ALL_PERSONS) and all(self.persons_booked)

    def _get_unbooked_indices(self) -> list:
        """Return list of person indices that still need booking."""
        unbooked = []
        for i in range(len(self.ALL_PERSONS)):
            if i >= len(self.persons_booked) or not self.persons_booked[i]:
                unbooked.append(i)
        return unbooked

    async def _request_manual_captcha(self, captcha_image_path: str) -> str:
        self.waiting_for_manual_captcha = True

        while not self.manual_captcha_queue.empty():
            try:
                self.manual_captcha_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        try:
            person_label = self._get_person_label()
            msg = (
                f"🤖 Automatic CAPTCHA solving failed for {person_label}.\n\n"
                "Please look at the CAPTCHA image and send me the code.\n"
                "Format: Just send the letters/numbers you see (e.g., 'ABC123')\n"
                "⏰ You have 1 minutes to respond."
            )
            await bot.send_message(CHAT_ID, msg)

            sent_image = False
            if os.path.exists(captcha_image_path):
                try:
                    photo = FSInputFile(captcha_image_path)
                    await bot.send_photo(CHAT_ID, photo, caption="👆 Enter this CAPTCHA code")
                    sent_image = True
                except Exception as e:
                    logging.error(f"Failed to send CAPTCHA image: {e}")

            if not sent_image and os.path.exists(self.screenshot_path):
                try:
                    photo = FSInputFile(self.screenshot_path)
                    await bot.send_photo(CHAT_ID, photo,
                                         caption="👆 CAPTCHA visible in form. Please send the code.")
                except Exception as e:
                    logging.error(f"Failed to send fallback screenshot: {e}")

            logging.info("Waiting for manual CAPTCHA input (max 2 min)...")

            try:
                manual_code = await asyncio.wait_for(self.manual_captcha_queue.get(), timeout=120)
                logging.info(f"Received manual CAPTCHA: {manual_code}")
                return manual_code.strip().upper()
            except asyncio.TimeoutError:
                logging.error("Timeout waiting for manual CAPTCHA input")
                await bot.send_message(CHAT_ID, "⏰ Timeout! No CAPTCHA received in 1 minutes.")
                return ""

        except Exception as e:
            logging.error(f"Error requesting manual CAPTCHA: {e}")
            return ""
        finally:
            self.waiting_for_manual_captcha = False

    def receive_manual_captcha(self, captcha_code: str):
        global main_loop
        if self.waiting_for_manual_captcha and main_loop:
            asyncio.run_coroutine_threadsafe(
                self.manual_captcha_queue.put(captcha_code), main_loop
            )
            return True
        return False

    # ─── FORM ERROR ANALYSIS ─────────────────────────────────────────────

    def _get_all_form_errors(self) -> dict:
        result = {
            "captcha_errors": [],
            "field_errors": [],
            "general_errors": [],
            "raw_errors": [],
        }

        all_error_texts: list[str] = []

        noise_patterns = [
            r"^[!\*\?\.\,\;\:\-\_\#\+]$",
            r"^nachname$", r"^vorname$", r"^geburtsdatum$",
            r"^reisepass\s*nr\.?$", r"^geschlecht$", r"^stra[sß]e$",
            r"^postleitzahl$", r"^plz$", r"^ort$", r"^stadt$", r"^land$",
            r"^telefon$", r"^e-?mail$", r"^geburtsname$",
            r"^staatsangeh[öo]rigkeit", r"^geburtsland$", r"^geburtsort$",
            r"^ausstellungsdatum$", r"^g[üu]ltig\s*bis$",
            r"^ausstellende\s*beh[öo]rde$", r"^reisedokument",
            r"^sicherheitscode$", r"^captcha$",
            r"^last\s*name$", r"^first\s*name$", r"^date\s*of\s*birth$",
            r"^passport\s*(no\.?|number)$", r"^sex$", r"^gender$",
            r"^street$", r"^postal\s*code$", r"^zip\s*code$", r"^city$",
            r"^country$", r"^telephone$", r"^phone$", r"^email$",
            r"^place\s*of\s*birth$", r"^nationality$",
            r"^issuing\s*authority$", r"^valid\s*until$",
            r"^date\s*of\s*issue$", r"^security\s*code$",
            r"^\(z\.?b\.?\s*\d", r"^\(e\.?g\.?\s*\d",
            r"^dd\.mm\.yyyy$", r"^tt\.mm\.jjjj$",
            r"^anzahl\s*der\s*personen\s*\d",
            r"^number\s*of\s*persons\s*\d",
            r"^startzeit\s", r"^start\s*time\s",
            r"^termin\s", r"^appointment\s",
            r"^weiter$", r"^next$", r"^zur[üu]ck$", r"^back$",
            r"^submit$", r"^abschicken$",
            r"^\*+$", r"^\s*$",
        ]

        def _is_noise(text: str) -> bool:
            if not text or len(text.strip()) == 0:
                return True
            stripped = text.strip()
            if len(stripped) <= 2 and not stripped.isalnum():
                return True
            lower = stripped.lower()
            for pattern in noise_patterns:
                if re.match(pattern, lower):
                    return True
            return False

        def _looks_like_real_error(text: str) -> bool:
            lower = text.strip().lower()
            error_indicators = [
                "fehlt", "fehlerhaft", "ungültig", "erforderlich", "stimmt nicht",
                "nicht überein", "ist nicht gültig", "bitte geben", "bitte wählen",
                "pflichtfeld", "muss ausgefüllt", "darf nicht leer",
                "is required", "is not valid", "is invalid", "does not match",
                "is incorrect", "cannot be empty", "must be", "please enter",
                "please select", "missing", "erroneous", "error", "failed",
                "validation", "not match",
                "text aus dem bild", "text from the picture",
                "folgende angaben fehlen", "following information is missing",
            ]
            return any(indicator in lower for indicator in error_indicators)

        try:
            for sel in [".validation-summary-errors li", "div.validation-summary-errors ul li"]:
                for el in self.driver.find_elements(By.CSS_SELECTOR, sel):
                    txt = el.text.strip()
                    if txt and txt not in all_error_texts and not _is_noise(txt):
                        all_error_texts.append(txt)
            try:
                for container in self.driver.find_elements(By.CSS_SELECTOR, ".validation-summary-errors"):
                    full_text = container.text.strip()
                    if full_text and _looks_like_real_error(full_text):
                        for line in full_text.splitlines():
                            line = line.strip()
                            if (line and line not in all_error_texts
                                    and not _is_noise(line) and _looks_like_real_error(line)):
                                all_error_texts.append(line)
            except Exception:
                pass
        except Exception:
            pass

        try:
            for sel in ["span.field-validation-error", ".field-validation-error"]:
                for el in self.driver.find_elements(By.CSS_SELECTOR, sel):
                    txt = el.text.strip()
                    if txt and txt not in all_error_texts and not _is_noise(txt):
                        all_error_texts.append(txt)
        except Exception:
            pass

        try:
            for sel in [".alert-danger", ".alert-error"]:
                for el in self.driver.find_elements(By.CSS_SELECTOR, sel):
                    txt = el.text.strip()
                    if (txt and txt not in all_error_texts
                            and not _is_noise(txt) and _looks_like_real_error(txt)):
                        all_error_texts.append(txt)
        except Exception:
            pass

        try:
            error_inputs = self.driver.find_elements(
                By.CSS_SELECTOR,
                "input.input-validation-error, select.input-validation-error"
            )
            for inp in error_inputs:
                field_name = inp.get_attribute("name") or inp.get_attribute("id") or "unknown-field"
                field_val = inp.get_attribute("value") or "(empty)"
                msg = f"Field '{field_name}' has validation error (current value: '{field_val}')"
                if msg not in all_error_texts:
                    all_error_texts.append(msg)
        except Exception:
            pass

        result["raw_errors"] = list(all_error_texts)

        captcha_keywords = [
            "captcha", "sicherheitscode", "security code", "verification code",
            "text from the picture", "text aus dem bild",
            "bild stimmt nicht", "does not match",
            "code is incorrect", "code is invalid",
            "stimmt nicht mit ihrer eingabe", "nicht überein", "captchatext",
        ]
        captcha_field_patterns = [
            r"field\s*'?\s*captcha", r"captcha.*validation\s*error", r"captchatext",
        ]
        field_keywords = [
            "is not valid", "is required", "ist erforderlich", "fehlt",
            "missing", "erroneous", "invalid", "ungültig", "pflichtfeld",
            "muss ausgefüllt", "darf nicht leer", "bitte geben", "bitte wählen",
            "please enter", "please select", "cannot be empty",
        ]

        for txt in all_error_texts:
            lower = txt.lower()
            is_captcha = any(kw in lower for kw in captcha_keywords)
            if not is_captcha:
                for pat in captcha_field_patterns:
                    if re.search(pat, lower):
                        is_captcha = True
                        break

            if is_captcha:
                result["captcha_errors"].append(txt)
            elif any(kw in lower for kw in field_keywords):
                if "captcha" not in lower and "captchatext" not in lower:
                    result["field_errors"].append(txt)
                else:
                    result["captcha_errors"].append(txt)
            else:
                if _looks_like_real_error(txt):
                    result["general_errors"].append(txt)

        return result

    def _is_only_captcha_error(self, errors: dict) -> bool:
        return (
            bool(errors["captcha_errors"])
            and not errors["field_errors"]
            and not errors["general_errors"]
        )

    def _analyse_and_log_errors(self) -> dict:
        errors = self._get_all_form_errors()
        if errors["raw_errors"]:
            logging.error("═══ FORM ERRORS DETECTED ═══")
            for i, e in enumerate(errors["raw_errors"], 1):
                logging.error(f"  [{i}] {e}")
            if errors["captcha_errors"]:
                logging.error(f"  → CAPTCHA errors: {errors['captcha_errors']}")
            if errors["field_errors"]:
                logging.error(f"  → Field errors: {errors['field_errors']}")
            if errors["general_errors"]:
                logging.error(f"  → General errors: {errors['general_errors']}")
            if self._is_only_captcha_error(errors):
                logging.info("  ✓ ONLY CAPTCHA errors — will retry")
            logging.error("═══════════════════════════")
        else:
            logging.info("No visible form errors found on page.")
        return errors

    def _has_date_field_errors(self, errors: dict) -> bool:
        date_fields = [
            "dateofbirth", "traveldocumentdateofissue", "traveldocumentvaliduntil",
            "date of birth", "date of issue", "valid until",
            "datum", "geburtsdatum", "ausstellungsdatum", "gültig bis",
        ]
        for txt in errors["field_errors"] + errors["general_errors"]:
            lower = txt.lower()
            if any(kw in lower for kw in date_fields):
                return True
        return False

    # ─── CAPTCHA SUBMISSION ──────────────────────────────────────────────

    async def _submit_form_with_captcha_handling(self, max_auto_attempts: int = 5) -> tuple:
        captcha_retry_count = 0
        max_captcha_retries = 5
        auto_attempts_failed = 0
        max_total_attempts = max_auto_attempts + 5
        attempt = 0

        while attempt < max_total_attempts:
            attempt += 1
            person_label = self._get_person_label()
            logging.info(f"=== FORM SUBMISSION ATTEMPT {attempt} for {person_label} ===")

            if auto_attempts_failed >= max_auto_attempts:
                logging.info("Switching to manual CAPTCHA input...")
                self._refresh_captcha()
                time.sleep(2)

                manual_captcha_path = "captcha_for_manual.png"
                if not self._capture_captcha_screenshot(manual_captcha_path):
                    manual_captcha_path = self.screenshot_path

                manual_code = await self._request_manual_captcha(manual_captcha_path)
                if not manual_code:
                    return False, "Timeout waiting for manual CAPTCHA input", None

                try:
                    captcha_input = self.driver.find_element(By.ID, "CaptchaText")
                    captcha_input.clear()
                    time.sleep(0.3)
                    captcha_input.send_keys(manual_code)
                except Exception as e:
                    return False, f"Failed to fill manual CAPTCHA: {e}", None
            else:
                logging.info(f"Automatic CAPTCHA attempt {auto_attempts_failed + 1}/{max_auto_attempts}")
                captcha_img_path = f"captcha_auto_{auto_attempts_failed}.png"
                if not self._capture_captcha_screenshot(captcha_img_path):
                    auto_attempts_failed += 1
                    if auto_attempts_failed >= max_auto_attempts:
                        continue
                    self._refresh_captcha()
                    time.sleep(2)
                    continue

                captcha_text = self._verify_captcha_text(captcha_img_path, max_retries=2)
                if not captcha_text:
                    auto_attempts_failed += 1
                    if auto_attempts_failed >= max_auto_attempts:
                        continue
                    self._refresh_captcha()
                    time.sleep(2)
                    continue

                try:
                    captcha_input = self.driver.find_element(By.ID, "CaptchaText")
                    captcha_input.clear()
                    time.sleep(0.3)
                    captcha_input.send_keys(captcha_text)
                except Exception as e:
                    auto_attempts_failed += 1
                    continue

            try:
                self.driver.save_screenshot(self.screenshot_path)
            except Exception:
                pass

            initial_url = self.driver.current_url
            if not self._click_submit_button():
                return False, "Failed to click submit button", None

            time.sleep(5)

            current_url = self.driver.current_url
            url_changed = current_url != initial_url
            form_still_present = self._check_for_form_on_page()
            errors = self._analyse_and_log_errors()
            has_any_error = bool(errors["raw_errors"])
            only_captcha = self._is_only_captcha_error(errors)
            has_field_errors = bool(errors["field_errors"])
            is_confirmation, confirmation_text = self._check_for_confirmation_page()

            # CASE 1: Field errors
            if has_field_errors:
                error_report = self._build_error_report(errors)
                try:
                    await bot.send_message(CHAT_ID, f"❌ {person_label}: Field errors:\n\n{error_report}")
                except Exception:
                    pass
                return False, error_report, None

            # CASE 2: CAPTCHA error only → retry
            if only_captcha and form_still_present:
                captcha_retry_count += 1
                logging.warning(f"CAPTCHA error – retry {captcha_retry_count}/{max_captcha_retries}")

                if captcha_retry_count > max_captcha_retries:
                    auto_attempts_failed = max_auto_attempts
                    try:
                        await bot.send_message(CHAT_ID,
                            f"⚠️ {person_label}: CAPTCHA failed {max_captcha_retries}x. Manual mode...")
                    except Exception:
                        pass
                    attempt -= 1
                    continue

                try:
                    self.driver.find_element(By.ID, "CaptchaText").clear()
                except Exception:
                    pass

                if not self._refresh_captcha():
                    return False, "Failed to refresh CAPTCHA", None
                time.sleep(2)

                retry_path = f"captcha_retry_{captcha_retry_count}.png"
                if not self._capture_captcha_screenshot(retry_path):
                    auto_attempts_failed += 1
                    continue

                new_text = self._verify_captcha_text(retry_path, max_retries=2)
                if not new_text:
                    auto_attempts_failed += 1
                    continue

                try:
                    ci = self.driver.find_element(By.ID, "CaptchaText")
                    ci.clear()
                    time.sleep(0.3)
                    ci.send_keys(new_text)
                except Exception:
                    auto_attempts_failed += 1
                    continue

                if not self._click_submit_button():
                    return False, "Failed to click submit on CAPTCHA retry", None
                time.sleep(5)

                current_url = self.driver.current_url
                url_changed = current_url != initial_url
                form_still_present = self._check_for_form_on_page()
                errors = self._analyse_and_log_errors()
                only_captcha = self._is_only_captcha_error(errors)
                has_field_errors = bool(errors["field_errors"])
                is_confirmation, confirmation_text = self._check_for_confirmation_page()

                if only_captcha and form_still_present:
                    try:
                        self.driver.find_element(By.ID, "CaptchaText").clear()
                    except Exception:
                        pass
                    continue

                if has_field_errors:
                    return False, self._build_error_report(errors), None

                if (is_confirmation or url_changed) and not form_still_present:
                    try:
                        self.driver.save_screenshot(self.confirmation_screenshot_path)
                    except Exception:
                        pass
                    return True, confirmation_text or "Appointment confirmed", self.confirmation_screenshot_path

                if form_still_present and not bool(errors["raw_errors"]):
                    continue

                if bool(errors["raw_errors"]):
                    return False, self._build_error_report(errors), None
                continue

            # CASE 3: Confirmation
            if (is_confirmation or url_changed) and not form_still_present:
                try:
                    self.driver.save_screenshot(self.confirmation_screenshot_path)
                except Exception:
                    pass
                return True, confirmation_text or "Appointment confirmed", self.confirmation_screenshot_path

            # CASE 4: Unknown errors
            if form_still_present and has_any_error:
                error_report = self._build_error_report(errors)
                try:
                    await bot.send_message(CHAT_ID, f"⚠️ {person_label}: Unknown errors:\n\n{error_report}")
                except Exception:
                    pass
                return False, error_report, None

            if form_still_present and not has_any_error:
                auto_attempts_failed += 1
                self._refresh_captcha()
                time.sleep(2)
                continue

            if url_changed and not form_still_present:
                try:
                    self.driver.save_screenshot(self.confirmation_screenshot_path)
                except Exception:
                    pass
                return True, "Page changed (appointment likely confirmed)", self.confirmation_screenshot_path

        return False, f"Failed after {max_total_attempts} attempts", None

    def _build_error_report(self, errors: dict) -> str:
        lines = []
        if errors["field_errors"]:
            lines.append("📋 FIELD ERRORS:")
            for e in errors["field_errors"]:
                lines.append(f"  • {e}")
                s = self._suggest_fix(e)
                if s:
                    lines.append(f"    💡 {s}")
        if errors["captcha_errors"]:
            lines.append("\n🔒 CAPTCHA ERRORS:")
            for e in errors["captcha_errors"]:
                lines.append(f"  • {e}")
        if errors["general_errors"]:
            lines.append("\n⚠️ OTHER ERRORS:")
            for e in errors["general_errors"]:
                lines.append(f"  • {e}")
        return "\n".join(lines) if lines else "Unknown error"

    def _suggest_fix(self, error_text: str) -> str:
        lower = error_text.lower()
        if "is not valid for" in lower and ("date" in lower or "traveldocument" in lower):
            return "Expected format: DD.MM.YYYY (e.g. 20.02.2024)."
        if "is required" in lower or "ist erforderlich" in lower:
            return "Field is required. Check PERSONAL_DATA."
        if "email" in lower:
            return "Check email format."
        if "telephone" in lower or "telefon" in lower:
            return "Check phone format (e.g. +43123456789)."
        return ""

    def _click_submit_button(self) -> bool:
        for selector in [
            "input[type='submit'][value='Weiter']",
            "input[type='submit'][value='Next']",
            "input[type='submit'][value='Submit']",
            "button[type='submit']", "#btnSubmit",
        ]:
            try:
                btn = self.driver.find_element(By.CSS_SELECTOR, selector)
                if btn.is_displayed():
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                    time.sleep(0.5)
                    btn.click()
                    return True
            except NoSuchElementException:
                continue
            except Exception:
                continue
        try:
            self.driver.execute_script("document.querySelector('form').submit();")
            return True
        except Exception:
            return False

    def _check_for_form_on_page(self) -> bool:
        try:
            for by, value in [(By.ID, "Lastname"), (By.ID, "Firstname"), (By.ID, "CaptchaText")]:
                try:
                    if self.driver.find_element(by, value).is_displayed():
                        return True
                except Exception:
                    continue
            return False
        except Exception:
            return True

    def _check_for_confirmation_page(self) -> tuple:
        try:
            page_source = self.driver.page_source.lower()
            page_title = self.driver.title.lower()
            indicators = [
                "bestätigung", "termin gebucht", "appointment booked",
                "erfolgreich gebucht", "buchung erfolgreich", "booking successful",
                "vielen dank für ihre buchung", "thank you for your booking",
                "referenznummer", "reference number", "buchungsnummer",
                "booking number", "ihr termin wurde", "termin bestätigt",
                "appointment confirmed", "successfully registered",
                "erfolgreich registriert",
            ]
            found = [i for i in indicators if i in page_source or i in page_title]
            is_conf = len(found) > 0
            conf_text = ""
            if is_conf:
                for pat in [r"(?:referenznummer|reference number|buchungsnummer|booking number)[\s:]*([a-z0-9\-]+)"]:
                    match = re.search(pat, page_source, re.IGNORECASE)
                    if match:
                        conf_text = f"Confirmation: {match.group(1).upper()}"
                        break
                if not conf_text:
                    conf_text = f"Confirmed (indicators: {', '.join(found)})"
            return is_conf, conf_text
        except Exception:
            return False, ""

    # ─── CAPTCHA HELPERS ─────────────────────────────────────────────────

    def _refresh_captcha(self) -> bool:
        try:
            refresh_selectors = [
                (By.ID, "Captcha_ReloadLink"),
                (By.CLASS_NAME, "BDC_ReloadLink"),
                (By.CSS_SELECTOR, "a[id*='ReloadLink']"),
                (By.CSS_SELECTOR, "a[title*='Change the CAPTCHA']"),
                (By.CSS_SELECTOR, "#Captcha_CaptchaIconsDiv a:first-child"),
                (By.XPATH, "//a[contains(@id, 'ReloadLink')]"),
                (By.XPATH, "//a[contains(@title, 'Change')]"),
                (By.XPATH, "//a[contains(@title, 'CAPTCHA')]"),
                (By.XPATH, "//img[contains(@id, 'ReloadIcon')]/parent::a"),
            ]
            reload_btn = None
            for by, sel in refresh_selectors:
                try:
                    reload_btn = self.driver.find_element(by, sel)
                    if reload_btn.is_displayed():
                        break
                except Exception:
                    continue
            if not reload_btn:
                return False

            old_src = None
            try:
                old_src = self.driver.find_element(By.ID, "Captcha_CaptchaImage").get_attribute("src")
            except Exception:
                pass

            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", reload_btn)
            time.sleep(0.5)
            self.driver.execute_script("arguments[0].click();", reload_btn)

            if old_src:
                for i in range(10):
                    time.sleep(0.5)
                    try:
                        new_src = self.driver.find_element(By.ID, "Captcha_CaptchaImage").get_attribute("src")
                        if new_src != old_src:
                            time.sleep(1.5)
                            return True
                    except Exception:
                        continue
            time.sleep(2.5)
            return True
        except Exception:
            return False

    def _capture_captcha_screenshot(self, image_path: str) -> bool:
        try:
            for by, sel in [
                (By.ID, "Captcha_CaptchaImage"),
                (By.CSS_SELECTOR, "img[id*='CaptchaImage']"),
                (By.CSS_SELECTOR, "img[alt*='CAPTCHA']"),
                (By.CSS_SELECTOR, "img[alt*='Retype']"),
                (By.XPATH, "//img[contains(@id, 'Captcha')]"),
            ]:
                try:
                    elem = self.driver.find_element(by, sel)
                    if elem.is_displayed():
                        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
                        time.sleep(0.5)
                        elem.screenshot(image_path)
                        if os.path.exists(image_path) and os.path.getsize(image_path) > 0:
                            return True
                except Exception:
                    continue
            return False
        except Exception:
            return False

    def _clean_captcha_text(self, text: str) -> str:
        if not text:
            return ""
        cleaned = "".join(text.split())
        cleaned = "".join(c for c in cleaned if c.isalnum())
        return cleaned.upper()

    def _extract_captcha_text_gemini(self, image_path: str) -> str:
        if not GEMINI_AVAILABLE:
            return ""
        try:
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                return ""
            genai.configure(api_key=api_key)
            if not os.path.exists(image_path):
                return ""
            image = Image.open(image_path)
            models = self._get_available_gemini_models()
            if not models:
                return ""
            prompt = (
                "Look at this CAPTCHA image and extract the text.\n"
                "Return ONLY the characters concatenated WITHOUT spaces.\n"
                "Example: 'ABC123'. No explanation, no formatting."
            )
            for model_name in models:
                if 'gemma' in model_name.lower() and 'it' in model_name.lower():
                    continue
                try:
                    model = genai.GenerativeModel(model_name)
                    response = model.generate_content([prompt, image])
                    cleaned = self._clean_captcha_text(response.text.strip())
                    if cleaned:
                        return cleaned
                except Exception:
                    continue
            return ""
        except Exception:
            return ""

    def _verify_captcha_text(self, image_path: str, max_retries: int = 5) -> str:
        for attempt in range(1, max_retries + 1):
            text1 = self._extract_captcha_text_gemini(image_path)
            if not text1:
                if attempt < max_retries and self._refresh_captcha():
                    self._capture_captcha_screenshot(image_path)
                continue
            time.sleep(0.5)
            text2 = self._extract_captcha_text_gemini(image_path)
            if not text2:
                if attempt < max_retries and self._refresh_captcha():
                    self._capture_captcha_screenshot(image_path)
                continue
            if text1 == text2:
                return text1
            else:
                if attempt < max_retries and self._refresh_captcha():
                    self._capture_captcha_screenshot(image_path)
        return ""

    def _get_available_gemini_models(self) -> list:
        try:
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                return []
            genai.configure(api_key=api_key)
            available = []
            try:
                for model in genai.list_models():
                    if 'generateContent' in model.supported_generation_methods:
                        available.append(model.name.replace("models/", ""))
            except Exception:
                pass
            if not available:
                return ["gemini-2.0-flash", "gemini-1.5-flash-latest",
                        "gemini-1.5-pro-latest", "gemini-1.5-flash", "gemini-pro-vision"]
            return available
        except Exception:
            return ["gemini-1.5-flash", "gemini-pro-vision"]

    # ─── FILL FORM ───────────────────────────────────────────────────────

    async def fill_personal_form(self, person_data: dict = None) -> tuple:
        try:
            if person_data is None:
                person_data = self.ALL_PERSONS[self.current_person_index]
            data = person_data
            person_label = self._get_person_label()

            try:
                self.wait.until(EC.presence_of_element_located((By.ID, "Lastname")))
            except TimeoutException:
                return False, ["Form page did not load"], None

            text_fields = [
                ("Lastname", data["Lastname"]),
                ("Firstname", data["Firstname"]),
                ("DateOfBirth", data["DateOfBirth"]),
                ("TraveldocumentNumber", data["TraveldocumentNumber"]),
                ("Street", data["Street"]),
                ("Postcode", data["Postcode"]),
                ("City", data["City"]),
                ("Telephone", data["Telephone"]),
                ("Email", data["Email"]),
                ("LastnameAtBirth", data["LastnameAtBirth"]),
                ("PlaceOfBirth", data["PlaceOfBirth"]),
                ("TraveldocumentDateOfIssue", data["TraveldocumentDateOfIssue"]),
                ("TraveldocumentValidUntil", data["TraveldocumentValidUntil"]),
            ]
            for elem_id, value in text_fields:
                try:
                    el = self.driver.find_element(By.ID, elem_id)
                    el.clear()
                    time.sleep(0.1)
                    el.send_keys(value)
                except Exception:
                    logging.exception(f"Failed to fill {elem_id}")

            dropdowns = [
                ("Sex", data["Sex"]),
                ("Country", data["Country"]),
                ("NationalityAtBirth", data["NationalityAtBirth"]),
                ("CountryOfBirth", data["CountryOfBirth"]),
                ("NationalityForApplication", data["NationalityForApplication"]),
                ("TraveldocumentIssuingAuthority", data["TraveldocumentIssuingAuthority"]),
            ]
            for sel_id, val in dropdowns:
                try:
                    Select(self.driver.find_element(By.ID, sel_id)).select_by_value(val)
                except Exception:
                    logging.exception(f"Failed to select {sel_id}")

            try:
                self.driver.execute_script(
                    "var cb = document.getElementById('DSGVOAccepted');"
                    "if(cb){ cb.checked=true; cb.dispatchEvent(new Event('change')); }"
                    "var h = document.querySelector('input[name=DSGVOAccepted][type=hidden]');"
                    "if(h) h.value='true';"
                )
            except Exception:
                pass

            try:
                self.driver.save_screenshot(self.screenshot_path)
            except Exception:
                pass

            success, message, screenshot = await self._submit_form_with_captcha_handling(max_auto_attempts=3)
            return success, [message], screenshot

        except Exception as e:
            return False, [f"Error: {str(e)}"], None

    # ─── NAVIGATION HELPERS ──────────────────────────────────────────────

    def _click_css_with_retry(self, css_selector: str, attempts: int = 3) -> bool:
        for _ in range(attempts):
            try:
                el = WebDriverWait(self.driver, 3).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, css_selector)))
                el.click()
                return True
            except (StaleElementReferenceException, TimeoutException):
                continue
        return False

    def _click_css_any_context(self, css_selector: str, attempts: int = 3) -> bool:
        for _ in range(attempts):
            try:
                self.driver.switch_to.default_content()
                if self._click_css_with_retry(css_selector, attempts=1):
                    return True
                for frame in self.driver.find_elements(By.TAG_NAME, "iframe"):
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
                self.wait.until(
                    lambda d: len(d.find_elements(By.CSS_SELECTOR, f"#{element_id} option")) > 1)
                return Select(el)
            except StaleElementReferenceException as e:
                last_exc = e
        raise last_exc or StaleElementReferenceException(f"Cannot find #{element_id}")

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
        norm_map = {}
        for option in select.options:
            n = self._normalize_visible_text(option.text)
            if n and n not in norm_map:
                norm_map[n] = option.text
        close = difflib.get_close_matches(target_norm, list(norm_map.keys()), n=3, cutoff=0.8)
        if close:
            select.select_by_visible_text(norm_map[close[0]])
            return True
        return False

    def setup_driver(self):
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-software-rasterizer")
        chrome_options.add_argument("--log-level=3")
        self.driver = webdriver.Chrome(service=Service(), options=chrome_options)

    def _restart_driver(self):
        """Quit and recreate the browser to get a clean session."""
        try:
            self.driver.quit()
        except Exception:
            pass
        self.setup_driver()
        self.wait = WebDriverWait(self.driver, 10)
        logging.info("✓ Browser restarted with fresh session")

    # ─── NAVIGATE TO APPOINTMENT LIST ────────────────────────────────────

    def _navigate_to_appointment_list(self) -> bool:
        try:
            btn = "input[type='submit'][value='Next'], input[type='submit'][value='Weiter']"

            self.driver.get(self.url)
            logging.info("Navigated to appointment website")
            
            time.sleep(3)

            self.driver.switch_to.default_content()

            # ===== DEBUG: Find where the elements actually are =====
            # Check main page
            buttons_main = self.driver.find_elements(By.CSS_SELECTOR, "input[type='submit']")
            logging.info(f"Main page - Found {len(buttons_main)} submit buttons:")
            for b in buttons_main:
                logging.info(f"  → value='{b.get_attribute('value')}' name='{b.get_attribute('name')}'")
            
            lang_main = self.driver.find_elements(By.ID, "Language")
            logging.info(f"Main page - Language dropdown found: {len(lang_main) > 0}")

            # Check iframes
            iframes = self.driver.find_elements(By.CSS_SELECTOR, "iframe")
            logging.info(f"Found {len(iframes)} iframes")
            
            target_frame = None
            for i, iframe in enumerate(iframes):
                try:
                    self.driver.switch_to.default_content()
                    self.driver.switch_to.frame(iframe)
                    
                    lang_els = self.driver.find_elements(By.ID, "Language")
                    buttons = self.driver.find_elements(By.CSS_SELECTOR, "input[type='submit']")
                    
                    logging.info(f"  iframe[{i}]: Language={len(lang_els) > 0}, buttons={len(buttons)}")
                    for b in buttons:
                        logging.info(f"    → value='{b.get_attribute('value')}' name='{b.get_attribute('name')}'")
                    
                    if lang_els:
                        target_frame = iframe
                        logging.info(f"  ✓ Language dropdown is in iframe[{i}]")
                except Exception as e:
                    logging.info(f"  iframe[{i}]: Error - {e}")
            
            self.driver.switch_to.default_content()
            # ===== END DEBUG =====

            # Now do the actual work - switch to correct frame if needed
            if target_frame is not None:
                self.driver.switch_to.frame(target_frame)
                logging.info("Switched to iframe containing Language dropdown")

            try:
                wait = WebDriverWait(self.driver, 10)
                
                # Check if English is already selected
                lang_element = wait.until(EC.presence_of_element_located((By.ID, "Language")))
                lang_select = Select(lang_element)
                current_lang = lang_select.first_selected_option.get_attribute("value")
                logging.info(f"Current language: {current_lang}")
                
                if current_lang != "en":
                    lang_select.select_by_value("en")
                    logging.info("Selected Language: English")
                    time.sleep(1)
                    
                    # The button still has its current-language label (e.g. 'ändern' in German).
                    # Find the submit button whose name='Command' regardless of its display value.
                    change_btn = self.driver.find_element(
                        By.CSS_SELECTOR, "input[type='submit'][name='Command']"
                    )
                    self.driver.execute_script("arguments[0].click();", change_btn)
                    logging.info("→ Language change button clicked")
                    
                    time.sleep(4)
                    
                    # After reload, re-acquire iframe context if applicable
                    self.driver.switch_to.default_content()
                    if target_frame is not None:
                        iframes = self.driver.find_elements(By.CSS_SELECTOR, "iframe")
                        for iframe in iframes:
                            try:
                                self.driver.switch_to.default_content()
                                self.driver.switch_to.frame(iframe)
                                if self.driver.find_elements(By.ID, "Language"):
                                    break
                            except Exception:
                                continue
                else:
                    logging.info("English already selected, skipping language change")

            except Exception as e:
                logging.error(f"Error changing language: {e}")
                return False

            # Continue with Office selection
            if not self._select_option_fuzzy_with_retry("Office", "TEHERAN"):
                return False
            logging.info("Selected office: TEHERAN")

            if not self._click_css_any_context(btn):
                return False
            logging.info("→ Next")

            # Step 2: Visa type
            visa_value = "48907107"
            visa_text = "Beglaubigung / Legalization"

            try:
                visa_select = self._get_select_by_id_with_retry("CalendarId")
            except Exception:
                return False

            try:
                has_value = any(o.get_attribute("value") == visa_value for o in visa_select.options)
            except StaleElementReferenceException:
                has_value = False

            if has_value:
                for _ in range(3):
                    try:
                        self._get_select_by_id_with_retry("CalendarId", 1).select_by_value(visa_value)
                        break
                    except StaleElementReferenceException:
                        continue
                else:
                    return False
            else:
                if not self._select_option_fuzzy_with_retry("CalendarId", visa_text):
                    return False

            if not self._click_css_any_context(btn):
                return False
            logging.info("→ Next (visa)")

            if not self._click_css_any_context(btn):
                return False
            logging.info("→ Number of persons")

            if not self._click_css_any_context(btn):
                return False
            logging.info("→ Information page")

            return True
        except Exception as e:
            logging.error(f"Navigation error: {e}", exc_info=True)
            return False
    # ─── CHECK IF APPOINTMENTS AVAILABLE (without booking) ───────────────

    def _check_appointments_available(self) -> tuple:
        """
        Check if there are any available appointment slots on the current page.
        Returns (has_appointments: bool, radio_buttons: list)
        """
        time.sleep(3)

        self.driver.switch_to.default_content()
        for frame in self.driver.find_elements(By.TAG_NAME, "iframe"):
            try:
                self.driver.switch_to.frame(frame)
                if self.driver.find_elements(By.CSS_SELECTOR, "input[type='radio']"):
                    break
            except Exception:
                self.driver.switch_to.default_content()

        try:
            radio_buttons = WebDriverWait(self.driver, 8).until(
                EC.presence_of_all_elements_located(
                    (By.CSS_SELECTOR, "input[type='radio']")
                )
            )
            if radio_buttons:
                return True, radio_buttons
            return False, []
        except TimeoutException:
            page_src = self.driver.page_source.lower()
            if any(kw in page_src for kw in [
                "no appointments", "keine termin", "nicht verfügbar",
                "not available", "keine freien", "no free"
            ]):
                logging.info("No appointments available (page says so)")
            else:
                logging.info("No appointment radio buttons found (timeout)")
            return False, []

    # ─── SELECT SLOT AND BOOK ────────────────────────────────────────────

    async def _select_and_book_appointment(self, radio_buttons) -> tuple:
        person_label = self._get_person_label()

        for attempt in range(3):
            try:
                if not radio_buttons:
                    return False, [], None

                first_radio = radio_buttons[0]

                details = "First available appointment"
                try:
                    rid = first_radio.get_attribute("id")
                    rval = first_radio.get_attribute("value")
                    lbl = self.driver.find_element(By.CSS_SELECTOR, f"label[for='{rid}']")
                    details = f"{lbl.text} on {rval}"
                except Exception:
                    pass

                try:
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", first_radio)
                    first_radio.click()
                except Exception:
                    try:
                        rid = first_radio.get_attribute('id')
                        self.driver.find_element(By.CSS_SELECTOR, f"label[for='{rid}']").click()
                    except Exception:
                        if attempt < 2:
                            continue
                        return False, [], None

                time.sleep(3)

                weiter = self._click_css_any_context(
                    "input[type='submit'][value='Weiter'], input[type='submit'][value='Next']"
                )
                if not weiter:
                    try:
                        b = self.driver.find_element(By.CSS_SELECTOR, "input[value='Next']")
                        self.driver.execute_script("arguments[0].click();", b)
                        weiter = True
                    except Exception:
                        pass
                if not weiter:
                    return False, [], None

                time.sleep(2)

                self.driver.switch_to.default_content()
                for frame in self.driver.find_elements(By.TAG_NAME, "iframe"):
                    try:
                        self.driver.switch_to.frame(frame)
                        if self.driver.find_elements(By.ID, "Lastname"):
                            break
                    except Exception:
                        self.driver.switch_to.default_content()

                try:
                    self.wait.until(EC.presence_of_element_located((By.ID, "Lastname")))
                    logging.info(f"✓ Form loaded for {person_label}")
                except TimeoutException:
                    return False, [], None

                person_data = self.ALL_PERSONS[self.current_person_index]
                ok, info, ss = await self.fill_personal_form(person_data)

                if ok:
                    logging.info(f"✓ Appointment booked for {person_label}: {info}")
                else:
                    logging.error(f"Booking failed for {person_label}: {info}")
                return ok, info, ss

            except StaleElementReferenceException:
                time.sleep(2)
                continue
            except Exception as e:
                logging.error(f"Attempt {attempt+1} error for {person_label}: {e}")
                if attempt < 2:
                    time.sleep(2)
                    continue
                return False, [], None

        return False, [], None

    # ─── SINGLE CHECK CYCLE (one navigation + check + possibly book) ─────

    async def _run_single_check_cycle(self) -> dict:
        """
        Run one complete check cycle:
        1. Navigate to appointment list
        2. Check if slots available
        3. If yes, try to book for each unbooked person

        Returns dict with:
            'appointments_found': bool
            'bookings_made': list of (person_index, success, info, screenshot)
            'error': str or None
        """
        result = {
            "appointments_found": False,
            "bookings_made": [],
            "error": None,
        }

        unbooked = self._get_unbooked_indices()
        if not unbooked:
            return result

        for person_idx in unbooked:
            self.current_person_index = person_idx
            person_label = self._get_person_label()

            logging.info(f"")
            logging.info(f"{'─'*50}")
            logging.info(f"  Checking for {person_label}")
            logging.info(f"{'─'*50}")

            try:
                # Restart browser for clean session each attempt
                self._restart_driver()

                if not self._navigate_to_appointment_list():
                    logging.error(f"Navigation failed for {person_label}")
                    result["bookings_made"].append(
                        (person_idx, False, [f"Navigation failed"], None)
                    )
                    continue

                has_appointments, radio_buttons = self._check_appointments_available()

                if not has_appointments:
                    logging.info(f"No appointments available for {person_label}")
                    result["bookings_made"].append(
                        (person_idx, False, ["No appointments available"], None)
                    )
                    continue

                # Appointments found!
                result["appointments_found"] = True
                logging.info(f"🎉 Appointments FOUND for {person_label}!")

                try:
                    await bot.send_message(
                        CHAT_ID,
                        f"🎉 Appointments found! Attempting to book for {person_label}..."
                    )
                except Exception:
                    pass

                ok, info, ss = await self._select_and_book_appointment(radio_buttons)
                result["bookings_made"].append((person_idx, ok, info, ss))

                if ok:
                    self.persons_booked[person_idx] = True
                    logging.info(f"✅ {person_label} BOOKED!")

                    try:
                        person_data = self.ALL_PERSONS[person_idx]
                        msg = (
                            f"✅✅✅ {person_label} BOOKED! ✅✅✅\n\n"
                            f"👤 {person_data['Firstname']} {person_data['Lastname']}\n"
                            f"📧 {person_data['Email']}\n"
                        )
                        if info:
                            msg += f"📋 {info[0] if isinstance(info, list) else info}\n"
                        await bot.send_message(CHAT_ID, msg)

                        if ss and os.path.exists(ss):
                            await bot.send_photo(
                                CHAT_ID, FSInputFile(ss),
                                caption=f"✅ Confirmation for {person_label}"
                            )
                    except Exception:
                        pass
                else:
                    logging.error(f"❌ Booking FAILED for {person_label}")
                    try:
                        detail = "\n".join(str(t) for t in info) if isinstance(info, list) else str(info)
                        await bot.send_message(
                            CHAT_ID,
                            f"❌ Booking failed for {person_label}:\n{detail}\n\n"
                            f"Will retry on next cycle..."
                        )
                    except Exception:
                        pass

            except Exception as e:
                logging.error(f"Error checking for {person_label}: {e}", exc_info=True)
                result["bookings_made"].append(
                    (person_idx, False, [f"Error: {str(e)}"], None)
                )
                continue

        return result

    # ─── MAIN POLLING LOOP ───────────────────────────────────────────────

    async def run_polling_loop(self):
        """
        Main loop: check every CHECK_INTERVAL_SECONDS until ALL persons are booked.
        """
        # Initialize booking status
        self.persons_booked = [False] * len(self.ALL_PERSONS)
        self.check_count = 0

        logging.info(f"")
        logging.info(f"{'='*60}")
        logging.info(f"  APPOINTMENT POLLING STARTED")
        logging.info(f"  Checking every {CHECK_INTERVAL_SECONDS} seconds ({CHECK_INTERVAL_SECONDS//60} min)")
        logging.info(f"  Booking for {len(self.ALL_PERSONS)} person(s):")
        for i, p in enumerate(self.ALL_PERSONS):
            logging.info(f"    {i+1}. {p['Firstname']} {p['Lastname']}")
        logging.info(f"{'='*60}")

        try:
            persons_list = "\n".join(
                f"  {i+1}. {p['Firstname']} {p['Lastname']}"
                for i, p in enumerate(self.ALL_PERSONS)
            )
            await bot.send_message(
                CHAT_ID,
                f"🚀 Appointment polling started!\n\n"
                f"⏱ Checking every {CHECK_INTERVAL_SECONDS//60} minutes\n"
                f"👥 Booking for:\n{persons_list}\n\n"
                f"I'll notify you when appointments are found and booked."
            )
        except Exception:
            pass

        while not self._all_persons_booked():
            self.check_count += 1
            unbooked = self._get_unbooked_indices()
            unbooked_names = [self._get_person_label(i) for i in unbooked]

            logging.info(f"")
            logging.info(f"{'='*60}")
            logging.info(f"  CHECK CYCLE #{self.check_count}")
            logging.info(f"  Still need to book: {', '.join(unbooked_names)}")
            logging.info(f"{'='*60}")

            try:
                cycle_result = await self._run_single_check_cycle()

                if not cycle_result["appointments_found"]:
                    logging.info(
                        f"No appointments found in cycle #{self.check_count}. "
                        f"Waiting {CHECK_INTERVAL_SECONDS}s before next check..."
                    )
                else:
                    # Appointments were found — check if we need to wait or continue immediately
                    any_new_booking = any(
                        bm[1] for bm in cycle_result["bookings_made"]
                    )
                    if any_new_booking and not self._all_persons_booked():
                        logging.info("Some bookings made. Checking immediately for remaining persons...")
                        continue  # skip the wait, check again now

            except Exception as e:
                logging.error(f"Error in check cycle #{self.check_count}: {e}", exc_info=True)
                try:
                    await bot.send_message(
                        CHAT_ID,
                        f"⚠️ Error in check #{self.check_count}: {e}\nWill retry..."
                    )
                except Exception:
                    pass

            # Check if all booked after this cycle
            if self._all_persons_booked():
                break

            # Wait before next check
            logging.info(f"💤 Sleeping {CHECK_INTERVAL_SECONDS}s until next check...")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

        # ─── All persons booked! ───
        logging.info(f"")
        logging.info(f"{'='*60}")
        logging.info(f"  🎉🎉🎉 ALL PERSONS BOOKED! 🎉🎉🎉")
        logging.info(f"  Total check cycles: {self.check_count}")
        logging.info(f"{'='*60}")

        try:
            summary = "🎉🎉🎉 ALL APPOINTMENTS BOOKED! 🎉🎉🎉\n\n"
            for i, p in enumerate(self.ALL_PERSONS):
                summary += f"✅ {p['Firstname']} {p['Lastname']}\n"
            summary += f"\n📊 Total checks: {self.check_count}"
            await bot.send_message(CHAT_ID, summary)
        except Exception:
            pass

    def cleanup(self):
        try:
            self.driver.quit()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────

async def send_notification(available_times, screenshot_path=None, confirmation_screenshot=None):
    message = "🚨 Appointment Booking Summary!\n\n"
    for t in available_times:
        message += f"📅 {t}\n"
    message += "\n🔗 https://appointment.bmeia.gv.at"

    try:
        await bot.send_message(CHAT_ID, message)
        if screenshot_path and os.path.exists(screenshot_path):
            try:
                await bot.send_photo(CHAT_ID, FSInputFile(screenshot_path), caption="📸 Form screenshot")
            except Exception:
                pass
        if confirmation_screenshot and os.path.exists(confirmation_screenshot):
            try:
                await bot.send_photo(CHAT_ID, FSInputFile(confirmation_screenshot), caption="✅ Confirmation")
            except Exception:
                pass
    except Exception as e:
        logging.error(f"Error sending notification: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

@dp.message(Command("status"))
async def handle_status(message: Message):
    global checker_instance
    if checker_instance:
        waiting = checker_instance.waiting_for_manual_captcha
        person_label = checker_instance._get_person_label()
        checks = checker_instance.check_count

        booked_str = ""
        for i, p in enumerate(checker_instance.ALL_PERSONS):
            booked = (i < len(checker_instance.persons_booked)
                      and checker_instance.persons_booked[i])
            status = "✅ Booked" if booked else "⏳ Waiting"
            booked_str += f"  {status} - {p['Firstname']} {p['Lastname']}\n"

        await message.reply(
            f"🤖 Bot is running\n"
            f"📊 Check cycles: {checks}\n"
            f"👤 Currently: {person_label}\n"
            f"🔒 CAPTCHA wait: {'Yes ⏳' if waiting else 'No'}\n\n"
            f"👥 Booking status:\n{booked_str}"
        )
    else:
        await message.reply("Bot is idle (no active checker).")


@dp.message(F.text)
async def handle_manual_captcha(message: Message):
    global checker_instance

    if message.text.startswith('/'):
        return
    if str(message.chat.id) != str(CHAT_ID):
        return

    if checker_instance and checker_instance.waiting_for_manual_captcha:
        captcha_code = message.text.strip().upper()
        if not captcha_code.isalnum():
            await message.reply("❌ Invalid. Send only letters/numbers (e.g., 'ABC123')")
            return
        if checker_instance.receive_manual_captcha(captcha_code):
            await message.reply(f"✅ CAPTCHA received: {captcha_code}\nSubmitting form...")
        else:
            await message.reply("⚠️ Not expecting CAPTCHA input right now.")
    else:
        await message.reply("ℹ️ Not waiting for CAPTCHA input.")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

async def run_appointment_checker():
    """Run the appointment checker polling loop."""
    global checker_instance

    logging.info("=== APPOINTMENT CHECKER STARTED (POLLING MODE) ===")
    checker = AppointmentChecker()
    checker_instance = checker

    try:
        await checker.run_polling_loop()
    except Exception as e:
        logging.error(f"Polling loop error: {e}", exc_info=True)
        try:
            await bot.send_message(CHAT_ID, f"❌ Fatal error: {e}")
        except Exception:
            pass
    finally:
        logging.info("Cleaning up...")
        checker.cleanup()
        checker_instance = None
        logging.info("=== CHECKER FINISHED ===")


async def main():
    global main_loop
    main_loop = asyncio.get_event_loop()
    polling_task = asyncio.create_task(dp.start_polling(bot))
    checker_task = asyncio.create_task(run_appointment_checker())

    try:
        await checker_task
    except Exception as e:
        logging.error(f"Checker task error: {e}", exc_info=True)
    finally:
        logging.info("Stopping Telegram polling...")
        await dp.stop_polling()
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
        try:
            await bot.session.close()
        except Exception:
            pass
        logging.info("=== ALL DONE ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Terminated by user")
        sys.exit(130)
    except Exception as e:
        logging.error(f"Crashed: {e}", exc_info=True)
        sys.exit(1)