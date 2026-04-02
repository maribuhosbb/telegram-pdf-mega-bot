# Импортируем модуль os для работы с переменными окружения и путями файлов
import os

# Импортируем re для поиска текста по шаблонам (регулярные выражения)
import re

# Импортируем tempfile для создания временных папок и файлов
import tempfile

# Импортируем logging для вывода логов в консоль Render
import logging

# Импортируем threading для запуска Flask-сервера в отдельном потоке
import threading

# Импортируем Flask, чтобы Render видел открытый веб-порт и не останавливал сервис
from flask import Flask

# Импортируем telebot для работы с Telegram Bot API
import telebot

# Импортируем types для создания кнопок Telegram
from telebot import types

# Импортируем PdfReader для чтения PDF-файла
from PyPDF2 import PdfReader

# Импортируем PdfWriter для записи отдельных PDF-страниц в новые файлы
from PyPDF2 import PdfWriter

# Импортируем pdfplumber для извлечения текста из PDF
import pdfplumber

import asyncio
import types as py_types

# Возвращаем asyncio.coroutine для старых библиотек в новых версиях Python
if not hasattr(asyncio, "coroutine"):
    asyncio.coroutine = py_types.coroutine
# Импортируем Mega для работы с облаком Mega
from mega import Mega


# =========================
# ЛОГИРОВАНИЕ
# =========================

# Настраиваем базовое логирование, чтобы видеть ошибки и события в логах Render
logging.basicConfig(level=logging.INFO)

# Создаём объект логгера для текущего файла
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

# Получаем порт от Render; если локально — используем 10000
PORT = int(os.getenv("PORT", "10000"))


# Проверяем, что обязательные переменные окружения заданы
if not BOT_TOKEN or not MEGA_EMAIL or not MEGA_PASSWORD:
    # Импортируем requests для проверки токена Telegram прямо при запуске
import requests

# Проверяем токен, который реально пришёл из Render environment
try:
    check_response = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=20)
    logger.info(f"TELEGRAM TOKEN CHECK: status={check_response.status_code}, body={check_response.text}")
except Exception as e:
    logger.exception(f"Ошибка проверки BOT_TOKEN через getMe: {e}")
    # Если чего-то не хватает — останавливаем запуск с понятной ошибкой
    raise ValueError("Не заданы BOT_TOKEN, MEGA_EMAIL или MEGA_PASSWORD в переменных окружения.")


# =========================
# TELEGRAM BOT
# =========================

# Создаём объект Telegram-бота
bot = telebot.TeleBot(BOT_TOKEN)

# Создаём множество пользователей, которые нажали кнопку и теперь должны прислать PDF
waiting_for_pdf = set()


# =========================
# FLASK ДЛЯ RENDER
# =========================

# Создаём Flask-приложение
app = Flask(__name__)

# Создаём маршрут главной страницы
@app.route("/")
def home():
    # Возвращаем простой текст, чтобы Render видел, что приложение живое
    return "Telegram bot is running!"


# =========================
# РАБОТА С MEGA
# =========================

def mega_login():
    # Создаём объект Mega
    mega = Mega()

    # Выполняем вход в Mega по e-mail и паролю
    m = mega.login(MEGA_EMAIL, MEGA_PASSWORD)

    # Возвращаем авторизованный объект Mega
    return m


def find_or_create_folder(mega_client, folder_name):
    # Получаем структуру файлов Mega
    files = mega_client.get_files()

    # Перебираем все элементы файловой структуры
    for file_id, info in files.items():
        # Проверяем, что это папка и её имя совпадает с нужным
        if info.get("a", {}).get("n") == folder_name and info.get("t") == 1:
            # Возвращаем идентификатор найденной папки
            return file_id

    # Если папка не найдена — создаём её
    new_folder = mega_client.create_folder(folder_name)

    # Если create_folder вернул словарь, забираем из него первый id
    if isinstance(new_folder, dict):
        # Возвращаем id созданной папки
        return list(new_folder.values())[0]

    # Если вернулся id напрямую — возвращаем его
    return new_folder


def upload_file_to_mega(mega_client, file_path, folder_id):
    # Загружаем файл на Mega в указанную папку
    mega_client.upload(file_path, folder_id)


# =========================
# ИЗВЛЕЧЕНИЕ ДАННЫХ ИЗ PDF
# =========================

def extract_text_from_page(pdf_path, page_number):
    # Открываем PDF через pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        # Получаем нужную страницу по номеру
        page = pdf.pages[page_number]

        # Извлекаем текст со страницы
        text = page.extract_text()

        # Если текст не найден — возвращаем пустую строку
        return text or ""


