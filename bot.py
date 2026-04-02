# Импортируем модуль os для работы с переменными окружения и путями файлов
import os

# Импортируем re для поиска текста по шаблонам (регулярные выражения)
import re

# Импортируем tempfile для создания временных папок и файлов
import tempfile

# Импортируем logging для вывода логов в консоль Render
import logging

# Импортируем asyncio для совместимости старых библиотек
import asyncio

# Импортируем types как py_types, чтобы вернуть asyncio.coroutine
import types as py_types

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

# Возвращаем asyncio.coroutine для старых библиотек в новых версиях Python
if not hasattr(asyncio, "coroutine"):
    asyncio.coroutine = py_types.coroutine

# Импортируем Mega для работы с облаком Mega
from mega import Mega


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

# Получаем Telegram-токен из переменных окружения Render
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Получаем e-mail от Mega из переменных окружения Render
MEGA_EMAIL = os.getenv("MEGA_EMAIL")

# Получаем пароль от Mega из переменных окружения Render
MEGA_PASSWORD = os.getenv("MEGA_PASSWORD")

# Получаем имя папки с оригинальными PDF на Mega
MEGA_ORIGINAL_FOLDER = os.getenv("MEGA_ORIGINAL_FOLDER", "Orginal")

# Получаем имя папки с разделёнными PDF на Mega
MEGA_SPLIT_FOLDER = os.getenv("MEGA_SPLIT_FOLDER", "Kvitancii")

# Получаем порт от Render
PORT = int(os.getenv("PORT", "10000"))

# Получаем внешний URL сервиса на Render
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

# Дополнительный секрет для пути webhook; если не задан, используем токен
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", BOT_TOKEN)

# Проверяем обязательные переменные
if not BOT_TOKEN or not MEGA_EMAIL or not MEGA_PASSWORD:
    raise ValueError("Не заданы BOT_TOKEN, MEGA_EMAIL или MEGA_PASSWORD в переменных окружения.")

# Показываем маску токена
if BOT_TOKEN and len(BOT_TOKEN) > 16:
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

# Создаём объект Telegram-бота
bot = telebot.TeleBot(BOT_TOKEN)

# Создаём множество пользователей, которые нажали кнопку и теперь должны прислать PDF
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
    # Возвращаем простой текст для проверки живости сервиса
    return "Telegram bot is running via webhook!"


@app.route(WEBHOOK_PATH, methods=["POST"])
def telegram_webhook():
    # Получаем сырое тело запроса
    json_data = request.get_data(as_text=True)

    # Преобразуем JSON в объект Update
    update = Update.de_json(json_data)

    # Передаём обновление в telebot
    bot.process_new_updates([update])

    # Возвращаем OK
    return "OK", 200


@app.route("/set_webhook", methods=["GET"])
def set_webhook_route():
    # Служебный маршрут для ручной установки webhook через браузер
    if not WEBHOOK_URL:
        return "RENDER_EXTERNAL_URL is not set", 500

    try:
        # Удаляем старый webhook и сбрасываем очередь
        bot.remove_webhook()

        # Устанавливаем новый webhook
        success = bot.set_webhook(url=WEBHOOK_URL)

        # Возвращаем результат
        return f"Webhook set: {success} -> {WEBHOOK_URL}", 200
    except Exception as e:
        logger.exception("Ошибка установки webhook")
        return f"Webhook set error: {e}", 500


# =========================
# РАБОТА С MEGA
# =========================

def mega_login():
    # Создаём объект Mega
    mega = Mega()

    # Выполняем вход в Mega
    mega_client = mega.login(MEGA_EMAIL, MEGA_PASSWORD)

    # Возвращаем клиента
    return mega_client


def find_or_create_folder(mega_client, folder_name):
    # Получаем структуру файлов Mega
    files = mega_client.get_files()

    # Перебираем все элементы
    for file_id, info in files.items():
        # Если нашли нужную папку, возвращаем её id
        if info.get("a", {}).get("n") == folder_name and info.get("t") == 1:
            return file_id

    # Иначе создаём папку
    new_folder = mega_client.create_folder(folder_name)

    # Если вернулся словарь, берём первый id
    if isinstance(new_folder, dict):
        return list(new_folder.values())[0]

    # Иначе возвращаем как есть
    return new_folder


def upload_file_to_mega(mega_client, file_path, folder_id):
    # Загружаем файл в Mega
    mega_client.upload(file_path, folder_id)


# =========================
# ИЗВЛЕЧЕНИЕ ДАННЫХ ИЗ PDF
# =========================

def extract_text_from_page(pdf_path, page_number):
    # Открываем PDF через pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        # Берём нужную страницу
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

    # Перебираем шаблоны
    for pattern in patterns:
        # Ищем совпадение
        match = re.search(pattern, text, re.IGNORECASE)

        # Если нашли, возвращаем номер
        if match:
            return match.group(1).strip()

    # Запасной вариант
    fallback = re.search(
        r"рах\w*[^\w]{0,20}([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
        text,
        re.IGNORECASE
    )

    # Если найден запасной вариант, возвращаем его
    if fallback:
        return fallback.group(1).strip()

    # Если не найдено ничего
    return "UNKNOWN_OR"


def extract_year(text):
    # Ищем год формата 20xx
    match = re.search(r"\b(20\d{2})\b", text)

    # Если нашли, возвращаем
    if match:
        return match.group(1)

    # Иначе возвращаем заглушку
    return "UNKNOWN_YEAR"


