import os
import math
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler

from utils.helper import (
    get_real_time_exchange_rate, 
    get_existing_sheets, 
    update_sheet_values, 
    find_empty_row, 
    save_percent_to_file, 
    load_percent_from_file,
    take_screenshot,
    upload_to_sendgb,
    send_one_time_photo,
    get_bot_service
)

load_dotenv()
logging.basicConfig(level=logging.INFO)

default_interest_percent = load_percent_from_file()
logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)
logging.getLogger('requests.packages.urllib3.connectionpool').setLevel(logging.CRITICAL)
# logging.basicConfig(level=logging.DEBUG)

def setup_bot():
    logging.info("Bot is starting...")
    bot_service = get_bot_service()
    updater = bot_service["updater"]
    logging.info("Initializing modules...")

    dispatcher = updater.dispatcher
    sheet_service = bot_service["sheet_service"]
    existing_sheet_id = os.getenv("GOOGLE_SHEET_FILE_ID") # ID of the existing Google Sheet

    def new_customer(update, context):
        # Implement logic to create a new Google Sheet for the customer
        logging.info(f"User sent command: {update.message.text}")
        logging.info("Handling /new_customer command...")

        # Extract command arguments
        args = context.args

        if len(args) < 2:
            update.message.reply_text("Please provide Sheet name and Password.\n\nFormat: /new_customer [Sheet name] [Password]\n\nExample: /new_customer Zangetsu Password123")
            return

        sheet_name = args[0]
        customer_password = args[1]

        try:
            existing_sheets = get_existing_sheets(existing_sheet_id, sheet_service)

            # Check if the sheet exists
            if f"{sheet_name} GBP/EUR" in existing_sheets:
                update.message.reply_text(f"Sheet '{sheet_name} GBP/EUR' already exists.")
                return
            
            # Use the Google Sheets API to add a new sheet to the existing Google Sheet
            new_sheet_body = {
                "requests": [
                    {
                        "addSheet": {
                            "properties": {
                                "title": f"{sheet_name} GBP/EUR"
                            }
                        }
                    }
                ]
            }

            sheet_service.spreadsheets().batchUpdate(
                spreadsheetId=existing_sheet_id,
                body=new_sheet_body
            ).execute()

            # Add header row to the new sheet
            header_row = [["Date", "Description", "GBP Amount", "Exchange Rate", "Interest Percent", "EUR Amount", "EUR Paid"]]
            header_range = f"{sheet_name} GBP/EUR!A1:G1"

            update_sheet_values(sheet_service, existing_sheet_id, header_range, header_row)

            initial_values = [["Total Due EUR"], ["Total Paid EUR"], ["Balance EUR"], ["Password"]]
            initial_range = f"{sheet_name} GBP/EUR!I1:I4"

            update_sheet_values(sheet_service, existing_sheet_id, initial_range, initial_values)

            new_values = [[0], [0], [0], [customer_password]]
            new_range = f"{sheet_name} GBP/EUR!J1:J4"

            update_sheet_values(sheet_service, existing_sheet_id, new_range, new_values)

            logging.info(f"New sheet '{sheet_name} GBP/EUR' created successfully for the customer.")
            update.message.reply_text(f"New sheet '{sheet_name} GBP/EUR' created successfully for the customer.")
        
        except Exception as e:
            update.message.reply_text(f"Error creating sheet: {str(e)}")


    def payments_in(update, context):
        # Implement logic for processing deposits and updating the Google Sheet
        logging.info(f"User sent command: {update.message.text}")
        logging.info("Handling /payments_in command...")

        args = context.args # Extract command arguments
        global default_interest_percent # Access the global variable

        if len(args) < 5:
            update.message.reply_text("Please provide payment details.\n\nFormat: /payments_in [Sheet name] - [Reference]\n[Amount] [Currency] @[Rate] @[Percent]\n[dd/mm/yy]\n\nExample: /payments_in Harry - First deposit\n1000 GBP/EUR @1.1203 @7%\n11/01/24")
            return

        dash_index = args.index('-') # Find the index of '-'
        sheet_name = ' '.join(args[:dash_index]).strip() # Extract sheet_name
        amount, rate, percent, date_str = None, None, None, None

        # Extract reference
        reference_start_index = dash_index + 1
        reference_end_index = next((i for i, x in enumerate(args[reference_start_index:]) if x.isdigit()), None)
        reference = ' '.join(args[reference_start_index:reference_start_index + reference_end_index]).strip()

        # Iterate through the list to extract relevant information
        for i, item in enumerate(args):
            if item.isdigit():
                amount = int(item)
            elif item.startswith('@'):
                # Check for '%' sign and handle accordingly
                if '%' in item:
                    percent = float(item.strip('%').lstrip('@'))
                else:
                    rate = float(item.lstrip('@'))
            elif '/' in item and i == len(args) - 1 and not item.startswith("G"):
                date_str = item

        # Extracting optional parameters
        exchange_rate = rate if rate else get_real_time_exchange_rate("GBP", "EUR")
        percentage = percent if percent else default_interest_percent
        date = date_str if date_str else datetime.now().strftime("%d/%m/%y")  # Default date is the present date

        try:
            existing_sheets = get_existing_sheets(existing_sheet_id, sheet_service)

            # Check if the sheet exists
            if f"{sheet_name} GBP/EUR" not in existing_sheets:
                update.message.reply_text(f"Sheet '{sheet_name} GBP/EUR' does not exist.")
                return

            # Find the first empty row in the specified sheet
            empty_row = find_empty_row(sheet_service, existing_sheet_id, sheet_name)
            eur_amount = math.ceil((amount * (1 - percentage/100)) * exchange_rate)

            # Add a new record to the customer's sheet
            record_values = [[date, reference, amount, exchange_rate, percentage, eur_amount, ""]]
            record_range = f"{sheet_name} GBP/EUR!A{empty_row}:G{empty_row}"

            update_sheet_values(sheet_service, existing_sheet_id, record_range, record_values)

            # Get the current EUR balance from cell J3
            eur_balance_range = f"{sheet_name} GBP/EUR!J3"
            eur_balance_response = sheet_service.spreadsheets().values().get(
                spreadsheetId=existing_sheet_id,
                range=eur_balance_range
            ).execute()

            eur_balance = int(eur_balance_response.get('values', [[0]])[0][0])
            new_eur_balance = eur_balance + eur_amount # Add the new EUR amount to the existing balance

            # Update the EUR balance in cell K3
            update_sheet_values(sheet_service, existing_sheet_id, eur_balance_range, [[new_eur_balance]])

            logging.info(f"Deposit record added successfully for '{sheet_name} GBP/EUR'.")
            update.message.reply_text(f"Deposit record added successfully for '{sheet_name} GBP/EUR'.")
        
        except Exception as e:
            update.message.reply_text(f"Error processing deposit: {str(e)}")


    def payments_out(update, context):
        # Implement logic for processing payments/withdrawals and updating the Google Sheet
        logging.info(f"User sent command: {update.message.text}")
        logging.info("Handling /payments_out command...")

        args = context.args # Extract command arguments
        global default_interest_percent # Access the global variable

        if len(args) < 4:
            update.message.reply_text("Please provide payment details.\n\nFormat: /payments_out [Sheet name] - [Reference]\n[Amount] [Currency]\n[dd/mm/yy]\n\nExample: /payments_out Harry - First payment\n500 EUR\n22/01/24")
            return

        dash_index = args.index('-') # Find the index of '-'
        sheet_name = ' '.join(args[:dash_index]).strip() # Extract sheet_name
        eur_amount, date_str = None, None

        # Extract reference
        reference_start_index = dash_index + 1
        reference_end_index = next((i for i, x in enumerate(args[reference_start_index:]) if x.isdigit()), None)
        reference = ' '.join(args[reference_start_index:reference_start_index + reference_end_index]).strip()

        # Iterate through the list to extract relevant information
        for i, item in enumerate(args):
            if item.isdigit():
                eur_amount = int(item)
            elif '/' in item and i == len(args) - 1:
                date_str = item

        exchange_rate = get_real_time_exchange_rate("GBP", "EUR")
        date = date_str if date_str else datetime.now().strftime("%d/%m/%y")  # Default date is the present date

        try:
            existing_sheets = get_existing_sheets(existing_sheet_id, sheet_service)

            # Check if the sheet exists
            if f"{sheet_name} GBP/EUR" not in existing_sheets:
                update.message.reply_text(f"Sheet '{sheet_name} GBP/EUR' does not exist.")
                return
            
            # Get the current EUR balance from cell J3
            eur_balance_range = f"{sheet_name} GBP/EUR!J3"
            eur_balance_response = sheet_service.spreadsheets().values().get(
                spreadsheetId=existing_sheet_id,
                range=eur_balance_range
            ).execute()

            eur_balance = int(eur_balance_response.get('values', [[0]])[0][0])

            # Check if the payment amount exceeds the EUR balance
            if eur_amount > eur_balance:
                update.message.reply_text(f"Error: Insufficient funds. The requested payment amount exceeds the available EUR balance.")
                return
            
            # Find the first empty row in the specified sheet
            empty_row = find_empty_row(sheet_service, existing_sheet_id, sheet_name)
            gbp_amount = math.ceil((eur_amount/exchange_rate) / (1 - default_interest_percent/100))

            # Add a new record to the customer's sheet
            record_values = [[date, reference, gbp_amount, exchange_rate, default_interest_percent, "", eur_amount]]
            record_range = f"{sheet_name} GBP/EUR!A{empty_row}:G{empty_row}"

            update_sheet_values(sheet_service, existing_sheet_id, record_range, record_values)

            # Get the current Total Paid EUR from cell J2
            total_paid_range = f"{sheet_name} GBP/EUR!J2"
            total_paid_response = sheet_service.spreadsheets().values().get(
                spreadsheetId=existing_sheet_id,
                range=total_paid_range
            ).execute()

            total_paid_balance = int(total_paid_response.get('values', [[0]])[0][0])
            new_total_paid_balance = total_paid_balance + eur_amount

            # Update the Total Paid EUR in cell J2
            update_sheet_values(sheet_service, existing_sheet_id, total_paid_range, [[new_total_paid_balance]])

            new_eur_balance = eur_balance - eur_amount
            update_sheet_values(sheet_service, existing_sheet_id, eur_balance_range, [[new_eur_balance]]) # Update the EUR balance in cell K3

            logging.info(f"Payment record added successfully for '{sheet_name} GBP/EUR'.")
            update.message.reply_text(f"Payment record added successfully for '{sheet_name} GBP/EUR'.")
        
        except Exception as e:
            update.message.reply_text(f"Error processing payment: {str(e)}")


    def change_percent_assumptions(update, context):
        # Implement logic for changing the default interest percent
        logging.info(f"User sent command: {update.message.text}")
        logging.info("Handling /change_percent command...")

        args = context.args # Extract command arguments
        global default_interest_percent # Access the global variable

        if len(args) < 1:
            update.message.reply_text("Please provide new percent assumption.\n\nFormat: /change_percent [Percent Amount]\n\nExample: /change_percent 12.5")
            return

        new_percent = args[0]

        try:
            default_interest_percent = float(new_percent) # Set the new interest percent in your script
            save_percent_to_file(new_percent) # Save the new interest percent to a file

            logging.info(f"Default interest percent changed to {new_percent}%.")
            update.message.reply_text(f"Default interest percent changed to {new_percent}%.")
        except ValueError:
            update.message.reply_text("Invalid percent amount. Please provide a valid number.")


    def change_sheet_password(update, context):
        # Implement logic for changing the customer's password
        logging.info(f"User sent command: {update.message.text}")
        logging.info("Handling /change_password command...")

        args = context.args # Extract command arguments

        if len(args) < 3:
            update.message.reply_text("Please provide new customer password.\n\nFormat: /change_password [Customer] - [New Password]\n\nExample: /change_password Harry - Imagine123")
            return

        sheet_name = args[0]
        new_passowrd = args[2]

        try:
            existing_sheets = get_existing_sheets(existing_sheet_id, sheet_service)

            # Check if the sheet exists
            if f"{sheet_name} GBP/EUR" not in existing_sheets:
                update.message.reply_text(f"Sheet '{sheet_name} GBP/EUR' does not exist.")
                return
            
            password_range = f"{sheet_name} GBP/EUR!J4"
            update_sheet_values(sheet_service, existing_sheet_id, password_range, [[new_passowrd]]) # Update the password in cell K4

            logging.info(f"Password changed successfully for sheet '{sheet_name} GBP/EUR'.")
            update.message.reply_text(f"Password changed successfully for sheet '{sheet_name} GBP/EUR'.")
        except Exception as e:
            update.message.reply_text(f"Error changing password: {str(e)}")


    def request_sheet(update, context):
        # Implement logic for requesting information about a customer's sheet
        logging.info(f"User sent command: {update.message.text}")
        logging.info("Handling /request_sheet command...")

        args = context.args  # Extract command arguments

        if len(args) == 0:
            update.message.reply_text("Please provide a sheet name.\n\nFormat: /request_sheet [Sheet name]\n\nExample: /request_sheet Harry")
            return

        sheet_name = args[0]
        sheet_title = f"{sheet_name} GBP/EUR"

        try:
            existing_sheets = get_existing_sheets(existing_sheet_id, sheet_service)

            # Check if the sheet exists
            if sheet_title not in existing_sheets:
                update.message.reply_text(f"Sheet '{sheet_title}' does not exist.")
                return

            # Take a screenshot of the customer's sheet
            screenshot_filename = take_screenshot(sheet_title)

            password_range = f"{sheet_name} GBP/EUR!J4"
            password_response = sheet_service.spreadsheets().values().get(
                spreadsheetId=existing_sheet_id,
                range=password_range
            ).execute()

            customer_password = password_response.get('values', [[0]])[0][0]

            # Upload the customer's sheet to SendGB and get a link
            sendgb_link = upload_to_sendgb(sheet_title, customer_password)

            # Generate a one-time photo of the customer's sheet and send it
            send_one_time_photo(update, context, screenshot_filename, sheet_name)

            # Send the SendGB link, customer's name, and one-time photo
            response_message = f"Request sheet Response:\n- SendGB link: {sendgb_link}\n- Customer's name: {sheet_name}"
            update.message.reply_text(response_message)
        except Exception as e:
            update.message.reply_text(f"Error requesting sheet information: {str(e)}")


    def button(update, context):
        query = update.callback_query
        query.answer()

        # Send a message with instructions
        if query.data == 'new_customer':
            query.edit_message_text(text="Please provide Sheet name and Password.\n\nFormat: /new_customer [Sheet name] [Password]\n\nExample: /new_customer Zangetsu Password123")
            return
        elif query.data == 'payments_in':
            query.edit_message_text(text="Please provide payment details.\n\nFormat: /payments_in [Sheet name] - [Reference]\n[Amount] [Currency] @[Rate] @[Percent]\n[dd/mm/yy]\n\nExample: /payments_in Harry - First deposit\n1000 GBP/EUR @1.1203 @7%\n11/01/24")
            return
        elif query.data == 'payments_out':
            query.edit_message_text(text="Please provide payment details.\n\nFormat: /payments_out [Sheet name] - [Reference]\n[Amount] [Currency]\n[dd/mm/yy]\n\nExample: /payments_out Harry - First payment\n500 EUR\n22/01/24")
            return
        elif query.data == 'change_percent':
            query.edit_message_text(text="Please provide new percent assumption.\n\nFormat: /change_percent [Percent Amount]\n\nExample: /change_percent 12.5")
            return
        elif query.data == 'change_password':
            query.edit_message_text(text="Please provide new customer password.\n\nFormat: /change_password [Customer] - [New Password]\n\nExample: /change_password Harry - Imagine123")
            return
        elif query.data == 'request_sheet':
            query.edit_message_text(text="Please provide a Sheet name.\n\nFormat: /request_sheet [Sheet name]\n\nExample: /request_sheet Harry")
            return

    # Add Command Handlers
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CallbackQueryHandler(button))
    dispatcher.add_handler(CommandHandler("new_customer", new_customer))
    dispatcher.add_handler(CommandHandler("payments_in", payments_in))
    dispatcher.add_handler(CommandHandler("payments_out", payments_out))
    dispatcher.add_handler(CommandHandler("change_percent", change_percent_assumptions))
    dispatcher.add_handler(CommandHandler("change_password", change_sheet_password))
    dispatcher.add_handler(CommandHandler("request_sheet", request_sheet))

    logging.info("Bot is now running and polling for updates.")
    return updater


def start(update, context):
        keyboard = [
            [InlineKeyboardButton("new_customer", callback_data='new_customer')],
            [InlineKeyboardButton("payments_in", callback_data='payments_in')],
            [InlineKeyboardButton("payments_out", callback_data='payments_out')],
            [InlineKeyboardButton("change_percent", callback_data='change_percent')],
            [InlineKeyboardButton("change_password", callback_data='change_password')],
            [InlineKeyboardButton("request_sheet", callback_data='request_sheet')]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text('Please choose an option:', reply_markup=reply_markup)