def extract_account_number(text):
    # Список шаблонов для поиска номера особового рахунку
    patterns = [
    r"Особов(?:ий|ого)\s+рахунок[:\s]*([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
    r"Особовий\s+рах\w*[:\s]*([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
    r"Лицев(?:ой|ого)\s+счет[:\s]*([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
    r"ОР[:\s]*([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})",
]
    # Перебираем шаблоны поиска
    for pattern in patterns:
        # Ищем совпадение в тексте без учёта регистра
        match = re.search(pattern, text, re.IGNORECASE)
        # Если нашли — возвращаем номер счёта
        if match:
            return match.group(1).strip()

    # Если не нашли — пробуем запасной вариант: длинное число рядом со словом рахунок
    fallback = re.search(r"рах\w*[^\w]{0,20}([0-9A-Za-zА-Яа-яІіЇїЄєҐґ\-\/]{5,})", text, re.IGNORECASE)
    # Если найден запасной вариант — возвращаем его
    if fallback:
        return fallback.group(1).strip()

    # Если не найдено ничего — возвращаем UNKNOWN_OR
    return "UNKNOWN_OR"


def extract_year(text):
    # Ищем год формата 20xx
    match = re.search(r"\b(20\d{2})\b", text)
    # Если нашли год — возвращаем его
    if match:
        return match.group(1)

    # Если не нашли — возвращаем UNKNOWN_YEAR
    return "UNKNOWN_YEAR"


def extract_month(text):
    # Словарь украинских, русских и числовых вариантов месяца
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
        # Если название месяца найдено в тексте — возвращаем его номер
        if name in lower_text:
            return number

    # Ищем дату вида 03.2026 или 03/2026
    match = re.search(r"\b(0?[1-9]|1[0-2])[./](20\d{2})\b", text)
    # Если нашли — возвращаем месяц с ведущим нулём
    if match:
        return match.group(1).zfill(2)

    # Ищем дату вида 2026-03
    match = re.search(r"\b(20\d{2})[-./](0?[1-9]|1[0-2])\b", text)
    # Если нашли — возвращаем месяц с ведущим нулём
    if match:
        return match.group(2).zfill(2)

    # Если месяц не нашли — возвращаем UNKNOWN_MONTH
    return "UNKNOWN_MONTH"


def build_output_filename(text):
    # Извлекаем год из текста страницы
    year = extract_year(text)

    # Извлекаем месяц из текста страницы
    month = extract_month(text)

    # Извлекаем номер особового рахунку из текста страницы
    account_number = extract_account_number(text)

    # Убираем из номера счёта опасные символы для имени файла
    safe_account = re.sub(r"[^\w\-]", "_", account_number)

    # Формируем имя файла
    return f"{year} {month} {safe_account}.pdf"


# =========================
# ДЕЛЕНИЕ PDF
# =========================

def split_pdf_by_pages(input_pdf_path, output_folder):
    # Создаём объект для чтения исходного PDF
    reader = PdfReader(input_pdf_path)

    # Создаём список для путей к новым файлам
    created_files = []

    # Проходим по всем страницам PDF
    for page_number in range(len(reader.pages)):
        # Получаем текст текущей страницы через pdfplumber
        page_text = extract_text_from_page(input_pdf_path, page_number)

        # Формируем имя выходного файла на основании текста страницы
        output_name = build_output_filename(page_text)

        # Формируем полный путь к выходному файлу
        output_path = os.path.join(output_folder, output_name)

        # Если такое имя уже существует — добавляем номер страницы
        if os.path.exists(output_path):
            output_name = output_name.replace(".pdf", f"_{page_number + 1}.pdf")
            output_path = os.path.join(output_folder, output_name)

        # Создаём объект для записи нового PDF
        writer = PdfWriter()

        # Добавляем в него текущую страницу
        writer.add_page(reader.pages[page_number])

        # Открываем файл для записи в бинарном режиме
        with open(output_path, "wb") as output_file:
            # Записываем одну страницу в отдельный PDF
            writer.write(output_file)

        # Добавляем путь созданного файла в список
        created_files.append(output_path)

    # Возвращаем список созданных файлов
    return created_files


# =========================
# TELEGRAM: КНОПКИ И КОМАНДЫ
# =========================

