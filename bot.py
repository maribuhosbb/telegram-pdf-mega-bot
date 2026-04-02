# Импортируем модуль os для работы с переменными окружения и путями файлов
import os

# Импортируем re для поиска текста по шаблонам
import re

# Импортируем tempfile для создания временных папок и файлов
import tempfile

# Импортируем logging для логов
import logging

# Импортируем subprocess для запуска megatools из командной строки
import subprocess

# Импортируем shlex для безопасственного вывода команд в лог
import shlex

# Импортируем requests для HTTP-запросов к Telegram API
import requests

# Импортируем Flask для webhook-сервера
from flask import Flask, request

# Импортируем telebot для работы с Telegram Bot API
import telebot

# Импортируем types для создания кнопок Telegram
from telebot import types

# Импортируем объект Update для разбора webhook-обновлений
from telebot.types import Update

# Импортируем PdfReader для чтения PDF-файла
from PyPDF2 import PdfReader

# Импортируем PdfWriter для записи отдельных PDF-страниц в новые файлы
from PyPDF2 import PdfWriter

# Импортируем pdfplumber для извлечения текста из PDF
import pdfplumber


# =========================
# ЛОГИРОВАНИЕ
# =========================

# Настраиваем базовое логирование
logging.basicConfig(level=logging.INFO)

# Создаём логгер
logger = logging.getLogger(__name__)


# =========================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# =========================

# Получаем Telegram-токен
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Получаем e-mail Mega
MEGA_EMAIL = os.getenv("MEGA_EMAIL")

# Получаем пароль Mega
MEGA_PASSWORD = os.getenv("MEGA_PASSWORD")

# Получаем имя папки с оригиналами
MEGA_ORIGINAL_FOLDER = os.getenv("MEGA_ORIGINAL_FOLDER", "Orginal")

# Получаем имя папки с квитанциями
MEGA_SPLIT_FOLDER = os.getenv("MEGA_SPLIT_FOLDER", "Kvitancii")

# Получаем внешний URL Render
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

# Получаем порт
PORT = int(os.getenv("PORT", "10000"))

# Получаем секрет для webhook-пути
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "telegram-webhook-secret")

# Проверяем обязательные переменные
if not BOT_TOKEN or not MEGA_EMAIL or not MEGA_PASSWORD:
    raise ValueError("Не заданы BOT_TOKEN, MEGA_EMAIL или MEGA_PASSWORD в переменных окружения.")

# Маска токена в лог
if len(BOT_TOKEN) > 16:
    logger.info("BOT_TOKEN MASK: %s...%s", BOT_TOKEN[:10], BOT_TOKEN[-6:])
else:
    logger.info("BOT_TOKEN MASK: %r", BOT_TOKEN)

# Проверяем токен через getMe
try:
    check_response = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=20)
    logger.info("TELEGRAM TOKEN CHECK STATUS: %s", check_response.status_code)
    logger.info("TELEGRAM TOKEN CHECK BODY: %s", check_response.text)
except Exception as e:
    logger.exception("Ошибка проверки BOT_TOKEN через getMe: %s", e)


# =========================
# TELEGRAM BOT
# =========================

# Создаём бота
bot = telebot.TeleBot(BOT_TOKEN)

# Список пользователей, которые нажали кнопку и должны прислать PDF
waiting_for_pdf = set()


# =========================
# FLASK ДЛЯ WEBHOOK
# =========================

# Создаём Flask-приложение
app = Flask(__name__)

# Формируем путь webhook
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"

# Формируем полный URL webhook
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}" if RENDER_EXTERNAL_URL else None


@app.route("/")
def home():
    # Возвращаем простой текст
    return "Telegram bot is running via webhook + megatools!"


@app.route(WEBHOOK_PATH, methods=["POST"])
def telegram_webhook():
    # Получаем тело запроса
    json_data = request.get_data(as_text=True)

    # Превращаем его в Update
    update = Update.de_json(json_data)

    # Передаём обновление в telebot
    bot.process_new_updates([update])

    # Возвращаем успешный ответ
    return "OK", 200


# =========================
# MEGATOOLS
# =========================

def run_megatools_command(args):
    # Формируем полную команду с логином и паролем
    cmd = [
        args[0],
        "--username", MEGA_EMAIL,
        "--password", MEGA_PASSWORD,
        "--no-ask-password",
        *args[1:]
    ]

    # Пишем безопасную маску команды в лог
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

    # Запускаем команду
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False
    )

    # Логируем stdout и stderr
    logger.info("MEGATOOLS EXIT CODE: %s", result.returncode)
    logger.info("MEGATOOLS STDOUT: %s", result.stdout.strip())
    logger.info("MEGATOOLS STDERR: %s", result.stderr.strip())

    # Если команда упала — даём понятную ошибку
    if result.returncode != 0:
        raise RuntimeError(
            f"Помилка megatools (code={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip() or 'невідома помилка'}"
        )

    # Возвращаем stdout
    return result.stdout.strip()


