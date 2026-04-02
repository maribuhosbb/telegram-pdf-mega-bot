import os
import re
import tempfile
import logging
import subprocess
import shlex
import requests

from flask import Flask, request
import telebot
from telebot import types
from telebot.types import Update
from PyPDF2 import PdfReader, PdfWriter
import pdfplumber

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MEGA_EMAIL = os.getenv("MEGA_EMAIL")
MEGA_PASSWORD = os.getenv("MEGA_PASSWORD")
MEGA_ORIGINAL_FOLDER = os.getenv("MEGA_ORIGINAL_FOLDER", "Orginal")
MEGA_SPLIT_FOLDER = os.getenv("MEGA_SPLIT_FOLDER", "Kvitancii")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
PORT = int(os.getenv("PORT", "10000"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "telegram-webhook-secret")

if not BOT_TOKEN or not MEGA_EMAIL or not MEGA_PASSWORD:
    raise ValueError("Не заданы BOT_TOKEN, MEGA_EMAIL или MEGA_PASSWORD в переменных окружения.")

logger.info("BOT_TOKEN MASK: %s...%s", BOT_TOKEN[:10], BOT_TOKEN[-6:])

check_response = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=20)
logger.info("TELEGRAM TOKEN CHECK STATUS: %s", check_response.status_code)
logger.info("TELEGRAM TOKEN CHECK BODY: %s", check_response.text)

bot = telebot.TeleBot(BOT_TOKEN)
waiting_for_pdf = set()

app = Flask(__name__)
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}" if RENDER_EXTERNAL_URL else None


@app.route("/")
def home():
    return "Telegram bot is running via webhook + megatools!"


@app.route(WEBHOOK_PATH, methods=["POST"])
def telegram_webhook():
    json_data = request.get_data(as_text=True)
    update = Update.de_json(json_data)
    bot.process_new_updates([update])
    return "OK", 200


@app.route("/set_webhook", methods=["GET"])
def set_webhook_route():
    if not WEBHOOK_URL:
        return "RENDER_EXTERNAL_URL is not set", 500

    try:
        delete_response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook",
            params={"drop_pending_updates": "true"},
            timeout=30
        )
        logger.info("DELETE WEBHOOK STATUS: %s", delete_response.status_code)
        logger.info("DELETE WEBHOOK BODY: %s", delete_response.text)

        set_response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            params={"url": WEBHOOK_URL},
            timeout=30
        )
        logger.info("SET WEBHOOK STATUS: %s", set_response.status_code)
        logger.info("SET WEBHOOK BODY: %s", set_response.text)

        return f"Webhook set: {set_response.text}", 200
    except Exception as e:
        logger.exception("Ошибка установки webhook")
        return f"Webhook set error: {e}", 500


def run_megatools_command(args):
    cmd = [
        args[0],
        "--username", MEGA_EMAIL,
        "--password", MEGA_PASSWORD,
        "--no-ask-password",
        *args[1:]
    ]

    safe_cmd = []
    skip_next = False
    for i, part in enumerate(cmd):
        if skip_next:
            skip_next = False
            continue
        if part == "--password" and i + 1 < len(cmd):
            safe_cmd.extend(["--password", "******"])
            skip_next = True
        else:
            safe_cmd.append(part)

    logger.info("MEGATOOLS CMD: %s", " ".join(shlex.quote(x) for x in safe_cmd))

    try:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=180
    )
except subprocess.TimeoutExpired:
    raise RuntimeError("Помилка megatools: команда зависла більше ніж на 180 секунд")

    logger.info("MEGATOOLS EXIT CODE: %s", result.returncode)
    logger.info("MEGATOOLS STDOUT: %s", result.stdout.strip())
    logger.info("MEGATOOLS STDERR: %s", result.stderr.strip())

    if result.returncode != 0:
        raise RuntimeError(
            f"Помилка megatools (code={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip() or 'невідома помилка'}"
        )

    return result.stdout.strip()


