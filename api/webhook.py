from flask import Flask, request, jsonify
import requests
import os
import json
import re
import time
import hmac
from html import unescape

app = Flask(__name__)

PRODAMUS_API_KEY = os.getenv("PRODAMUS_API_KEY")
QWEN_API_KEY = os.getenv("QWEN_API_KEY")
PRODAMUS_BASE_URL = "https://api.xl.ru/api/v1"
QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"

# Telegram-уведомления, когда нужен человек.
# TELEGRAM_CHAT_IDS - список ID через запятую, например: "111111111,222222222"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_IDS = [
    chat_id.strip()
    for chat_id in os.getenv("TELEGRAM_CHAT_IDS", "").split(",")
    if chat_id.strip()
]

# Тег, которым помечаем контакт, пока с ним общается человек -
# пока тег стоит, нейросеть не отвечает этому студенту.
# Уберите тег вручную в карточке контакта в Prodamus, когда разговор с человеком завершён.
AI_PAUSED_TAG = "ai_paused"

# Используется только как fallback, если chatChannelId не пришёл в вебхуке
DEFAULT_CHAT_CHANNEL_ID = os.getenv("CHAT_CHANNEL_ID", "AVmLK7Mvd0qzLMsqzADQCA")

# Секретный токен вебхука - защита от того, что кто-то, зная URL и studentId, дёргает
# вебхук напрямую (в обход сценария Prodamus). Проверяется через query-параметр ?token=...
# в самом URL вебхука (это надёжнее всего настроить в сценарии - URL точно поддерживается).
# ОПЦИОНАЛЬНО: если переменная не задана - проверка выключена, ничего не ломается.
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# ============================================================
# ОБЩАЯ БАЗА ЗНАНИЙ О ШКОЛЕ TIMOFEEVA-ONLINE
# Этот текст видит нейросеть при ответе на КАЖДОЕ сообщение от ЛЮБОГО студента.
# Курсы/тарифы/цены сюда больше вписывать не нужно - они тянутся из Prodamus
# автоматически (см. build_catalog_text() ниже). Здесь остаётся то, чего в API
# нет: общие сведения о школе, оплата, лояльность, сертификаты, FAQ и правила,
# когда звать человека. Правьте прямо здесь и деплойте заново.
# ============================================================
SCHOOL_INFO_INTRO = """
=== О ШКОЛЕ ===
Название: TIMOFEEVA-ONLINE
Тематика: онлайн-курсы, интенсивы и материалы по ультразвуковой диагностике (УЗД) для врачей.
Сайт: https://timofeeva-online.ru/
Личный кабинет (вход в онлайн-школу): https://school.timofeeva-online.ru
Восстановление пароля: https://school.timofeeva-online.ru/forgot-password
Telegram-группа школы: https://t.me/timofeeva_online
Max-группа школы: https://max.ru/id301724845154_biz

Формат обучения (общий для курсов): записи лекций, диагностические задачи,
поддержка куратора, ответы на вопросы, помощь в интерпретации УЗ-изображений
с приёма прямо в чате курса. Онлайн-встречи есть не во всех курсах и тарифах
(см. детали по каждому курсу ниже).
Уровень: курсы подходят как новичкам, так и опытным врачам УЗД.
УЗ-аппарат для обучения не обязателен, но желателен для отработки практики.
Даты старта продаж курса всегда открываются примерно за 1 месяц до начала обучения.
Расписание уроков — фиксированные даты, привязаны к дате старта потока.
"""


# ============================================================
# КАТАЛОГ КУРСОВ И ПРОДУКТОВ - ТЯНЕТСЯ АВТОМАТИЧЕСКИ ИЗ PRODAMUS API
# Раньше этот раздел был текстом, который редактировали вручную в коде.
# Теперь актуальные курсы, тарифы, цены, даты стартов потоков и статусы
# публикации читаются напрямую из Prodamus (GET /course и GET /product) -
# правьте их в админке школы, а не здесь. Результат кэшируется на
# CATALOG_CACHE_TTL_SECONDS секунд, чтобы не дёргать API на каждое сообщение.
# ============================================================

CATALOG_CACHE_TTL_SECONDS = int(os.getenv("CATALOG_CACHE_TTL_SECONDS", "900"))  # 15 минут

_catalog_cache = {"text": "", "fetched_at": 0.0}

CURRENCY_SYMBOLS = {"rub": "₽", "usd": "$", "eur": "€", "byn": "Br", "kzt": "₸"}