@bot.message_handler(commands=["start"])
def start_command(message):
    # Создаём клавиатуру с изменяемым размером кнопок
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)

    # Создаём кнопку "Завантажити та Розділити"
    btn_upload = types.KeyboardButton("Завантажити та Розділити")

    # Добавляем кнопку в клавиатуру
    markup.add(btn_upload)

    # Отправляем приветствие и показываем кнопку
    bot.send_message(
        message.chat.id,
        "Натисни кнопку «Завантажити та Розділити», а потім надішли PDF-файл.",
        reply_markup=markup
    )


@bot.message_handler(func=lambda message: message.text == "Завантажити та Розділити")
def ask_for_pdf(message):
    # Добавляем пользователя в список ожидающих отправку PDF
    waiting_for_pdf.add(message.chat.id)

    # Просим пользователя отправить PDF-файл
    bot.send_message(message.chat.id, "Надішли PDF-файл з квитанціями.")


@bot.message_handler(content_types=["document"])
def handle_document(message):
    # Проверяем, что пользователь до этого нажал кнопку
    if message.chat.id not in waiting_for_pdf:
        # Если кнопку не нажимал — сообщаем, что сначала нужно нажать кнопку
        bot.send_message(message.chat.id, "Спочатку натисни кнопку «Завантажити та Розділити».")
        return

    # Проверяем, что прислан именно PDF
    if not message.document.file_name.lower().endswith(".pdf"):
        # Если это не PDF — просим прислать PDF
        bot.send_message(message.chat.id, "Будь ласка, надішли саме PDF-файл.")
        return

    try:
        # Отправляем сообщение о начале обработки
        bot.send_message(message.chat.id, "Файл отримано. Завантажую на Mega та розділяю...")

        # Получаем информацию о файле в Telegram
        file_info = bot.get_file(message.document.file_id)

        # Скачиваем файл из Telegram по его пути
        downloaded_file = bot.download_file(file_info.file_path)

        # Создаём временную папку для обработки файлов
        with tempfile.TemporaryDirectory() as temp_dir:
            # Формируем путь для сохранения исходного PDF во временной папке
            original_pdf_path = os.path.join(temp_dir, message.document.file_name)

            # Сохраняем скачанный PDF на диск
            with open(original_pdf_path, "wb") as new_file:
                # Записываем содержимое файла
                new_file.write(downloaded_file)

            # Авторизуемся в Mega
            mega_client = mega_login()

            # Находим или создаём папку Orginal
            original_folder_id = find_or_create_folder(mega_client, MEGA_ORIGINAL_FOLDER)

            # Находим или создаём папку Kvitancii
            split_folder_id = find_or_create_folder(mega_client, MEGA_SPLIT_FOLDER)

            # Загружаем исходный PDF в папку Orginal
            upload_file_to_mega(mega_client, original_pdf_path, original_folder_id)

            # Создаём подпапку для разделённых файлов
            output_folder = os.path.join(temp_dir, "split_pages")

            # Создаём эту папку на диске
            os.makedirs(output_folder, exist_ok=True)

            # Делим PDF на отдельные страницы
            split_files = split_pdf_by_pages(original_pdf_path, output_folder)

            # Перебираем все разделённые файлы
            for split_file in split_files:
                # Загружаем каждый разделённый файл в папку Kvitancii
                upload_file_to_mega(mega_client, split_file, split_folder_id)

            # Сообщаем пользователю об успешной обработке
            bot.send_message(
                message.chat.id,
                f"Готово. Оригінальний PDF завантажено в «{MEGA_ORIGINAL_FOLDER}», "
                f"а {len(split_files)} окремих файлів — у «{MEGA_SPLIT_FOLDER}»."
            )

        # Убираем пользователя из списка ожидающих PDF
        waiting_for_pdf.discard(message.chat.id)

    except Exception as e:
        # Пишем ошибку в лог
        logger.exception("Ошибка при обработке PDF")

        # Сообщаем пользователю об ошибке
        bot.send_message(message.chat.id, f"Сталася помилка: {e}")


# =========================
# ЗАПУСК
# =========================

def run_flask():
    # Запускаем Flask-сервер на 0.0.0.0 и нужном порту
    app.run(host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    # Создаём отдельный поток для Flask, чтобы он не мешал Telegram-боту
    flask_thread = threading.Thread(target=run_flask)

    # Делаем поток демоном, чтобы он завершался вместе с программой
    flask_thread.daemon = True

    # Запускаем Flask-поток
    flask_thread.start()

    # Запускаем бесконечное получение сообщений от Telegram
    bot.infinity_polling(timeout=60, long_polling_timeout=30)
