import os
import re
import math
from datetime import datetime
from dotenv import load_dotenv
from utils.custom_logger import get_custom_logger
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
logger = get_custom_logger(__name__)
default_interest_percent = load_percent_from_file()

def setup_bot():
    logger.info("Bot is starting...")
    bot_service = get_bot_service()
    updater = bot_service["updater"]
    logger.info("Initializing modules...")

    dispatcher = updater.dispatcher
    sheet_service = bot_service["sheet_service"]
    existing_sheet_id = os.getenv("GOOGLE_SHEET_FILE_ID") # ID of the existing Google Sheet

    def new_customer(update, context):
        # Implement logic to create a new Google Sheet for the customer
        logger.info(f"User sent command: {update.message.text}")
        logger.info("Handling /new_customer command...")

        # Extract command arguments
        args = context.args

        if len(args) < 2:
            logger.error("Please provide Sheet name and Password in the correct format")
            update.message.reply_text("Please provide Sheet name and Password in the correct format\n\n"
                                      "Format: /NC [Sheet name] [Password]\n\n"
                                      "Example: /NC Zangetsu Password123")
            return

        sheet_name = args[0].lower()
        customer_password = args[1]

        try:
            existing_sheets = get_existing_sheets(existing_sheet_id, sheet_service)

            # Check if the sheet exists
            if f"{sheet_name} GBP/EUR" in existing_sheets:
                logger.error(f"Sheet '{sheet_name} GBP/EUR' already exists")
                update.message.reply_text(f"Sheet '{sheet_name} GBP/EUR' already exists")
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

            logger.info(f"New sheet '{sheet_name} GBP/EUR' created successfully for the customer")
            update.message.reply_text(f"New sheet '{sheet_name} GBP/EUR' created successfully for the customer")
        
        except Exception as e:
            logger.error(f"Error creating sheet: {str(e)}")
            update.message.reply_text(f"Error creating sheet: {str(e)}")


    def payments_in(update, context):
        # Implement logic for processing deposits and updating the Google Sheet
        logger.info(f"User sent command: {update.message.text}")
        logger.info("Handling /payments_in command...")

        global default_interest_percent # Access the global variable

        try:
            command_text = update.message.text[len('/PI '):].strip()  # Remove command prefix and strip whitespace
            pattern = re.compile(r'(\w+)\s*-\s*(.*?)\s+(\d+)([A-Z]{3})\s*@\s*(\d+(\.\d{1,8})?)\s*(?:@\s*(\d+(\.\d{1,2})?))?\s*(\d{2}/\d{2}/\d{2})?')
            match = pattern.search(command_text)
            
            if not match:
                logger.error("Couldn't parse the command. Please check the format and try again")
                update.message.reply_text("Couldn't parse the command. Please check the format and try again")
                return

            # Extracting the matched groups with default values for optional fields
            sheet_name, reference, amount_str, currency, rate_str, _, percent_str, _, date_str = match.groups()

            sheet_name = sheet_name.lower()
            amount = int(amount_str)
            exchange_rate = round(float(rate_str), 8) if rate_str else get_real_time_exchange_rate(currency.upper(), "EUR")
            percentage = round(float(percent_str), 2) if percent_str else default_interest_percent
            date = datetime.strptime(date_str, "%d/%m/%y").strftime("%d/%m/%Y") if date_str else datetime.now().strftime("%d/%m/%Y")

            logger.info(f"Extracted details below \nSheet Name: {sheet_name} \nReference: {reference} \nPayment Amount: {amount} \nCurrency: {currency.upper()} \nExchange Rate: {exchange_rate} \nInterest Percent: {percentage} \nDate: {date}")
            existing_sheets = get_existing_sheets(existing_sheet_id, sheet_service)

            # Check if the sheet exists
            if f"{sheet_name} GBP/EUR" not in existing_sheets:
                logger.error(f"Sheet '{sheet_name} GBP/EUR' does not exist")
                update.message.reply_text(f"Sheet '{sheet_name} GBP/EUR' does not exist")
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

            logger.info(f"Deposit record added successfully for '{sheet_name} GBP/EUR'")
            update.message.reply_text(f"Deposit record added successfully for '{sheet_name} GBP/EUR'")

        except ValueError as e:
            logger.error(f"ValueError occurred: {str(e)}")
            update.message.reply_text("Invalid input: please ensure numerical values are correct")
        except TypeError as e:
            logger.error(f"TypeError occurred: {str(e)}")
            update.message.reply_text("Invalid operation: please check the format of your inputs")
        except Exception as e:
            logger.error(f"Error processing deposit: {str(e)}")
            update.message.reply_text(f"Error processing deposit: {str(e)}")


    def payments_out(update, context):
        # Implement logic for processing payments/withdrawals and updating the Google Sheet
        logger.info(f"User sent command: {update.message.text}")
        logger.info("Handling /payments_out command...")

        global default_interest_percent

        command_text = update.message.text[len('/PO '):].strip()  # Adjust the slice to remove '/PO ' prefix correctly
        pattern = re.compile(r'(\w+)\s*-\s*(.*?)\s+(\d{1,3}(?:,\d{3})*)\s*([A-Z]{3})\s*(\d{2}/\d{2}/\d{2,4})?')

        match = pattern.search(command_text)
        if not match:
            logger.error("Invalid format for payment details")
            update.message.reply_text("Please provide a valid format for payment details\n\n"
                                      "Format: /PO [Sheet name]-[Reference] [Amount][Currency] [dd/mm/yy]\n\n"
                                      "Example: /PO Harry-First payment 500EUR 22/01/24")
            return

        sheet_name, reference, amount_str, currency, date_str = match.groups()
        eur_amount = int(amount_str.replace(',', ''))  # Remove commas from amount and convert to int

        sheet_name = sheet_name.lower()
        exchange_rate = get_real_time_exchange_rate("GBP", currency.upper())
        date = datetime.strptime(date_str, "%d/%m/%y").strftime("%d/%m/%Y") if date_str else datetime.now().strftime("%d/%m/%Y")

        logger.info(f"Extracted details below \nSheet Name: {sheet_name} \nReference: {reference} \nPayment Amount: {eur_amount} \nCurrency: {currency.upper()} \nExchange Rate: {exchange_rate} \nInterest Percent: {default_interest_percent} \nDate: {date}")
        
        try:
            existing_sheets = get_existing_sheets(existing_sheet_id, sheet_service)

            # Check if the sheet exists
            if f"{sheet_name} GBP/EUR" not in existing_sheets:
                logger.error(f"Sheet '{sheet_name} GBP/EUR' does not exist")
                update.message.reply_text(f"Sheet '{sheet_name} GBP/EUR' does not exist")
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
                logger.error(f"Error: Insufficient funds. The requested payment amount exceeds the available EUR balance")
                update.message.reply_text(f"Error: Insufficient funds. The requested payment amount exceeds the available EUR balance")
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

            # Update the EUR balance in cell K3
            new_eur_balance = eur_balance - eur_amount
            update_sheet_values(sheet_service, existing_sheet_id, eur_balance_range, [[new_eur_balance]]) 

            logger.info(f"Payment record added successfully for '{sheet_name} GBP/EUR'")
            update.message.reply_text(f"Payment record added successfully for '{sheet_name} GBP/EUR'")
        
        except ValueError as e:
            logger.error(f"ValueError occurred: {str(e)}")
            update.message.reply_text("Invalid input: please ensure numerical values are correct")
        except TypeError as e:
            logger.error(f"TypeError occurred: {str(e)}")
            update.message.reply_text("Invalid operation: please check the format of your inputs")
        except Exception as e:
            logger.error(f"Error processing deposit: {str(e)}")
            update.message.reply_text(f"Error processing deposit: {str(e)}")


    def change_percent_assumptions(update, context):
        # Implement logic for changing the default interest percent
        logger.info(f"User sent command: {update.message.text}")
        logger.info("Handling /change_percent command...")

        args = context.args # Extract command arguments
        global default_interest_percent # Access the global variable

        if len(args) < 1:
            logger.error("Invalid format for new percent assumption")
            update.message.reply_text("Please provide a valid format for new percent assumption\n\n" 
                                      "Format: /CP [Percent Amount]\n\n"
                                      "Example: /CP 12.5")
            return

        new_percent = args[0]

        try:
            interest_rate = float(new_percent)
            default_interest_percent = round(interest_rate, 2) # Set the new interest percent in your script
            save_percent_to_file(default_interest_percent) # Save the new interest percent to a file

            logger.info(f"Default interest percent changed to {default_interest_percent}%")
            update.message.reply_text(f"Default interest percent changed to {default_interest_percent}%")
        except ValueError:
            logger.error("Invalid percent amount. Please provide a valid number")
            update.message.reply_text("Invalid percent amount. Please provide a valid number")


    def change_sheet_password(update, context):
        # Implement logic for changing the customer's password
        logger.info(f"User sent command: {update.message.text}")
        logger.info("Handling /change_password command...")

        command_text = update.message.text[5:].strip()  # Remove '/CSP ' prefix
        pattern = re.compile(r'(\w+)\s*-\s*([\S]+)$')
        
        match = pattern.search(command_text)
        if not match:
            logger.error("Invalid format for new customer password")
            update.message.reply_text("Please provide a valid format for new customer password\n\n"
                                      "Ensure the password does not contain spaces\n\n"
                                      "Format: /CSP [Customer]-[New Password]\n\n"
                                      "Example: /CSP Harry-Imagine123")
            return

        sheet_name, new_password = match.groups()
        sheet_name = sheet_name.lower()

        try:
            existing_sheets = get_existing_sheets(existing_sheet_id, sheet_service)

            # Check if the sheet exists
            if f"{sheet_name} GBP/EUR" not in existing_sheets:
                logger.error(f"Sheet '{sheet_name} GBP/EUR' does not exist")
                update.message.reply_text(f"Sheet '{sheet_name} GBP/EUR' does not exist")
                return
            
            password_range = f"{sheet_name} GBP/EUR!J4"
            update_sheet_values(sheet_service, existing_sheet_id, password_range, [[new_password]]) # Update the password in cell K4

            logger.info(f"Password changed successfully for sheet '{sheet_name} GBP/EUR'")
            update.message.reply_text(f"Password changed successfully for sheet '{sheet_name} GBP/EUR'")
        except Exception as e:
            logger.error(f"Error changing password: {str(e)}")
            update.message.reply_text(f"Error changing password: {str(e)}")


    def request_sheet(update, context):
        # Implement logic for requesting information about a customer's sheet
        logger.info(f"User sent command: {update.message.text}")
        logger.info("Handling /request_sheet command...")

        args = context.args

        if len(args) == 0:
            logger.error("Invalid format for requesting sheet")
            update.message.reply_text("Please provide a valid format for requesting sheet\n\n"
                                      "Format: /RS [Sheet name]\n\n"
                                      "Example: /RS Harry")
            return

        sheet_name = args[0].lower()
        sheet_title = f"{sheet_name} GBP/EUR"

        try:
            existing_sheets = get_existing_sheets(existing_sheet_id, sheet_service)

            # Check if the sheet exists
            if sheet_title not in existing_sheets:
                logger.error(f"Sheet '{sheet_title}' does not exist")
                update.message.reply_text(f"Sheet '{sheet_title}' does not exist")
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

            logger.info(response_message)
            update.message.reply_text(response_message)
        except Exception as e:
            logger.error(f"Error requesting sheet information: {str(e)}")
            update.message.reply_text(f"Error requesting sheet information: {str(e)}")


    def list_sheet(update, context):
        # Implement logic for listing all customer's sheets
        logger.info("User sent command: /LS")
        logger.info("Handling /list_sheet command...")

        try:
            existing_sheets = get_existing_sheets(existing_sheet_id, sheet_service)
            formatted_sheets = [sheet.replace(' GBP/EUR', '') for sheet in existing_sheets]
            sheets_str = ', '.join(formatted_sheets)

            logger.info(f"These are all the available sheets: {sheets_str}")
            update.callback_query.message.reply_text(f"These are all the available sheets: {sheets_str}")
        except Exception as e:
            logger.error(f"Error listing sheets: {str(e)}")
            update.callback_query.message.reply_text(f"Error listing sheets: {str(e)}")


    def error_handler(update, context):
        logger.error(f"An unexpected error occurred: {context.error}")
        
        # Check if the error occurred during a callback query handling
        if update.callback_query:
            update.callback_query.message.reply_text("An unexpected error occurred. Please try again")

        elif update.message:
            update.message.reply_text("An unexpected error occurred. Please try again")
        else:
            logger.error("The error handler was called but no message or callback query was available to reply to")


    def button(update, context):
        query = update.callback_query
        query.answer()

        # Send a message with instructions
        if query.data == "new_customer":
            query.edit_message_text(text="Please provide Sheet name and Password\n\nFormat: /NC [Sheet name] [Password]\n\nExample: /NC Zangetsu Password123")
            return
        elif query.data == "payments_in":
            query.edit_message_text(text="Please provide payment details\n\nFormat: /PI [Sheet name]-[Reference] [Amount][Currency] @[Rate] @[Percent] [dd/mm/yy]\n\nExample: /PI Harry-First deposit 1000GBP @1.1203 @7.0 17/01/24")
            return
        elif query.data == "payments_out":
            query.edit_message_text(text="Please provide payment details\n\nFormat: /PO [Sheet name]-[Reference] [Amount][Currency] [dd/mm/yy]\n\nExample: /PO Harry-First payment 500EUR 22/01/24")
            return
        elif query.data == "change_percent":
            query.edit_message_text(text="Please provide new percent assumption\n\nFormat: /CP [Percent Amount]\n\nExample: /CP 12.5")
            return
        elif query.data == "change_sheet_password":
            query.edit_message_text(text="Please provide new customer password\n\nFormat: /CSP [Customer]-[New Password]\n\nExample: /CSP Harry-Imagine123")
            return
        elif query.data == "request_sheet":
            query.edit_message_text(text="Please provide a Sheet name\n\nFormat: /RS [Sheet name]\n\nExample: /RS Harry")
            return
        elif query.data == "list_sheet":
            context.args = []
            list_sheet(update, context)
            return

    # Add Command Handlers
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CallbackQueryHandler(button))

    dispatcher.add_handler(CommandHandler("NC", new_customer))
    dispatcher.add_handler(CommandHandler("PI", payments_in))
    dispatcher.add_handler(CommandHandler("PO", payments_out))
    dispatcher.add_handler(CommandHandler("CP", change_percent_assumptions))
    dispatcher.add_handler(CommandHandler("CSP", change_sheet_password))
    dispatcher.add_handler(CommandHandler("RS", request_sheet))
    dispatcher.add_handler(CommandHandler("LS", list_sheet))

    dispatcher.add_error_handler(error_handler)

    logger.info("Bot is now running and polling for updates...")
    return updater


def start(update, context):
        keyboard = [
            [InlineKeyboardButton("new_customer", callback_data='new_customer')],
            [InlineKeyboardButton("payments_in", callback_data='payments_in')],
            [InlineKeyboardButton("payments_out", callback_data='payments_out')],
            [InlineKeyboardButton("change_percent", callback_data='change_percent')],
            [InlineKeyboardButton("change_sheet_password", callback_data='change_sheet_password')],
            [InlineKeyboardButton("request_sheet", callback_data='request_sheet')],
            [InlineKeyboardButton("list_sheet", callback_data='list_sheet')]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text('Please choose an option:', reply_markup=reply_markup)