def _strip_html(text):
    """Описания в Prodamus - это HTML. Для промпта модели превращаем в чистый текст."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _format_price(item):
    price = item.get("price")
    if price is None:
        return "цена не указана"
    currency = (item.get("currency") or "rub").lower()
    symbol = CURRENCY_SYMBOLS.get(currency, currency.upper())
    return f"{price:,.0f} {symbol}".replace(",", " ")


def _prodamus_get_items(path, fields, take=200):
    url = f"{PRODAMUS_BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {PRODAMUS_API_KEY}",
        "Content-Type": "application/json"
    }
    params = {"fields": fields, "take": take}

    response = requests.get(url, headers=headers, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    return (data.get("body") or {}).get("items") or []


def fetch_courses():
    fields = "{id,name,shortDescription,isPublished}"
    return _prodamus_get_items("/course", fields)


def fetch_products():
    fields = (
        "{id,name,description,type,isPublished,currency,price,"
        "duration,durationType,courseId,categoryId,category{id,name},"
        "flowDateId,flowDate{beginDate,endDate},softDeleted}"
    )
    return _prodamus_get_items("/product", fields)


def build_catalog_text():
    """
    Собирает текстовый каталог из Prodamus:
    - продукты с courseId группируются как тарифы соответствующего курса
    - остальные продукты (интенсивы, чек-листы, библиотеки и т.д.) группируются
      по их category.name

    Возвращает None при ошибке запроса к API (тогда get_catalog_text() отдаст
    последнюю успешно закэшированную версию, если она есть).
    """
    try:
        courses = fetch_courses()
        products = fetch_products()
    except Exception as e:
        print(f"ERROR: Failed to fetch catalog from Prodamus: {e}")
        return None

    courses_by_id = {c["id"]: c for c in courses if c.get("id")}
    tariffs_by_course = {}
    standalone_products = []

    for p in products:
        if p.get("softDeleted"):
            continue
        course_id = p.get("courseId")
        if course_id and course_id in courses_by_id:
            tariffs_by_course.setdefault(course_id, []).append(p)
        else:
            standalone_products.append(p)

    lines = ["=== КУРСЫ ==="]

    for course_id, course in courses_by_id.items():
        tariffs = tariffs_by_course.get(course_id, [])
        if not tariffs:
            continue  # курс без ни одного тарифа/продукта - продавать нечего

        status = "" if course.get("isPublished") else " [курс не опубликован]"
        lines.append(f"\n«{course.get('name')}»{status}")
        short_desc = _strip_html(course.get("shortDescription"))
        if short_desc:
            lines.append(short_desc)

        for t in sorted(tariffs, key=lambda x: x.get("price") or 0):
            availability = "" if t.get("isPublished") else " [сейчас не в продаже]"
            price = _format_price(t)
            duration = ""
            if t.get("duration") and t.get("durationType"):
                unit = "мес." if t["durationType"] == "month" else "дн."
                duration = f", доступ {t['duration']} {unit}"
            start = ""
            flow_date = t.get("flowDate") or {}
            if flow_date.get("beginDate"):
                start = f", старт потока: {flow_date['beginDate'][:10]}"
            lines.append(f"  - Тариф «{t.get('name')}»: {price}{duration}{start}{availability}")
            desc = _strip_html(t.get("description"))
            if desc:
                lines.append(f"    {desc}")

    lines.append("\n=== ИНТЕНСИВЫ И ДРУГИЕ ПРОДУКТЫ ===")
    by_category = {}
    for p in standalone_products:
        cat_name = ((p.get("category") or {}).get("name")) or "Прочее"
        by_category.setdefault(cat_name, []).append(p)

    for cat_name, items in by_category.items():
        lines.append(f"\n{cat_name}:")
        for p in items:
            availability = "" if p.get("isPublished") else " [сейчас не в продаже]"
            price = _format_price(p)
            lines.append(f"  - «{p.get('name')}»: {price}{availability}")
            desc = _strip_html(p.get("description"))
            if desc:
                lines.append(f"    {desc}")

    return "\n".join(lines)


def get_catalog_text():
    """Каталог с кэшем по TTL - Prodamus API дёргается не чаще раза в CATALOG_CACHE_TTL_SECONDS."""
    now = time.time()
    if _catalog_cache["text"] and (now - _catalog_cache["fetched_at"] < CATALOG_CACHE_TTL_SECONDS):
        return _catalog_cache["text"]

    fresh = build_catalog_text()
    if fresh:
        _catalog_cache["text"] = fresh
        _catalog_cache["fetched_at"] = now
        return fresh

    if _catalog_cache["text"]:
        print("WARNING: Using stale cached catalog (fresh fetch failed)")
        return _catalog_cache["text"]

    print("WARNING: No catalog available (fresh fetch failed and no cache)")
    return "(Не удалось получить список курсов и продуктов из Prodamus. " \
           "Не называй студенту конкретные курсы, цены или даты - предложи позвать человека.)"


SCHOOL_INFO_OUTRO = """
=== СЕРТИФИКАТЫ ===
- Выдаются только по итогам КУРСОВ (не выдаются за интенсивы и прочие продукты).
- Сертификат внутреннего образца. Подойдёт для портфолио, для аккредитации использовать нельзя.
- Сертификат не даёт право работать врачом ультразвуковой диагностики.
- Чтобы получить сертификат, нужно набрать более 70% в итоговом тестировании.
- Пересдача итогового теста — бесплатно, количество попыток не ограничено.
- Баллы НМО (непрерывного медицинского образования) с 1 марта 2026 года НЕ выдаются
  за дистанционные курсы — это изменение регламента аккредитации онлайн-форматов обучения.
- Если в сертификате ошибка в данных (например, ФИО) — нужно звать человека, он перевыпустит
  сертификат и пришлёт новую ссылку на скачивание.

=== ОПЛАТА ===
Способы оплаты: СБП, SberPay, Visa/MasterCard/МИР (в рублях), Visa/Mastercard (USD, EUR —
карты банков любых стран, кроме России, Украины, Беларуси), Visa/MasterCard/Белкарт (BYN).

Рассрочка (только в рублях, одинаковый набор для ВСЕХ курсов):
  - Яндекс Сплит (оплата частями)
  - Плати Частями от Сбербанка — 4 платежа, карта Сбера не обязательна
  - Рассрочка от Банков-партнёров («Порублю») на 6 / 10 / 12 месяцев
    (доступна при сумме заказа от 9 900 ₽, даёт промокод на 3 000 ₽ в М.Видео)
  - Также доступны варианты рассрочки РФ на 1.5 / 3 / 4 месяца

Сплит-оплата в 2 платежа — доступна ТОЛЬКО у курсов (не у интенсивов и прочих продуктов),
это отдельный от рассрочки способ (сплит от платформы школы). Вторая часть всегда равна
ровно половине полной цены заказа.
Если заказ оплачен частично (сплит) - ссылку на оплату второй части можно сформировать
и прислать студенту прямо в чате, звать человека для этого не нужно (см. раздел
"СТАТУС ЗАКАЗОВ И ОПЛАТЫ"). Для рассрочки через банки-партнёров (не сплит) такой ссылки
нет - там по-прежнему нужно звать человека.

=== СИСТЕМА ЛОЯЛЬНОСТИ ===
- Скидка 50% на повторное прохождение курса — применяется автоматически при повторной
  оплате на ту же почту (не нужно запрашивать промокод).
- Партнёрской/реферальной программы пока нет, в будущем планируются другие виды скидок.

=== B2B / КОРПОРАТИВНЫЕ ЗАКАЗЫ ===
Возможна оплата обучения для нескольких сотрудников клиники (покупка для юрлиц).
По таким запросам всегда нужно звать человека для уточнения деталей.

=== ВОЗВРАТ СРЕДСТВ И СМЕНА ТАРИФА ===
- Возврат денег возможен — по этому вопросу всегда нужно звать человека.
- Переход с одного тарифа на другой (например, с Базового на Погружение) возможен,
  требуется доплата — для оформления перехода нужно звать человека.

=== ЧАСТЫЕ ВОПРОСЫ И ТОЧНЫЕ ОТВЕТЫ ===

В: Можно ли оплатить баллы НМО за прохождение курса?
О: С 1 марта 2026 года баллы НМО больше не выдаются за дистанционные курсы. Это изменение
   связано с обновлением регламента аккредитации онлайн-форматов обучения.

В: Есть ли скидка / когда старт курса, который сейчас не продаётся?
О: Если курс не в открытой продаже — сообщить, что активного набора сейчас нет, и предложить
   лист ожидания по ссылке конкретного курса (см. раздел «Курсы» выше), а также подписаться
   на Max-группу и Telegram-группу школы, чтобы не пропустить старт и специальные предложения.

В: Не получил(а) доступ к курсу после оплаты.
О: Проверьте правильность введённых данных при входе в личный кабинет
   (https://school.timofeeva-online.ru) и проверьте письмо с доступом на почте, указанной
   при оформлении заказа (в том числе папки "Спам" и "Рассылки"). Обязательно позвать
   человека при этом вопросе.

В: Можно ссылку на оплату оставшейся (второй) части курса?
О: Если заказ оплачен частично (сплит) - ссылка уже готова в разделе "СТАТУС ЗАКАЗОВ И
   ОПЛАТЫ" ниже, пришли её прямо в ответе. Если готовой ссылки там нет (например, это
   рассрочка через банк-партнёр, а не сплит) - позвать человека, самому не придумывать.