def ensure_mega_folder(folder_name):
    # Пробуем посмотреть папку
    try:
        run_megatools_command(["megals", f"/Root/{folder_name}"])
        logger.info("MEGA folder exists: %s", folder_name)
        return
    except Exception:
        logger.info("MEGA folder missing, creating: %s", folder_name)

    # Если папки нет — создаём
    run_megatools_command(["megamkdir", f"/Root/{folder_name}"])
    logger.info("MEGA folder created: %s", folder_name)


def upload_file_to_mega(file_path, folder_name):
    # Загружаем файл в указанную папку
    run_megatools_command([
        "megaput",
        "--path", f"/Root/{folder_name}/",
        file_path
    ])


# =========================
# ИЗВЛЕЧЕНИЕ ДАННЫХ ИЗ PDF
# =========================

def extract_text_from_page(pdf_path, page_number):
    # Открываем PDF
    with pdfplumber.open(pdf_path) as pdf:
        # Получаем страницу
        page = pdf.pages[page_number]

        # Извлекаем текст
        text = page.extract_text()

        # Возвращаем текст или пустую строку
        return text or ""


def extract_account_number(text):
    # Список шаблонов для поиска номера особового рахунку
    patterns = [
        r"Особов(?:ий|ого)\s+рахунок[:\s№]*([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
        r"Особовий\s+рах\w*[:\s№]*([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
        r"Лицев(?:ой|ого)\s+счет[:\s№]*([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
        r"\bОР[:\s№]*([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
    ]

    # Ищем по основным шаблонам
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()

    # Запасной вариант
    fallback = re.search(
        r"рах\w*[^\w]{0,20}([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
        text,
        re.IGNORECASE
    )

    if fallback:
        return fallback.group(1).strip()

    # Если не нашли
    return "UNKNOWN_OR"


def extract_year(text):
    # Ищем год 20xx
    match = re.search(r"\b(20\d{2})\b", text)
    if match:
        return match.group(1)
    return "UNKNOWN_YEAR"


def extract_month(text):
    # Словарь месяцев
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

    # Переводим текст в lower
    lower_text = text.lower()

    # Ищем словесный месяц
    for name, number in month_map.items():
        if name in lower_text:
            return number

    # Ищем 03.2026 или 03/2026
    match = re.search(r"\b(0?[1-9]|1[0-2])[./](20\d{2})\b", text)
    if match:
        return match.group(1).zfill(2)

    # Ищем 2026-03
    match = re.search(r"\b(20\d{2})[-./](0?[1-9]|1[0-2])\b", text)
    if match:
        return match.group(2).zfill(2)

    # Если не нашли
    return "UNKNOWN_MONTH"


def build_output_filename(text):
    # Извлекаем год
    year = extract_year(text)

    # Извлекаем месяц
    month = extract_month(text)

    # Извлекаем номер ОР
    account_number = extract_account_number(text)

    # Делаем безопасное имя
    safe_account = re.sub(r"[^\w\-]", "_", account_number)

    # Формируем имя файла
    return f"{year} {month} {safe_account}.pdf"


# =========================
# ДЕЛЕНИЕ PDF
# =========================

def split_pdf_by_pages(input_pdf_path, output_folder):
    # Читаем PDF
    reader = PdfReader(input_pdf_path)

    # Список созданных файлов
    created_files = []

    # Идём по всем страницам
    for page_number in range(len(reader.pages)):
        # Получаем текст страницы
        page_text = extract_text_from_page(input_pdf_path, page_number)

        # Строим имя файла
        output_name = build_output_filename(page_text)

        # Полный путь
        output_path = os.path.join(output_folder, output_name)

        # Если имя уже занято — добавляем суффикс
        if os.path.exists(output_path):
            output_name = output_name.replace(".pdf", f"_{page_number + 1}.pdf")
            output_path = os.path.join(output_folder, output_name)

        # Создаём writer
        writer = PdfWriter()

        # Добавляем одну страницу
        writer.add_page(reader.pages[page_number])

        # Записываем отдельный PDF
        with open(output_path, "wb") as output_file:
            writer.write(output_file)

        # Сохраняем путь
        created_files.append(output_path)

    # Возвращаем список
    return created_files


# =========================
# TELEGRAM: КНОПКИ И КОМАНДЫ
# =========================