def extract_month(text):
    # Словарь украинских и русских названий месяцев
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

    # Приводим текст к нижнему регистру
    lower_text = text.lower()

    # Ищем текстовый месяц
    for name, number in month_map.items():
        if name in lower_text:
            return number

    # Ищем дату вида 03.2026 или 03/2026
    match = re.search(r"\b(0?[1-9]|1[0-2])[./](20\d{2})\b", text)
    if match:
        return match.group(1).zfill(2)

    # Ищем дату вида 2026-03
    match = re.search(r"\b(20\d{2})[-./](0?[1-9]|1[0-2])\b", text)
    if match:
        return match.group(2).zfill(2)

    # Если месяц не нашли
    return "UNKNOWN_MONTH"


def build_output_filename(text):
    # Извлекаем год
    year = extract_year(text)

    # Извлекаем месяц
    month = extract_month(text)

    # Извлекаем номер ОР
    account_number = extract_account_number(text)

    # Убираем опасные символы
    safe_account = re.sub(r"[^\w\-]", "_", account_number)

    # Формируем имя файла
    return f"{year} {month} {safe_account}.pdf"


# =========================
# ДЕЛЕНИЕ PDF
# =========================

def split_pdf_by_pages(input_pdf_path, output_folder):
    # Читаем исходный PDF
    reader = PdfReader(input_pdf_path)

    # Создаём список результатов
    created_files = []

    # Идём по страницам
    for page_number in range(len(reader.pages)):
        # Получаем текст страницы
        page_text = extract_text_from_page(input_pdf_path, page_number)

        # Строим имя файла
        output_name = build_output_filename(page_text)

        # Формируем путь
        output_path = os.path.join(output_folder, output_name)

        # Если имя уже занято, добавляем номер страницы
        if os.path.exists(output_path):
            output_name = output_name.replace(".pdf", f"_{page_number + 1}.pdf")
            output_path = os.path.join(output_folder, output_name)

        # Создаём writer
        writer = PdfWriter()

        # Добавляем одну страницу
        writer.add_page(reader.pages[page_number])

        # Сохраняем отдельный PDF
        with open(output_path, "wb") as output_file:
            writer.write(output_file)

        # Сохраняем путь
        created_files.append(output_path)

    # Возвращаем список созданных файлов
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

    # Отправляем сообщение
    bot.send_message(
        message.chat.id,
        "Натисни кнопку «Завантажити та Розділити», а потім надішли PDF-файл.",
        reply_markup=markup
    )


@bot.message_handler(func=lambda message: message.text == "Завантажити та Розділити")
def ask_for_pdf(message):
    # Запоминаем пользователя
    waiting_for_pdf.add(message.chat.id)

    # Просим прислать PDF
    bot.send_message(message.chat.id, "Надішли PDF-файл з квитанціями.")


@bot.message_handler(content_types=["document"])
def handle_document(message):
    # Проверяем, что пользователь нажал кнопку
    if message.chat.id not in waiting_for_pdf:
        bot.send_message(message.chat.id, "Спочатку натисни кнопку «Завантажити та Розділити».")
        return

    # Проверяем расширение
    if not message.document.file_name.lower().endswith(".pdf"):
        bot.send_message(message.chat.id, "Будь ласка, надішли саме PDF-файл.")
        return

    try:
        # Сообщаем о начале обработки
        bot.send_message(message.chat.id, "Файл отримано. Завантажую на Mega та розділяю...")

        # Получаем информацию о файле
        file_info = bot.get_file(message.document.file_id)

        # Скачиваем файл
        downloaded_file = bot.download_file(file_info.file_path)

        # Создаём временную папку
        with tempfile.TemporaryDirectory() as temp_dir:
            # Путь к исходному PDF
            original_pdf_path = os.path.join(temp_dir, message.document.file_name)

            # Сохраняем файл
            with open(original_pdf_path, "wb") as new_file:
                new_file.write(downloaded_file)

            # Авторизуемся в Mega
            mega_client = mega_login()

            # Находим или создаём папку Orginal
            original_folder_id = find_or_create_folder(mega_client, MEGA_ORIGINAL_FOLDER)

            # Находим или создаём папку Kvitancii
            split_folder_id = find_or_create_folder(mega_client, MEGA_SPLIT_FOLDER)

            # Загружаем исходный PDF
            upload_file_to_mega(mega_client, original_pdf_path, original_folder_id)

            # Создаём папку для разделённых файлов
            output_folder = os.path.join(temp_dir, "split_pages")
            os.makedirs(output_folder, exist_ok=True)

            # Делим PDF на отдельные страницы
            split_files = split_pdf_by_pages(original_pdf_path, output_folder)

            # Загружаем все разделённые файлы
            for split_file in split_files:
                upload_file_to_mega(mega_client, split_file, split_folder_id)

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
# УСТАНОВКА WEBHOOK ПРИ СТАРТЕ
# =========================

def ensure_webhook():
    # Если внешний URL не задан, пишем предупреждение и выходим
    if not WEBHOOK_URL:
        logger.warning("RENDER_EXTERNAL_URL не задан. Webhook не будет установлен.")
        return

    try:
        # Удаляем старый webhook и сбрасываем накопившиеся обновления
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

    # Запускаем Flask-сервер
    app.run(host="0.0.0.0", port=PORT)