В: Почему урок/шаг курса недоступен?
О: Уроки открываются по расписанию. Некоторые шаги могут быть недоступны, если пропущены
   или не выполнены предыдущие шаги. Чтобы узнать точную причину — нажать на иконку замочка
   в меню навигации слева на платформе.

В: Видео не загружается / виснет / глючит / лагает.
О: Возможно нестабильное интернет-соединение — проверить подключение, попробовать
   перезагрузку, и обязательно проверить, что отключён VPN (он может тормозить загрузку видео).

В: Как получить справку об обучении для налогового вычета?
О: Позвать человека — он пришлёт заявление, которое нужно заполнить.

В: Как продлить доступ к курсу?
О: Продление доступа можно оформить внутри курса — этот шаг обычно находится в конце курса.
   Если доступ уже закончился, а студент хочет продлить — позвать человека, он пришлёт
   ссылку для оформления продления.

В: Ошибка в данных сертификата (неверные ФИО и т.п.), можно исправить?
О: Позвать человека — он перевыпустит сертификат с исправленными данными и пришлёт ссылку
   на скачивание.

В: Указал(а) неправильную почту при оформлении заказа, как исправить?
О: Попросить студента прислать полностью новый правильный email - его можно сменить прямо
   в этом чате (см. раздел "СМЕНА EMAIL"), звать человека для этого не нужно.

В: Нужен ли доступ к УЗИ-аппарату, чтобы проходить обучение?
О: Не обязательно, но желательно — так легче отрабатывать практические навыки.

В: Дают ли материалы курса официальное право описывать протоколы / есть ли юридическая
   значимость у сертификата?
О: Сертификат не даёт право работать врачом ультразвуковой диагностики.

В: Для каких специальностей подходят курсы?
О: Все курсы подходят врачам ультразвуковой диагностики — как опытным, так и без опыта.
   Курс "УЗИ щитовидной железы" также может быть интересен и полезен врачам-эндокринологам.

В: Хочу вернуть деньги, курс не подошёл.
О: Возврат возможен. Позвать человека для оформления.

В: Купил(а) не тот тариф, хочу перейти на другой (например, с Базового на Погружение).
О: Переход возможен, потребуется доплата. Позвать человека для оформления перехода/нового заказа.

В: Можно ли оплатить обучение для нескольких сотрудников клиники?
О: Да, возможна B2B-покупка для юрлиц. Позвать человека для уточнения деталей.

В: Как попасть в закрытый Telegram-канал после оплаты?
О: Сразу после оплаты на почту, указанную при заказе, приходит ссылка на вступление в канал.

В: Не набрал(а) 70% в итоговом тесте, можно ли пересдать?
О: Да, пересдача бесплатна, количество попыток не ограничено.

В: Где найти чат обсуждения курса?
О: Нужно вернуться на шаг "Инструкция по работе с платформой" в структуре курса — там есть
   переход в чат обсуждения.

=== КОГДА ОБЯЗАТЕЛЬНО ЗВАТЬ ЧЕЛОВЕКА (не пытаться решить самостоятельно) ===
- Проблемы с доступом к курсу/оплатой, которые не решаются проверкой почты и данных входа
- Запрос ссылки на оплату оставшейся части через РАССРОЧКУ (банк-партнёр) - для сплита
  (2 платежа, курсы) ссылку теперь можно сформировать самому, см. "СТАТУС ЗАКАЗОВ И ОПЛАТЫ"
- Ошибка в данных уже выпущенного сертификата (например, неверные ФИО) - смена email НЕ
  входит сюда, её можно сделать прямо в чате, см. раздел "СМЕНА EMAIL"
- Возврат денег
- Смена тарифа или курса
- Продление доступа, если он уже закончился
- Справка для налогового вычета
- B2B/корпоративные заказы
- Любой вопрос, ответа на который нет в этой базе знаний

