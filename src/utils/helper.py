import os
import re
import requests
from dotenv import load_dotenv
from selenium import webdriver
from telegram.utils.request import Request
from selenium.webdriver.common.by import By
from googleapiclient.discovery import build
from forex_python.converter import CurrencyRates
from utils.custom_logger import get_custom_logger
from telegram.ext import Updater, CallbackContext
from telegram import Bot, Update, InputMediaPhoto
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from oauth2client.service_account import ServiceAccountCredentials

load_dotenv()
logger = get_custom_logger(__name__)

# Define the file path for storing the interest percent
percent_file_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "credentials"))
percent_file_path = os.path.join(percent_file_dir, "interest_percent.txt")
percent_file_path = os.path.normpath(percent_file_path)

# Run Chrome in headless mode (no GUI)
chrome_options = webdriver.ChromeOptions()
chrome_options.add_argument("--headless")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--window-size=1250x600")

chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument(f'user-agent={os.getenv("USER_AGENT")}')
chrome_options.add_argument("--disable-blink-features=AutomationControlled")
chrome_options.add_argument(f'--user-data-dir={os.getenv("USER_DATA_DIR")}')


def normalize_command_text(command_text):
    # Ensure proper spacing around '-' and '@', and remove commas
    command_text = command_text.replace(',', '')
    command_text = re.sub(r'(\d+)([A-Z]{3})', r'\1 \2', command_text)  # Ensure space between amount and currency
    command_text = re.sub(r'\s*-\s*', ' - ', command_text)  # Space around '-'
    command_text = re.sub(r'@\s*', ' @', command_text)  # Ensure single space before '@'
    return command_text

def get_bot_service():
    creds_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "credentials"))
    creds_file_path = os.path.join(creds_dir, os.getenv("GOOGLE_SHEETS_API_CREDENTIALS_FILE"))
    creds_file_path = os.path.normpath(creds_file_path)

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"] # Set up Google Sheets API
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        creds_file_path, 
        scope
    )

    # Create the Telegram Bot and Updater
    pool_request = Request(con_pool_size=16)
    telegram_bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"), request=pool_request)

    updater = Updater(
        bot=telegram_bot,
        use_context=True
    )
    
    sheet_service = build('sheets', 'v4', credentials=creds) 
    return { 'sheet_service': sheet_service, 'updater': updater, 'creds': creds }


# Function to take a screenshot of the customer's sheet and save it
def take_screenshot(sheet_name):
    # Define the base filename
    base_filename = f"{sheet_name.split(' ')[0]}_screenshot.png"

    # Define the path for the screenshots directory
    screenshots_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "screenshots"))

    # Create the screenshots directory if it doesn't exist
    if not os.path.exists(screenshots_dir):
        os.makedirs(screenshots_dir)

    # Define the full path to save the screenshot
    screenshot_filename = os.path.join(screenshots_dir, base_filename)
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        spreadsheet_url = os.getenv("GOOGLE_SHEET_URL")        
        driver.get(spreadsheet_url)

        sheet_element = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, f"//span[@class='docs-sheet-tab-name' and text()='{sheet_name}']"))
        )
        sheet_element.click()

        # Hide the sheet tabs using JavaScript
        driver.execute_script("""
            document.querySelector('div[role="navigation"][aria-label="Sheet tab bar"]').style.visibility = 'hidden';
        """)

        # Wait for the UI to update after hiding the tab bar
        WebDriverWait(driver, 2).until(lambda d: d.execute_script(
            "return document.readyState") == "complete")
        
        driver.save_screenshot(screenshot_filename) # Take a screenshot of the sheet
    except Exception as e:
        logger.error(f"Screenshot error occurred with selenium driver: {e}")
    finally:
        driver.close()

    return screenshot_filename


def send_one_time_photo(update: Update, context: CallbackContext, filename: str, sheet_name: str):
    # Use the context to get the bot object
    bot = context.bot

    # Use the message attribute of the update object to get the chat_id
    chat_id = update.message.chat_id

    # Send the one-time photo
    with open(filename, 'rb') as photo:
        media = InputMediaPhoto(media=photo.read(), caption=f"Photo of {sheet_name}'s sheet")
        bot.send_media_group(chat_id=chat_id, media=[media])

    # Delete the file after sending
    try:
        os.remove(filename)
        logger.info(f"File {filename} deleted successfully")
    except Exception as e:
        logger.error(f"Error deleting file {filename}: {e}")
    

def get_sheet_id(sheet_service, sheet_name):
    result = sheet_service.spreadsheets().get(spreadsheetId=os.getenv("GOOGLE_SHEET_FILE_ID")).execute()
    sheets = result.get('sheets', [])

    #* Find the sheet with the given name
    sheet = next((s for s in sheets if s['properties']['title'] == sheet_name), None)

    if sheet:
        return sheet['properties']['sheetId']
    else:
        return None
    