def ensure_mega_folder(folder_name):
    try:
        run_megatools_command(["megals", f"/Root/{folder_name}"])
        logger.info("MEGA folder exists: %s", folder_name)
        return
    except Exception:
        logger.info("MEGA folder missing, creating: %s", folder_name)

    run_megatools_command(["megamkdir", f"/Root/{folder_name}"])
    logger.info("MEGA folder created: %s", folder_name)


def upload_file_to_mega(file_path, folder_name):
    filename = os.path.basename(file_path)

    try:
        run_megatools_command(["megarm", f"/Root/{folder_name}/{filename}"])
        logger.info("MEGA: old file removed -> %s", filename)
    except Exception:
        logger.info("MEGA: file not exists, skip remove -> %s", filename)

    run_megatools_command([
        "megaput",
        "--path", f"/Root/{folder_name}/",
        file_path
    ])


def extract_text_from_page(pdf_path, page_number):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_number]
        text = page.extract_text()
        return text or ""


def extract_account_number(text):
    patterns = [
        r"Особов(?:ий|ого)\s+рахунок[:\s№]*([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
        r"Особовий\s+рах\w*[:\s№]*([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
        r"Лицев(?:ой|ого)\s+счет[:\s№]*([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
        r"\bОР[:\s№]*([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    fallback = re.search(
        r"рах\w*[^\w]{0,20}([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
        text,
        re.IGNORECASE
    )
    if fallback:
        return fallback.group(1).strip()

    return "UNKNOWN_OR"


def extract_year(text):
    match = re.search(r"\b(20\d{2})\b", text)
    if match:
        return match.group(1)
    return "UNKNOWN_YEAR"


def extract_month(text):
    month_map = {
        "січень": "01", "січня": "01", "январь": "01", "января": "01",
        "лютий": "02", "лютого": "02", "февраль": "02", "февраля": "02",
        "березень": "03", "березня": "03", "март": "03", "марта": "03",
        "квітень": "04", "квітня": "04", "апрель": "04", "апреля": "04",
        "травень": "05", "травня": "05", "май": "05", "мая": "05",
        "червень": "06", "червня": "06", "июнь": "06", "июня": "06",
        "липень": "07", "липня": "07", "июль": "07", "июля": "07",
        "серпень": "08", "серпня": "08", "август": "08", "августа": "08",
        "вересень": "09", "вересня": "09", "сентябрь": "09", "сентября": "09",
        "жовтень": "10", "жовтня": "10", "октябрь": "10", "октября": "10",
        "листопад": "11", "листопада": "11", "ноябрь": "11", "ноября": "11",
        "грудень": "12", "грудня": "12", "декабрь": "12", "декабря": "12",
    }

    lower_text = text.lower()

    for name, number in month_map.items():
        if name in lower_text:
            return number

    match = re.search(r"\b(0?[1-9]|1[0-2])[./](20\d{2})\b", text)
    if match:
        return match.group(1).zfill(2)

    match = re.search(r"\b(20\d{2})[-./](0?[1-9]|1[0-2])\b", text)
    if match:
        return match.group(2).zfill(2)

    return "UNKNOWN_MONTH"


def build_output_filename(text):
    year = extract_year(text)
    month = extract_month(text)
    account_number = extract_account_number(text)
    safe_account = re.sub(r"[^\w\-]", "_", account_number)
    return f"{year} {month} {safe_account}.pdf"


def split_pdf_by_pages(input_pdf_path, output_folder):
    reader = PdfReader(input_pdf_path)
    created_files = []

    for page_number in range(len(reader.pages)):
        page_text = extract_text_from_page(input_pdf_path, page_number)
        output_name = build_output_filename(page_text)
        output_path = os.path.join(output_folder, output_name)

        if os.path.exists(output_path):
            output_name = output_name.replace(".pdf", f"_{page_number + 1}.pdf")
            output_path = os.path.join(output_folder, output_name)

        writer = PdfWriter()
        writer.add_page(reader.pages[page_number])

        with open(output_path, "wb") as output_file:
            writer.write(output_file)

        created_files.append(output_path)

    return created_files