Если нужно позвать человека — не придумывай ответ сам. Вежливо сообщи студенту, что
не можешь ответить на этот конкретный вопрос и уже передал обращение специалисту,
который скоро свяжется.
"""


def parse_tags(raw):
    """
    Парсим строку тегов, пришедшую из макроса #Contact.Tags# в теле вебхука.
    Формат резолва макроса заранее не известен (через запятую? JSON-массив?
    через точку с запятой?), поэтому парсер гибкий - обрабатывает разные варианты.
    """
    if not raw:
        return []

    text = str(raw).strip()

    # Если это похоже на JSON-массив - снимаем внешние скобки
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]

    # Разбиваем по запятой или точке с запятой
    parts = re.split(r"[,;]", text)
    return [p.strip().strip('"').strip("'") for p in parts if p.strip()]


def fetch_full_contact(student_id):
    """
    Читаем контакт целиком через CRM API Prodamus - нужно для двух вещей:
    1) проверить, не стоит ли уже тег AI_PAUSED_TAG (тогда бот молчит)
    2) получить email/имя для уведомления, и текущие поля контакта,
       чтобы потом безопасно вернуть их обратно при PUT (не потерять данные)

    ВАЖНО: поле "attributes" сюда намеренно НЕ включено - оно вложенная коллекция
    пользовательских атрибутов и требует явного указания вложенных полей
    (иначе API отвечает 500 "Nested fields ... must be specified"). Без параметра
    fields вообще API отвечает 400 "mustSpecifyFieldsToSelect" - он обязателен.

    GET /api/v1/crm/lead/{id}?fields={...}
    """

    fields = "{id,email,firstName,middleName,lastName,phone,comment,country,birthday,tags,groups}"
    url = f"{PRODAMUS_BASE_URL}/crm/lead/{student_id}"
    params = {"fields": fields}
    headers = {
        "Authorization": f"Bearer {PRODAMUS_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        print(f"DEBUG: Get contact status={response.status_code}")
        print(f"DEBUG: Get contact body: {response.text[:800]}")

        if response.status_code == 200:
            data = response.json()
            return data.get("body") or {}

        return None
    except Exception as e:
        print(f"ERROR: Failed to fetch contact info: {e}")
        return None


def add_ai_paused_tag(contact, known_current_tags=None):
    """
    Добавляем тег AI_PAUSED_TAG и отправляем ВЕСЬ объект контакта обратно через PUT
    (read-merge-write), чтобы не затереть остальные поля контакта.

    ВАЖНО: GET /crm/lead/{id} на стороне Prodamus иногда возвращает ПУСТОЙ список tags,
    даже если у контакта реально есть теги (подтверждено логами - тот же контакт в тот же
    момент показывает теги через макрос #Contact.Tags# в сценарии, но не через этот API).
    Поэтому если known_current_tags передан (например, теги из тела вебхука через
    #Contact.Tags#) - используем ИХ как источник правды, а не contact["tags"] из GET.
    """

    if not contact:
        return False

    if known_current_tags is not None:
        tags = list(known_current_tags)
    else:
        tags = list(contact.get("tags") or [])

    if AI_PAUSED_TAG not in tags:
        tags.append(AI_PAUSED_TAG)

    # Эта модель API использует Optional<T> для полей-коллекций - сервер требует
    # обёртку {"value": [...]}, а не голый JSON-массив (иначе 400 invalidModel).
    updated_contact = dict(contact)
    updated_contact["tags"] = {"value": tags}
    if "groups" in updated_contact:
        updated_contact["groups"] = {"value": updated_contact.get("groups") or []}

    url = f"{PRODAMUS_BASE_URL}/crm/lead"
    headers = {
        "Authorization": f"Bearer {PRODAMUS_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.put(url, headers=headers, json=updated_contact, timeout=10)
        print(f"DEBUG: Update contact (add tag) status={response.status_code}")
        print(f"DEBUG: Update contact body: {response.text[:500]}")
        return response.status_code == 200
    except Exception as e:
        print(f"ERROR: Failed to update contact tags: {e}")
        return False


def send_telegram_notification(text):
    """Отправляем текст всем настроенным админам в Telegram."""

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        print("WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_IDS not configured, skipping notification")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    all_ok = True

    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            response = requests.post(
                url,
                json={"chat_id": chat_id, "text": text},
                timeout=10
            )
            print(f"DEBUG: Telegram notify to {chat_id} status={response.status_code}")
            if response.status_code != 200:
                print(f"ERROR: Telegram notify to {chat_id} failed: {response.text}")
                all_ok = False
        except Exception as e:
            print(f"ERROR: Telegram notify to {chat_id} exception: {e}")
            all_ok = False

    return all_ok


def notify_human(contact, student_id, message_text, ai_reply):
    """Уведомляем админов, что нужен человек"""

    contact = contact or {}
    full_name = " ".join(
        part for part in [contact.get("firstName"), contact.get("lastName")] if part
    ) or "неизвестно"
    email = contact.get("email") or "неизвестно"

    text = (
        "🔔 Нужен человек в чате поддержки\n\n"
        f"Студент: {full_name}\n"
        f"Email: {email}\n"
        f"ID студента: {student_id}\n\n"
        f"Сообщение студента: {message_text}\n\n"
        f"Ответ бота студенту: {ai_reply}\n\n"
        f"⚠️ Бот поставлен на паузу для этого контакта (тег \"{AI_PAUSED_TAG}\"). "
        "Снимите тег в карточке контакта в Prodamus, когда закончите общение."
    )
    return send_telegram_notification(text)


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def update_student_email(student_id, new_email):
    """
    Меняем email студента по его просьбе (например, ошибся при оформлении заказа) -
    это тот же email, на который приходит доступ и под которым он логинится в личный
    кабинет школы. Читаем контакт целиком и отправляем обратно с обновлённым email
    (read-merge-write), чтобы не потерять остальные поля - тот же паттерн, что и в
    add_ai_paused_tag.

    Возвращает (success: bool, contact: dict | None, old_email: str | None).
    """
    contact = fetch_full_contact(student_id)
    if contact is None:
        return False, None, None

    old_email = contact.get("email")

    updated_contact = dict(contact)
    updated_contact["email"] = new_email

    # Эта модель API использует Optional<T> для полей-коллекций - сервер требует
    # обёртку {"value": [...]}, а не голый JSON-массив (иначе 400 invalidModel).
    if "tags" in updated_contact:
        updated_contact["tags"] = {"value": updated_contact.get("tags") or []}
    if "groups" in updated_contact:
        updated_contact["groups"] = {"value": updated_contact.get("groups") or []}

    url = f"{PRODAMUS_BASE_URL}/crm/lead"
    headers = {
        "Authorization": f"Bearer {PRODAMUS_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.put(url, headers=headers, json=updated_contact, timeout=10)
        print(f"DEBUG: Update contact (change email) status={response.status_code}")
        print(f"DEBUG: Update contact body: {response.text[:500]}")
        return response.status_code == 200, contact, old_email
    except Exception as e:
        print(f"ERROR: Failed to update contact email: {e}")
        return False, contact, old_email


LICENSE_STATE_LABELS = {
    "active": "включён",
    "paused": "приостановлен администратором",
}

LICENSE_RELEVANCE_LABELS = {
    "past": "период доступа уже закончился",
    "present": "доступ действует прямо сейчас",
    "future": "доступ ещё не начался (начнётся позже)",
}


def fetch_student_course_accesses(student_id):
    """
    Реальный доступ студента к курсам/продуктам в Prodamus - это отдельная сущность
    (StudentLicense = "лицензия"), а НЕ производная от статуса оплаты заказа: оплаченный
    заказ не всегда означает активный доступ прямо сейчас - доступ может быть вручную
    приостановлен (state=paused) или его период ещё не начался/уже закончился
    (relevance=future/past) независимо от оплаты. Отдельного REST-пути для лицензий
    в API нет, но их можно прочитать как вложенное поле контакта.

    GET /api/v1/crm/lead/{id}?fields={studentCourseAccesses{...}}
    """
    fields = (
        "{id,studentCourseAccesses{id,type,state,relevance,beginDate,endDate,"
        "courseId,course{id,name},coursePlanId,coursePlan{id,name},"
        "productId,product{id,name}}}"
    )
    url = f"{PRODAMUS_BASE_URL}/crm/lead/{student_id}"
    headers = {
        "Authorization": f"Bearer {PRODAMUS_API_KEY}",
        "Content-Type": "application/json"
    }
    params = {"fields": fields}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=15)
        print(f"DEBUG: Get student course accesses status={response.status_code}")
        print(f"DEBUG: Get student course accesses body: {response.text[:800]}")

        if response.status_code == 200:
            data = response.json()
            body = data.get("body") or {}
            return body.get("studentCourseAccesses") or []
        return []
    except Exception as e:
        print(f"ERROR: Failed to fetch student course accesses: {e}")
        return []


def build_student_access_text(student_id):
    """
    Формирует текст о РЕАЛЬНОМ доступе ЭТОГО студента к курсам/продуктам на основе его
    лицензий (StudentLicense), а не статуса оплаты заказов. Персональный блок, поэтому
    не кэшируется (в отличие от общего каталога) - запрашивается заново на каждое сообщение.
    """
    accesses = fetch_student_course_accesses(student_id)
    if not accesses:
        return "У этого студента нет ни одной лицензии доступа в Prodamus (или не удалось их получить)."

    lines = []
    for lic in accesses:
        course = lic.get("course") or {}
        product = lic.get("product") or {}
        course_plan = lic.get("coursePlan") or {}

        if course.get("name"):
            name = f"курс «{course['name']}»"
            if course_plan.get("name"):
                name += f", тариф «{course_plan['name']}»"
        elif product.get("name"):
            name = f"продукт «{product['name']}»"
        else:
            name = "неизвестный курс/продукт"

        state_label = LICENSE_STATE_LABELS.get(lic.get("state"), lic.get("state") or "неизвестно")
        relevance_label = LICENSE_RELEVANCE_LABELS.get(lic.get("relevance"), lic.get("relevance") or "")

        end_date = lic.get("endDate")
        end_text = f", доступ до {end_date[:10]}" if end_date else ", без даты окончания (бессрочно)"

        lines.append(f"- {name} — доступ {state_label}, {relevance_label}{end_text}")

    return "\n".join(lines)


ORDER_STATUS_LABELS = {
    "created": "заказ создан, оплата не начата",
    "checkoutData": "оформление не завершено",
    "payment": "ожидает подтверждения оплаты",
    "partiallyPaid": "оплачен частично",
    "paid": "оплачен полностью",
    "preparingShipment": "оплачен",
    "shipped": "оплачен",
    "fulfilled": "оплачен, доступ выдан",
    "canceled": "отменён",
    "refund": "оформлен возврат",
}

# Заказы без единой попытки оплаты - это и есть тот самый "шум" от повторных кликов
# "купить": студент часто создаёт несколько заказов на один и тот же курс, а платит
# только по одному, остальные так и остаются нетронутыми черновиками.
ORDERS_WITHOUT_PAYMENT_ATTEMPT_STATUSES = {"created", "checkoutData"}

# Заказы в этих статусах считаем "с реальной оплатой" - именно такой заказ на продукт
# нужно показывать студенту, если он есть, вместо всех остальных дублей-черновиков.
ORDER_HAS_PAYMENT_STATUSES = {
    "payment", "partiallyPaid", "paid", "preparingShipment", "shipped", "fulfilled", "refund"
}


def fetch_student_orders(student_id):
    """
    POST /api/v1/purchase-order/list с фильтром по studentId - заказы студента со
    статусом оплаты. ВАЖНО: студенты часто создают по несколько заказов на один и тот
    же курс/продукт (повторные попытки оформить заказ), а оплачивают только один -
    остальные так и остаются висеть неоплаченными черновиками. Дедупликация от этого
    шума происходит в build_student_orders_text(), здесь просто сырые данные.
    """
    fields = (
        "{id,status,fullyPaid,partiallyPaid,completed,createdDate,expirationDate,"
        "totalAmount,paidAmount,currency,"
        "payments{id,status,amount,paymentDate,isSuccess,isFail,installmentStatus},"
        "contents{id,productId,product{id,name,courseId,course{id,name}}}}"
    )
    url = f"{PRODAMUS_BASE_URL}/purchase-order/list"
    headers = {
        "Authorization": f"Bearer {PRODAMUS_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {"filter": {"studentId": student_id, "take": 100}, "fields": fields}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        print(f"DEBUG: Get student orders status={response.status_code}")
        print(f"DEBUG: Get student orders body: {response.text[:800]}")

        if response.status_code == 200:
            data = response.json()
            return (data.get("body") or {}).get("items") or []
        return []
    except Exception as e:
        print(f"ERROR: Failed to fetch student orders: {e}")
        return []


def _order_product_names(order):
    names = []
    for c in (order.get("contents") or []):
        product = c.get("product") or {}
        name = product.get("name") or "неизвестный продукт"
        course = (product.get("course") or {})
        if course.get("name"):
            name = f"{name} (курс «{course['name']}»)"
        names.append(name)
    return names


def _order_product_key(order):
    """Ключ группировки дублей - набор товаров в заказе, а не id самого заказа."""
    ids = tuple(sorted(c.get("productId") or "" for c in (order.get("contents") or [])))
    return ids or (order.get("id"),)


SPLIT_PAYMENT_CHECKOUT_URL = "https://store.timofeeva-online.ru/checkout"


def _split_payment_link(order):
    """
    Ссылка на оплату второй половины при сплит-оплате в 2 платежа (только у курсов,
    не у интенсивов/прочих продуктов - см. SCHOOL_INFO_OUTRO). Формат подтверждён
    владельцем школы: .../checkout?orderId=<id>&partValue=<половина полной цены>.

    partiallyPaid бывает и от рассрочки через банк-партнёра (не сплит) - для неё эта
    формула "ровно половина" не годится. Поэтому ссылку формируем только если уже
    оплачено похоже на половину суммы (с небольшим допуском на округление); если нет -
    возвращаем None, и по этому заказу бот по-прежнему зовёт человека.
    """
    if order.get("status") != "partiallyPaid":
        return None

    order_id = order.get("id")
    total_amount = order.get("totalAmount") or 0
    paid_amount = order.get("paidAmount") or 0
    if not order_id or not total_amount:
        return None

    half = total_amount / 2
    if abs(paid_amount - half) > max(total_amount * 0.05, 1):
        return None

    part_value = int(half) if half == int(half) else round(half, 2)
    return f"{SPLIT_PAYMENT_CHECKOUT_URL}?orderId={order_id}&partValue={part_value}"


def build_student_orders_text(student_id):
    """
    Формирует текст о заказах/оплате ЭТОГО студента - с дедупликацией повторных
    неоплаченных попыток. Для каждого набора товаров (курс/продукт) показываем ОДИН
    заказ: если среди дублей есть хоть один с реальной попыткой оплаты - показываем
    его (самый свежий из таких), остальные черновики без оплаты молча скрываем, чтобы
    не путать студента списком из пяти одинаковых заказов. Если оплаты не было вообще -
    показываем самый свежий черновик, чтобы можно было ответить "заказ создан, но не
    оплачен". Персональный блок, не кэшируется - запрашивается заново на каждое сообщение.
    """
    orders = fetch_student_orders(student_id)
    orders = [o for o in orders if not o.get("softDeleted")]
    if not orders:
        return "У этого студента нет заказов в Prodamus (или не удалось их получить)."

    groups = {}
    for order in orders:
        key = _order_product_key(order)
        groups.setdefault(key, []).append(order)

    def sort_key(o):
        return o.get("createdDate") or ""

    lines = []
    for key, group_orders in groups.items():
        paid_attempts = [o for o in group_orders if o.get("status") in ORDER_HAS_PAYMENT_STATUSES]
        if paid_attempts:
            chosen = max(paid_attempts, key=sort_key)
        else:
            chosen = max(group_orders, key=sort_key)

        status_label = ORDER_STATUS_LABELS.get(chosen.get("status"), chosen.get("status") or "неизвестен")
        product_names = _order_product_names(chosen)
        products_text = ", ".join(product_names) if product_names else "товар не определён"

        currency = (chosen.get("currency") or "rub").lower()
        symbol = CURRENCY_SYMBOLS.get(currency, currency.upper())
        paid_amount = chosen.get("paidAmount") or 0
        total_amount = chosen.get("totalAmount") or 0
        amount_text = f"{paid_amount:,.0f} из {total_amount:,.0f} {symbol}".replace(",", " ")

        skipped = len(group_orders) - 1
        skipped_text = f" (плюс ещё {skipped} неоплаченных дублей этого же заказа, не учитываются)" if skipped > 0 else ""

        link = _split_payment_link(chosen)
        link_text = f" | ссылка на оплату второй части: {link}" if link else ""

        lines.append(f"- {products_text}: {status_label}, оплачено {amount_text}{skipped_text}{link_text}")

    return "\n".join(lines)


def call_qwen_api(message_text, student_id, history=None):
    """
    Возвращает (reply_text, needs_human).

    history - список предыдущих сообщений диалога [{"role": "user"/"assistant", "content": "..."}]
    в хронологическом порядке (без текущего сообщения) - даёт модели минимальную память
    о разговоре, а не только о последнем сообщении студента.

    Просим Qwen отвечать строго в формате JSON, чтобы явно понимать,
    нужно ли звать человека, без хрупкого поиска ключевых слов в тексте.
    """

    if not QWEN_API_KEY:
        return "Ошибка: не настроен ключ нейросети.", False

    student_access_text = build_student_access_text(student_id)
    student_orders_text = build_student_orders_text(student_id)

    system_prompt = (
        "Ты - помощник техподдержки онлайн-школы. Отвечай вежливо, кратко и по делу, "
        "используя факты из базы знаний ниже. Если ответа в базе знаний нет или вопрос "
        "требует действия человека (см. раздел про то, когда звать человека) - НЕ придумывай "
        "ответ, а вежливо скажи, что зовёшь специалиста.\n\n"
        + SCHOOL_INFO_INTRO + "\n"
        + get_catalog_text() + "\n"
        + SCHOOL_INFO_OUTRO +
        "\n\nЕсли студент спрашивает про свой личный доступ, какие курсы у него куплены "
        "или до какого числа действует доступ - в конце этого сообщения (после истории "
        "переписки) будет отдельный блок \"ДОСТУП ЭТОГО СТУДЕНТА ПРЯМО СЕЙЧАС\" - отвечай "
        "строго по нему, а не по общему каталогу курсов и не по истории переписки.\n"
        "\n\n=== СТАТУС ЗАКАЗОВ И ОПЛАТЫ ===\n"
        "Если студент спрашивает про статус своего заказа/оплаты, сколько уже оплачено "
        "или прошла ли оплата - в конце этого сообщения будет блок \"ЗАКАЗЫ И ОПЛАТА ЭТОГО "
        "СТУДЕНТА ПРЯМО СЕЙЧАС\" - отвечай по нему. Студенты часто создают несколько заказов "
        "на один и тот же курс, а оплачивают только один - в этом блоке уже отфильтрованы "
        "неоплаченные дубли, показан только реальный (оплаченный, если есть) заказ на каждый "
        "курс/продукт, так что можешь доверять списку как есть, не пересчитывай сам. "
        "Если заказ оплачен частично (статус partiallyPaid) и в этом блоке рядом с заказом "
        "уже есть готовая ссылка на оплату второй части - пришли её студенту прямо в reply, "
        "спрашивать разрешения не нужно. Если статус partiallyPaid, а готовой ссылки НЕТ - "
        "значит это рассрочка через банк-партнёра, а не сплит, для неё ссылку сформировать "
        "нельзя - зови человека (см. раздел \"когда звать человека\"). Ссылку на оплату "
        "заказа, который ещё не оплачен вообще (created/checkoutData), тоже не выдумывай.\n"
        "\n\n=== СМЕНА EMAIL ===\n"
        "Если студент просит исправить/поменять свою почту (например, ошибся при оформлении "
        "заказа) И явно написал новый адрес полностью (вида имя@домен.зона) - положи этот "
        "адрес в поле requestedEmailChange формата ответа ниже. Саму смену и текст "
        "подтверждения сделает код, а не ты - в reply в этом случае просто напиши что-то "
        "нейтральное вроде \"Секунду, обновляю почту...\", не утверждай, что уже сделано. "
        "Если студент просит поменять почту, но НЕ написал новый адрес (или написал что-то "
        "не похожее на email) - НЕ заполняй requestedEmailChange, а в reply попроси прислать "
        "новый адрес полностью.\n"
        "\n\n=== ФОРМАТ ОТВЕТА ===\n"
        "Отвечай СТРОГО в формате JSON, без markdown-разметки и пояснений вокруг, вот так:\n"
        '{"reply": "текст ответа для студента", "needs_human": true или false, '
        '"requestedEmailChange": "новый@адрес.ru или null"}\n\n'
        "needs_human = true, если вопрос попадает в раздел \"когда обязательно звать человека\" "
        "или если ты не можешь уверенно ответить на основе базы знаний.\n"
        "needs_human = false, если ты полностью и уверенно ответил на основе базы знаний.\n"
        "requestedEmailChange = null, если студент не просил сменить email в этом сообщении."
    )

    # Блок доступа приклеивается к ПОСЛЕДНЕМУ user-сообщению (тому самому, на которое модель
    # сейчас отвечает), а не идёт отдельным system-сообщением в середине диалога и не лежит
    # в начале большого system-промпта. Оба этих варианта проверялись на реальном боте и
    # проигрывали истории переписки - модель раз за разом повторяла свои же старые неверные
    # утверждения про доступ, даже на прямой вопрос "ты уверен?". Дописывание свежих данных
    # прямо в тот user-turn, который модель непосредственно обрабатывает - стандартный приём
    # для RAG и на практике оказался единственным надёжным способом.
    access_reminder = (
        "=== ДОСТУП ЭТОГО СТУДЕНТА ПРЯМО СЕЙЧАС (запрошено из Prodamus для ЭТОГО "
        "сообщения, самые свежие данные) ===\n"
        + student_access_text +
        "\n\nЭто АКТУАЛЬНОЕ состояние доступа на данный момент. Доступ мог измениться "
        "с момента более ранних сообщений в этом диалоге (например, курс могли купить "
        "или выдать доступ прямо во время разговора) - если этот блок противоречит "
        "истории переписки выше, включая твои собственные предыдущие ответы, "
        "ПРАВ ЭТОТ БЛОК, а не история. Отвечай студенту, используя именно эти данные."
    )

    orders_reminder = (
        "=== ЗАКАЗЫ И ОПЛАТА ЭТОГО СТУДЕНТА ПРЯМО СЕЙЧАС (запрошено из Prodamus для "
        "ЭТОГО сообщения, самые свежие данные, дубли-черновики без оплаты уже убраны) ===\n"
        + student_orders_text +
        "\n\nЭто АКТУАЛЬНОЕ состояние заказов на данный момент, оно может отличаться от "
        "того, что говорилось раньше в этом диалоге - если противоречит истории переписки "
        "выше, ПРАВ ЭТОТ БЛОК, а не история."
    )

    user_message_with_context = (
        access_reminder + "\n\n" + orders_reminder +
        "\n\n=== СООБЩЕНИЕ СТУДЕНТА ===\n" + message_text
    )

    payload = {
        "model": "qwen-plus",
        "messages": (
            [{"role": "system", "content": system_prompt}]
            + (history or [])
            + [{"role": "user", "content": user_message_with_context}]
        ),
        # Без этого Qwen иногда игнорирует инструкцию про формат ответа и отвечает
        # обычным текстом вместо JSON - тогда парсинг падает и needs_human=True
        # выставляется "на всякий случай" даже если по сути отвечать было не нужно.
        # response_format форсирует валидный JSON на стороне самой модели.
        "response_format": {"type": "json_object"}
    }

    try:
        response = requests.post(
            QWEN_API_URL,
            headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=30
        )
        print(f"DEBUG: Qwen status={response.status_code}")
        response.raise_for_status()
        raw_text = response.json()["choices"][0]["message"]["content"]
        print(f"DEBUG: Qwen raw answer: {raw_text[:200]}")

        # На случай, если модель всё же обернёт JSON в ```json ... ```
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json\n", "", 1).replace("json", "", 1)

        parsed = json.loads(cleaned)
        reply = parsed.get("reply", "Извините, произошла ошибка. Попробуйте позже.")
        needs_human = bool(parsed.get("needs_human", False))
        requested_email = parsed.get("requestedEmailChange")

        print(f"DEBUG: Parsed reply='{reply[:80]}...' needs_human={needs_human} requestedEmailChange={requested_email}")

        # Саму смену email и текст итогового ответа формируем в коде, а не доверяем модели -
        # она не знает заранее, удастся ли реально обновить контакт в Prodamus.
        if requested_email and isinstance(requested_email, str) and requested_email.lower() != "null":
            requested_email = requested_email.strip()
            if not EMAIL_RE.match(requested_email):
                reply = (
                    f"Адрес «{requested_email}» не похож на настоящий email. "
                    "Пришлите, пожалуйста, полный адрес вида имя@домен.ru."
                )
                needs_human = False
            else:
                success, contact, old_email = update_student_email(student_id, requested_email)
                if success:
                    reply = (
                        f"Готово, обновил почту на {requested_email}. "
                        "Письма с доступом и вся переписка теперь будут приходить на новый адрес — "
                        "проверьте, пожалуйста, папку «Спам», если письмо не придёт сразу."
                    )
                    needs_human = False
                    send_telegram_notification(
                        "✅ Бот сменил email студента\n\n"
                        f"ID студента: {student_id}\n"
                        f"Старый email: {old_email or 'неизвестно'}\n"
                        f"Новый email: {requested_email}"
                    )
                else:
                    reply = "Не получилось автоматически поменять почту — сейчас передам это специалисту."
                    needs_human = True

        return reply, needs_human

    except json.JSONDecodeError as e:
        # Если модель вернула не-JSON - используем сырой текст как ответ,
        # и на всякий случай считаем, что человек может понадобиться
        print(f"ERROR: Qwen did not return valid JSON: {e}")
        return raw_text if 'raw_text' in dir() else "Извините, сейчас я не могу ответить. Попробуйте позже.", True
    except Exception as e:
        print(f"ERROR: Qwen failed: {e}")
        return "Извините, сейчас я не могу ответить. Попробуйте позже.", True


CONVERSATION_HISTORY_MESSAGE_COUNT = int(os.getenv("CONVERSATION_HISTORY_MESSAGE_COUNT", "5"))

# Берём с запасом больше, чем нужно для истории: самый свежий элемент - это, как правило,
# само текущее сообщение (Prodamus уже записывает его до вызова вебхука) - его исключаем,
# плюс попадаются пустые/служебные сообщения (например, приветствие сценария).
CONVERSATION_HISTORY_FETCH_COUNT = CONVERSATION_HISTORY_MESSAGE_COUNT + 5


def fetch_recent_channel_messages(chat_channel_id, student_id, take):
    """Последние сообщения канала чата через API Prodamus.

    Реальная структура ответа:
    {
      "success": true,
      "errors": [],
      "body": {
        "items": [
          {
            "id": "...",
            "conversationId": "...",
            "text": "...",
            "user": {"contact": {"id": "...", ...}, "isSystem": bool},
            ...
          },
          ...
        ]
      },
      "resetToken": false
    }

    Элементы идут от НОВЫХ к СТАРЫМ. Сообщения от студента - это те, у которых
    user.contact.id совпадает со studentId; ответы бота/сценария приходят
    с user.isSystem=true и contact=null.

    Используется и для определения conversationId, и для построения истории диалога -
    один вызов API на оба назначения.
    """

    url = f"{PRODAMUS_BASE_URL}/chat-channel/messages/recent"
    params = {
        "chatChannelId": chat_channel_id,
        "studentId": student_id,
        "take": take
    }

    headers = {
        "Authorization": f"Bearer {PRODAMUS_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        print(f"DEBUG: Get recent messages status={response.status_code}")
        print(f"DEBUG: Get recent messages body: {response.text[:800]}")

        if response.status_code == 200:
            data = response.json()
            return (data.get("body") or {}).get("items") or []
        return []
    except Exception as e:
        print(f"ERROR: Failed to get recent messages from API: {e}")
        return []


def extract_conversation_id(items, student_id):
    """conversationId лежит внутри каждого элемента - берём первый, у которого он есть
    (по возможности - совпадающий по studentId)."""

    for item in items:
        contact = (item.get("user") or {}).get("contact") or {}
        if contact.get("id") == student_id and item.get("conversationId"):
            conv_id = item["conversationId"]
            print(f"DEBUG: Found conversationId (matched student)={conv_id}")
            return conv_id

    for item in items:
        if item.get("conversationId"):
            conv_id = item["conversationId"]
            print(f"DEBUG: Found conversationId (first available)={conv_id}")
            return conv_id

    print("DEBUG: No conversation found via API")
    return None


def build_conversation_history(items, student_id, current_message_text, max_messages):
    """
    Превращает последние сообщения канала в список [{"role", "content"}] для Qwen -
    минимальная память бота о разговоре (последние max_messages сообщений).

    items идут от новых к старым - разворачиваем в хронологический порядок.
    Текущее входящее сообщение (оно и так передаётся отдельным user-сообщением)
    исключаем, чтобы не дублировать его в истории.
    """

    history = []
    for item in items:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        contact = (item.get("user") or {}).get("contact") or {}
        role = "user" if contact.get("id") == student_id else "assistant"
        history.append({"role": role, "content": text})

    history.reverse()

    if history and history[-1]["role"] == "user" and history[-1]["content"] == current_message_text:
        history.pop()

    if max_messages:
        history = history[-max_messages:]

    return history


def send_prodamus_message(chat_channel_id, student_id, text, conversation_id=None):
    """Отправляем сообщение в Prodamus"""

    payload = {
        "ChatChannelId": chat_channel_id,
        "StudentId": student_id,
        "Text": text
    }

    if conversation_id:
        payload["ConversationId"] = conversation_id

    print(f"DEBUG: Sending to Prodamus: {payload}")

    try:
        response = requests.post(
            f"{PRODAMUS_BASE_URL}/chat-channel/messages",
            headers={"Authorization": f"Bearer {PRODAMUS_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=10
        )
        print(f"DEBUG: Prodamus status={response.status_code}")
        print(f"DEBUG: Response: {response.text[:500]}")

        if response.status_code != 200:
            print(f"ERROR: Failed to send: {response.text}")
            return False
        return True
    except Exception as e:
        print(f"ERROR: Failed to send: {e}")
        return False


@app.route('/', methods=['POST', 'GET'])
def webhook():
    print("=" * 60)
    print("NEW WEBHOOK REQUEST")
    print("=" * 60)
    print(f"DEBUG: Method={request.method}, Content-Type={request.content_type}, Content-Length={request.content_length}")

    # Токен всегда читаем из query-параметра URL (?token=...), а не из тела запроса -
    # тело содержит сообщение студента и не годится как секрет.
    if WEBHOOK_SECRET:
        provided_token = request.args.get("token", "")
        if not hmac.compare_digest(provided_token, WEBHOOK_SECRET):
            print("WARNING: Webhook request rejected - missing or invalid ?token=")
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = {}

    if request.is_json and request.content_length and request.content_length > 0:
        data = request.get_json(silent=True) or {}

    if not data and request.form:
        data = request.form.to_dict()

    if not data:
        data = request.args.to_dict()

    print(f"DEBUG: Received data: {data}")

    student_id = (
        data.get("studentId") or data.get("StudentId")
        or data.get("contactId")
    )
    message_text = (
        data.get("text") or data.get("Text")
        or data.get("message")
    )
    conversation_id_from_webhook = (
        data.get("chatConversationId") or data.get("conversationId")
        or data.get("ChatConversationId")
    )
    chat_channel_id = (
        data.get("chatChannelId") or data.get("ChatChannelId")
        or DEFAULT_CHAT_CHANNEL_ID
    )
    tags_from_webhook = parse_tags(data.get("tags") or data.get("Tags"))

    print(f"DEBUG: Parsed:")
    print(f"  student_id:       {student_id}")
    print(f"  chat_channel_id:  {chat_channel_id}")
    print(f"  conversation_id:  {conversation_id_from_webhook}")
    print(f"  message_text:     '{message_text}'")
    print(f"  tags_from_webhook: {tags_from_webhook}")

    if not student_id:
        return jsonify({"status": "error", "message": "Missing studentId"}), 400

    # Тестовые запросы из редактора сценария подставляют буквальную строку "null",
    # когда нет реального контекста чата - это не настоящее сообщение
    if str(student_id).lower() == "null":
        print("WARNING: studentId is literal 'null' - looks like a test request, not a real message")
        return jsonify({"status": "ignored", "message": "Test request detected"}), 200

    # Проверка паузы: если тег ai_paused виден в теле вебхука (#Contact.Tags#), это ещё
    # не окончательная правда - на практике подтверждено, что после снятия тега вручную
    # в карточке контакта следующие 1-2 вебхука всё равно приходят с этим тегом в макросе
    # (устаревший снимок на стороне Prodamus). Поэтому в этом случае дополнительно
    # перепроверяем через живой GET /crm/lead - и снимаем паузу, если тега там уже нет.
    # Если вебхук НЕ показывает тег - доверяем этому без лишнего вызова API (быстрый путь).
    if AI_PAUSED_TAG in tags_from_webhook:
        contact_check = fetch_full_contact(student_id)
        if contact_check is None:
            # Не удалось проверить актуальное состояние - на всякий случай остаёмся
            # молчать, чтобы не влезть в разговор, где сейчас работает человек
            print("DEBUG: AI paused per webhook tags, live contact check failed - staying safe, skipping")
            return jsonify({"status": "ignored", "message": "AI paused for this contact (unverified)"}), 200

        live_tags = contact_check.get("tags") or []
        other_tags_from_webhook = [t for t in tags_from_webhook if t != AI_PAUSED_TAG]

        # Второй известный глюк Prodamus (в обратную сторону от первого): GET /crm/lead
        # иногда отдаёт СОВЕРШЕННО ПУСТОЙ список тегов, даже когда у контакта их реально
        # несколько. Если вебхук только что показывал другие теги этого контакта (не
        # только ai_paused), а живой GET вдруг говорит "тегов нет вообще" - это больше
        # похоже на сбой самого GET, чем на то, что все теги разом сняли. В этом случае
        # не доверяем "чистому" результату и подстраховываемся - остаёмся на паузе.
        if not live_tags and other_tags_from_webhook:
            print(
                f"DEBUG: Live contact fetch returned NO tags at all, but webhook showed "
                f"other tags {other_tags_from_webhook} for this contact - looks like a "
                f"stale/broken GET response, not a real tag removal. Staying paused to be safe."
            )
            return jsonify({"status": "ignored", "message": "AI paused for this contact (GET looked unreliable)"}), 200

        if AI_PAUSED_TAG in live_tags:
            print(f"DEBUG: AI is paused for this contact (confirmed via live contact fetch) - skipping")
            return jsonify({"status": "ignored", "message": "AI paused for this contact"}), 200
        print(f"DEBUG: Webhook showed stale '{AI_PAUSED_TAG}' tag, but live contact fetch shows it's removed - resuming")

    # Если текст - макрос или пустой
    if not message_text or "#" in str(message_text):
        print("WARNING: Message text is macro/missing")
        message_text = "Привет! Чем могу помочь?"

    # 0. Одним запросом получаем последние сообщения канала - используем их и для истории
    # диалога (минимальная память бота), и ниже для определения conversationId.
    recent_items = fetch_recent_channel_messages(
        chat_channel_id, student_id, take=CONVERSATION_HISTORY_FETCH_COUNT
    )
    conversation_history = build_conversation_history(
        recent_items, student_id, message_text, CONVERSATION_HISTORY_MESSAGE_COUNT
    )
    print(f"DEBUG: Conversation history ({len(conversation_history)} messages): {conversation_history}")

    # 1. Получаем ответ от Qwen (с учётом общей базы знаний школы, доступа этого студента
    # и истории последних сообщений диалога)
    print(f"DEBUG: Calling Qwen with: '{message_text[:80]}...'")
    ai_response, needs_human = call_qwen_api(message_text, student_id, conversation_history)
    print(f"DEBUG: AI response: '{ai_response[:80]}...' needs_human={needs_human}")

    # 1.5 Если нужен человек - читаем контакт через API (email/имя + база для read-merge-write),
    # ставим тег паузы и уведомляем в Telegram. Контакт читаем только сейчас, а не для
    # каждого сообщения - экономим вызовы API в обычном случае.
    if needs_human:
        contact = fetch_full_contact(student_id)
        add_ai_paused_tag(contact, known_current_tags=tags_from_webhook)
        notify_human(contact, student_id, message_text, ai_response)

    # 2. Получаем conversationId - из вебхука, а если там макрос/пусто, то из уже
    # полученных recent_items (без повторного вызова API)
    conversation_id = conversation_id_from_webhook

    if not conversation_id or "#" in str(conversation_id):
        conversation_id = extract_conversation_id(recent_items, student_id)

    print(f"DEBUG: Final conversation_id: {conversation_id}")

    # 3. Отправляем в Prodamus
    success = send_prodamus_message(chat_channel_id, student_id, ai_response, conversation_id)

    if success:
        print("SUCCESS: Message sent!")
        return jsonify({"status": "success"}), 200
    else:
        print("ERROR: Failed to send")
        return jsonify({"status": "error", "message": "Failed to send to Prodamus"}), 500


if __name__ == '__main__':
    app.run(debug=True, port=3000)