@bot.message_handler(commands=["start"])
def start_command(message):
    # Создаём клавиатуру
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)

    # Создаём кнопку
    btn_upload = types.KeyboardButton("Завантажити та Розділити")

    # Добавляем кнопку
    markup.add(btn_upload)

    # Отправляем приветствие
    bot.send_message(
        message.chat.id,
        "Натисни кнопку «Завантажити та Розділити», а потім надішли PDF-файл.",
        reply_markup=markup
    )


@bot.message_handler(func=lambda message: message.text == "Завантажити та Розділити")
def ask_for_pdf(message):
    # Добавляем пользователя в ожидание PDF
    waiting_for_pdf.add(message.chat.id)

    # Просим прислать PDF
    bot.send_message(message.chat.id, "Надішли PDF-файл з квитанціями.")


@bot.message_handler(content_types=["document"])
def handle_document(message):
    # Проверяем, что пользователь нажал кнопку
    if message.chat.id not in waiting_for_pdf:
        bot.send_message(message.chat.id, "Спочатку натисни кнопку «Завантажити та Розділити».")
        return

    # Проверяем, что пришёл PDF
    if not message.document.file_name.lower().endswith(".pdf"):
        bot.send_message(message.chat.id, "Будь ласка, надішли саме PDF-файл.")
        return

    try:
        # Сообщаем о старте
        bot.send_message(message.chat.id, "Файл отримано. Завантажую на Mega та розділяю...")

        # Получаем инфо о файле
        logger.info("TG: getting file info")
        file_info = bot.get_file(message.document.file_id)

        # Скачиваем файл
        logger.info("TG: downloading file")
        downloaded_file = bot.download_file(file_info.file_path)

        # Работаем во временной папке
        with tempfile.TemporaryDirectory() as temp_dir:
            # Путь к оригинальному PDF
            original_pdf_path = os.path.join(temp_dir, message.document.file_name)

            # Сохраняем оригинал
            logger.info("FS: saving original pdf -> %s", original_pdf_path)
            with open(original_pdf_path, "wb") as new_file:
                new_file.write(downloaded_file)

            # Убеждаемся, что папки Mega существуют
            ensure_mega_folder(MEGA_ORIGINAL_FOLDER)
            ensure_mega_folder(MEGA_SPLIT_FOLDER)

            # Загружаем оригинал
            upload_file_to_mega(original_pdf_path, MEGA_ORIGINAL_FOLDER)

            # Папка для разрезанных файлов
            output_folder = os.path.join(temp_dir, "split_pages")
            os.makedirs(output_folder, exist_ok=True)

            # Делим PDF
            logger.info("PDF: splitting start")
            split_files = split_pdf_by_pages(original_pdf_path, output_folder)
            logger.info("PDF: splitting done, files=%s", len(split_files))

            # Загружаем каждую страницу
            for split_file in split_files:
                upload_file_to_mega(split_file, MEGA_SPLIT_FOLDER)

            # Сообщаем об успехе
            bot.send_message(
                message.chat.id,
                f"Готово. Оригінальний PDF завантажено в «{MEGA_ORIGINAL_FOLDER}», "
                f"а {len(split_files)} окремих файлів — у «{MEGA_SPLIT_FOLDER}»."
            )

        # Убираем пользователя из ожидания
        waiting_for_pdf.discard(message.chat.id)

    except Exception as e:
        # Пишем ошибку в лог
        logger.exception("Ошибка при обработке PDF")

        # Сообщаем пользователю
        bot.send_message(message.chat.id, f"Сталася помилка: {e}")


# =========================
# УСТАНОВКА WEBHOOK
# =========================

def ensure_webhook():
    # Если внешний URL не задан
    if not WEBHOOK_URL:
        logger.warning("RENDER_EXTERNAL_URL не задан. Webhook не будет установлен.")
        return

    try:
        # Удаляем старый webhook
        delete_response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook",
            params={"drop_pending_updates": "true"},
            timeout=30
        )
        logger.info("DELETE WEBHOOK STATUS: %s", delete_response.status_code)
        logger.info("DELETE WEBHOOK BODY: %s", delete_response.text)

        # Устанавливаем новый webhook
        set_response = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            params={"url": WEBHOOK_URL},
            timeout=30
        )
        logger.info("SET WEBHOOK STATUS: %s", set_response.status_code)
        logger.info("SET WEBHOOK BODY: %s", set_response.text)

    except Exception as e:
        logger.exception("Ошибка установки webhook: %s", e)


# =========================
# ЗАПУСК
# =========================

if __name__ == "__main__":
    # Настраиваем webhook
    ensure_webhook()

    # Запускаем Flask
    app.run(host="0.0.0.0", port=PORT)