@bot.message_handler(commands=["start"])
def start_command(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_upload = types.KeyboardButton("Завантажити та Розділити")
    markup.add(btn_upload)

    bot.send_message(
        message.chat.id,
        "Натисни кнопку «Завантажити та Розділити», а потім надішли PDF-файл.",
        reply_markup=markup
    )


@bot.message_handler(func=lambda message: message.text == "Завантажити та Розділити")
def ask_for_pdf(message):
    waiting_for_pdf.add(message.chat.id)
    bot.send_message(message.chat.id, "Надішли PDF-файл з квитанціями.")


@bot.message_handler(content_types=["document"])
def handle_document(message):
    if message.chat.id not in waiting_for_pdf:
        bot.send_message(message.chat.id, "Спочатку натисни кнопку «Завантажити та Розділити».")
        return

    if not message.document.file_name.lower().endswith(".pdf"):
        bot.send_message(message.chat.id, "Будь ласка, надішли саме PDF-файл.")
        return

    try:
        bot.send_message(message.chat.id, "Файл отримано. Завантажую на Mega та розділяю...")

        logger.info("TG: getting file info")
        file_info = bot.get_file(message.document.file_id)

        logger.info("TG: downloading file")
        downloaded_file = bot.download_file(file_info.file_path)

        with tempfile.TemporaryDirectory() as temp_dir:
            original_pdf_path = os.path.join(temp_dir, message.document.file_name)

            logger.info("FS: saving original pdf -> %s", original_pdf_path)
            with open(original_pdf_path, "wb") as new_file:
                new_file.write(downloaded_file)

            ensure_mega_folder(MEGA_ORIGINAL_FOLDER)
            ensure_mega_folder(MEGA_SPLIT_FOLDER)

            upload_file_to_mega(original_pdf_path, MEGA_ORIGINAL_FOLDER)
            import time
time.sleep(2)

            output_folder = os.path.join(temp_dir, "split_pages")
            os.makedirs(output_folder, exist_ok=True)

            logger.info("PDF: splitting start")
            split_files = split_pdf_by_pages(original_pdf_path, output_folder)
            logger.info("PDF: splitting done, files=%s", len(split_files))

            import time

for index, split_file in enumerate(split_files, start=1):
    logger.info("UPLOAD SPLIT FILE %s/%s -> %s", index, len(split_files), split_file)
    upload_file_to_mega(split_file, MEGA_SPLIT_FOLDER)
    time.sleep(1)

            bot.send_message(
                message.chat.id,
                f"Готово. Оригінальний PDF завантажено в «{MEGA_ORIGINAL_FOLDER}», "
                f"а {len(split_files)} окремих файлів — у «{MEGA_SPLIT_FOLDER}»."
            )

        waiting_for_pdf.discard(message.chat.id)

    except Exception as e:
        logger.exception("Ошибка при обработке PDF")
        bot.send_message(message.chat.id, f"Сталася помилка: {e}")


def ensure_webhook():
    if not WEBHOOK_URL:
        logger.warning("RENDER_EXTERNAL_URL не задан. Webhook не будет установлен.")
        return

    try:
        delete_response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook",
            params={"drop_pending_updates": "true"},
            timeout=30
        )
        logger.info("DELETE WEBHOOK STATUS: %s", delete_response.status_code)
        logger.info("DELETE WEBHOOK BODY: %s", delete_response.text)

        set_response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            params={"url": WEBHOOK_URL},
            timeout=30
        )
        logger.info("SET WEBHOOK STATUS: %s", set_response.status_code)
        logger.info("SET WEBHOOK BODY: %s", set_response.text)

    except Exception as e:
        logger.exception("Ошибка установки webhook: %s", e)


if __name__ == "__main__":
    ensure_webhook()
    app.run(host="0.0.0.0", port=PORT)