def download_pdf_sheet(sheet_name):
    try:
        bot_service = get_bot_service()

        creds = bot_service["creds"]
        sheet_service = bot_service["sheet_service"]

        existing_sheet_id = os.getenv("GOOGLE_SHEET_FILE_ID") 
        sheet_id = get_sheet_id(sheet_service, sheet_name)

        if sheet_id is None:
            print(f"Sheet '{sheet_name}' not found.")
            return None

        url = f"https://docs.google.com/spreadsheets/export?format=pdf&id={existing_sheet_id}&gid={sheet_id}"

        headers = {'Authorization': 'Bearer ' + creds.create_delegated(os.getenv("SERVICE_ACCOUNT_EMAIL")).get_access_token().access_token}
        res = requests.get(url, headers=headers)

        pdf_filename = f"{sheet_name.split(' ')[0]}_sheet.pdf"
        pdf_files_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sheets"))

        if not os.path.exists(pdf_files_dir):
            os.makedirs(pdf_files_dir)

        output_file = os.path.join(pdf_files_dir, pdf_filename)

        with open(output_file, "wb") as f:
            f.write(res.content)

        return output_file
    except Exception as e:
        logger.error(f"Error downloading sheet: {str(e)}")


def upload_to_sendgb(sheet_name, customer_password):
    pdf_file_path = download_pdf_sheet(sheet_name)

    try:
        driver = webdriver.Chrome(options=chrome_options)
        sendgb_url = os.getenv("SENDGB_URL")       

        driver.get(sendgb_url)
        actions = ActionChains(driver)

        #* Click on the link icon
        link_icon = driver.find_element(By.XPATH, "//label[@title='Link']")
        link_icon.click()

        #* Click on the '+' icon to select a file to upload
        h2_element = driver.find_element(By.XPATH, "//h2[text()='Select file(s)']")

        #* Use ActionChains to move to the element and click
        actions.move_to_element(h2_element).click().perform()

        #* Wait for the file upload dialog to appear
        file_upload_input = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, "//input[@type='file']"))
        )

        #* Upload the PDF file
        file_upload_input.send_keys(pdf_file_path)

        #* Click on the 'Password (Optional)' input field
        password_input = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//input[@id='password']"))
        )
        actions.move_to_element(password_input).click().perform()

        #* Fill in the password field with 'customer_password'
        password_input.send_keys(customer_password)

        #* Click on the 'Share file(s)' button
        share_button = driver.find_element(By.XPATH, "//button[@id='submit_upload']")
        actions.move_to_element(share_button).click().perform()

        #* Retrieve the download link
        copied_link_element = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "button#copy-button"))
        )
        copied_link = copied_link_element.get_attribute("data-clipboard-text")

        return copied_link
    except Exception as e:
        logger.error(f"Upload error occurred with selenium driver: {e}")
    finally:
        try:
            os.remove(pdf_file_path)
            logger.info(f"File {pdf_file_path} deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting file {pdf_file_path}: {e}")
            
        driver.close()


def save_percent_to_file(percent):
    # Save the interest percent to the file
    with open(percent_file_path, "w") as file:
        file.write(str(percent))


def load_percent_from_file():
    # Load the interest percent from the file
    if os.path.exists(percent_file_path):
        with open(percent_file_path, "r") as file:
            return float(file.read())
    

# Function to get real-time exchange rate using forex_python
def get_real_time_exchange_rate(base_currency, target_currency):
    c = CurrencyRates()
    rate = c.get_rate(base_currency, target_currency)
    return round(rate, 4)


def get_existing_sheets(spreadsheet_id, sheets_service):
    try:
        # Use the Google Sheets API to get information about the spreadsheet
        spreadsheet_info = sheets_service.spreadsheets().get(
            spreadsheetId=spreadsheet_id
        ).execute()

        # Extract sheet names
        sheet_names = [sheet['properties']['title'] for sheet in spreadsheet_info['sheets']]
        return sheet_names

    except Exception as e:
        logger.error(f"Error getting existing sheets: {str(e)}")
        return []


def find_empty_row(sheet_service, spreadsheet_id, sheet_name):
    # Find the first empty row in the specified sheet
    values_range = f"{sheet_name} GBP/EUR!A:G"
    result = sheet_service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=values_range).execute()
    values = result.get('values', [])

    if not values:
        return 2  # If the sheet is completely empty, start from row 2
    else:
        return len(values) + 1


def update_sheet_values(service, spreadsheet_id, range, values):
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range,
        valueInputOption="RAW",
        body={
            "values": values
        }
    ).execute()