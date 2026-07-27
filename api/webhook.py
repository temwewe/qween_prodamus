from flask import Flask, request, jsonify
import requests
import os
import json

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

# ============================================================
# ОБЩАЯ БАЗА ЗНАНИЙ О ШКОЛЕ TIMOFEEVA-ONLINE
# Этот текст видит нейросеть при ответе на КАЖДОЕ сообщение
# от ЛЮБОГО студента. Редактируйте прямо здесь и деплойте заново,
# чтобы обновить данные (даты стартов, цены, статусы продуктов и т.д.)
# ============================================================
SCHOOL_INFO = """
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

=== КУРСЫ (3) ===

1) «Дифференциальная УЗД лимфаденопатий» (иногда называют коротко «ЛАП»)
Описание: разбор вопросов от настроек аппарата до дифференциальной диагностики
метастазов, лимфаденитов и реактивных изменений.
Страница курса: https://timofeeva-online.ru/lymph2
Лист ожидания (если сейчас не в продаже): https://store.timofeeva-online.ru/wlymph
Старт ближайшего потока: февраль 2027
Тарифы:
  - Базовый: доступ 2 месяца, от 12 900 ₽. Без онлайн-встреч.
  - Погружение: доступ 3 месяца, от 14 900 ₽. Включает "лимфопедию" (атлас УЗ-изображений
    поверхностных лимфоузлов), мастер-урок с примерами протоколов УЗИ, больше тем уроков,
    есть одна онлайн-встреча, гайд (памятка) по всему курсу, её можно скачать.

2) «УЗИ щитовидной железы: от нормы до патологии»
Описание: разбор сложных тем и спорных вопросов — EU TI-RADS, тиреоидиты, узловые
образования, лимфоузлы, эластография, гайд (памятка) по всему курсу (можно скачать)
и протоколы описания. В курс входит атлас "Эхограммы верифицированных узлов щитовидной железы".
Длительность: 3 месяца. Цена: от 14 500 ₽. Один тариф (без деления).
Страница курса: https://timofeeva-online.ru/thyroid
Лист ожидания: https://store.timofeeva-online.ru/wthyroid
Старт ближайшего потока: 8 сентября 2026
Также может быть полезен врачам-эндокринологам (не только УЗД).

3) «Селезёнка — забытый остров»
Описание: онлайн-курс о самом недооценённом органе брюшной полости — от анатомии
и сосудов до травм и редких казусов, есть чат курса.
Длительность: 3 месяца. Цена: от 6 000 ₽. Один тариф, без онлайн-встреч.
Страница курса: https://timofeeva-online.ru/spleen
Лист ожидания: https://store.timofeeva-online.ru/wspleen
Статус: сейчас не в открытой продаже, активного набора на новый поток нет.

=== ИНТЕНСИВЫ (3, каждый длится 2 недели, без фиксированных дат старта — доступны всегда) ===

1) «Уровни лимфоузлов шеи» — 1 850 ₽, доступен
   Разбор: зачем и как пользоваться делением шеи на уровни при УЗИ лимфоузлов.

2) «O-RADS: от признака до категории» — от 3 900 ₽, статус "скоро"
   Разбор подводных камней и сложностей системы O-RADS.

3) «EU TI-RADS: от признака к категории» — 2 500 ₽, статус "пока не доступен"
   Содержание: 10 микро-уроков по 5-10 минут, перевод гайдлайна EU TI-RADS 2017,
   пример структурированного протокола, закрытый чат с автором курса.

=== ДРУГИЕ ПРОДУКТЫ ===

- Чек-лист по описанию грудных имплантатов на УЗИ — 1 000 ₽, доступен, даётся навсегда.
  Содержит пошаговый алгоритм и визуальные примеры нормы и патологии.

- Закрытый Telegram-канал УЗ-диагноста (алгоритмы, библиотека, комьюнити) —
  5 500 ₽/год, доступ на 1 год, 1000+ материалов.
  Доступ выдаётся сразу после оплаты — ссылка на вступление приходит на почту, указанную
  при заказе. Внутри библиотеки уже 1000+ материалов: готовые алгоритмы по УЗД в гинекологии,
  уронефрологии, гепатобилиарной зоны и поверхностно расположенных органов на основе
  отечественных и зарубежных рекомендаций.

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
это отдельный от рассрочки способ (сплит от платформы школы).
Если студент просит ссылку на оплату оставшейся (второй) части — бот НЕ должен пытаться
сформировать её сам, нужно звать человека, который пришлёт ссылку.

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
О: Сказать, что сейчас позовёт технического специалиста, и он пришлёт ссылку. Самому ссылку
   не формировать.

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
О: Позвать человека для решения проблемы, а пока попросить студента прислать правильную/
   действующую почту.

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
- Запрос ссылки на оплату оставшейся части (рассрочка/сплит)
- Ошибка в данных сертификата или в email при оформлении заказа
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


def fetch_full_contact(student_id):
    """
    Читаем контакт целиком через CRM API Prodamus - нужно для двух вещей:
    1) проверить, не стоит ли уже тег AI_PAUSED_TAG (тогда бот молчит)
    2) получить email/имя для уведомления, и текущие поля контакта,
       чтобы потом безопасно вернуть их обратно при PUT (не потерять данные)

    ВАЖНО: поле "attributes" сюда намеренно НЕ включено - оно вложенная коллекция
    пользовательских атрибутов и требует явного указания вложенных полей
    (иначе API отвечает 500 "Nested fields ... must be specified"). Для наших целей
    (проверка/простановка тега) оно и не нужно.

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


def contact_is_ai_paused(contact):
    if not contact:
        return False
    tags = contact.get("tags") or []
    return AI_PAUSED_TAG in tags


def add_ai_paused_tag(contact):
    """
    Добавляем тег AI_PAUSED_TAG к уже прочитанному контакту и отправляем
    ВЕСЬ объект контакта обратно через PUT (read-merge-write), чтобы не
    затереть остальные поля контакта (email, phone, groups и т.д.)
    """

    if not contact:
        return False

    tags = list(contact.get("tags") or [])
    if AI_PAUSED_TAG not in tags:
        tags.append(AI_PAUSED_TAG)

    updated_contact = dict(contact)
    updated_contact["tags"] = tags

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


def notify_human(contact, student_id, message_text, ai_reply):
    """Отправляем уведомление всем настроенным админам в Telegram, что нужен человек"""

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_IDS:
        print("WARNING: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_IDS not configured, skipping notification")
        return False

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


def call_qwen_api(message_text):
    """
    Возвращает (reply_text, needs_human).

    Просим Qwen отвечать строго в формате JSON, чтобы явно понимать,
    нужно ли звать человека, без хрупкого поиска ключевых слов в тексте.
    """

    if not QWEN_API_KEY:
        return "Ошибка: не настроен ключ нейросети.", False

    system_prompt = (
        "Ты - помощник техподдержки онлайн-школы. Отвечай вежливо, кратко и по делу, "
        "используя факты из базы знаний ниже. Если ответа в базе знаний нет или вопрос "
        "требует действия человека (см. раздел про то, когда звать человека) - НЕ придумывай "
        "ответ, а вежливо скажи, что зовёшь специалиста.\n\n"
        + SCHOOL_INFO +
        "\n\n=== ФОРМАТ ОТВЕТА ===\n"
        "Отвечай СТРОГО в формате JSON, без markdown-разметки и пояснений вокруг, вот так:\n"
        '{"reply": "текст ответа для студента", "needs_human": true или false}\n\n'
        "needs_human = true, если вопрос попадает в раздел \"когда обязательно звать человека\" "
        "или если ты не можешь уверенно ответить на основе базы знаний.\n"
        "needs_human = false, если ты полностью и уверенно ответил на основе базы знаний."
    )

    payload = {
        "model": "qwen-plus",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message_text}
        ]
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

        print(f"DEBUG: Parsed reply='{reply[:80]}...' needs_human={needs_human}")
        return reply, needs_human

    except json.JSONDecodeError as e:
        # Если модель вернула не-JSON - используем сырой текст как ответ,
        # и на всякий случай считаем, что человек может понадобиться
        print(f"ERROR: Qwen did not return valid JSON: {e}")
        return raw_text if 'raw_text' in dir() else "Извините, сейчас я не могу ответить. Попробуйте позже.", True
    except Exception as e:
        print(f"ERROR: Qwen failed: {e}")
        return "Извините, сейчас я не могу ответить. Попробуйте позже.", True


def get_conversation_id(chat_channel_id, student_id):
    """Получаем conversationId через API Prodamus.

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
            "user": {"contact": {"id": "...", ...}},
            ...
          },
          ...
        ]
      },
      "resetToken": false
    }

    conversationId лежит внутри каждого элемента body.items - берём первый элемент,
    у которого он есть (по возможности - совпадающий по studentId).
    """

    url = f"{PRODAMUS_BASE_URL}/chat-channel/messages/recent"
    params = {
        "chatChannelId": chat_channel_id,
        "studentId": student_id,
        "take": 5
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
            items = (data.get("body") or {}).get("items") or []

            # Сначала пробуем найти сообщение именно от нужного студента
            for item in items:
                contact = (item.get("user") or {}).get("contact") or {}
                if contact.get("id") == student_id and item.get("conversationId"):
                    conv_id = item["conversationId"]
                    print(f"DEBUG: Found conversationId (matched student)={conv_id}")
                    return conv_id

            # Если не нашли точное совпадение - берём первый попавшийся conversationId
            for item in items:
                if item.get("conversationId"):
                    conv_id = item["conversationId"]
                    print(f"DEBUG: Found conversationId (first available)={conv_id}")
                    return conv_id

        print("DEBUG: No conversation found via API")
        return None
    except Exception as e:
        print(f"ERROR: Failed to get conversation from API: {e}")
        return None


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

    print(f"DEBUG: Parsed:")
    print(f"  student_id:       {student_id}")
    print(f"  chat_channel_id:  {chat_channel_id}")
    print(f"  conversation_id:  {conversation_id_from_webhook}")
    print(f"  message_text:     '{message_text}'")

    if not student_id:
        return jsonify({"status": "error", "message": "Missing studentId"}), 400

    # Тестовые запросы из редактора сценария подставляют буквальную строку "null",
    # когда нет реального контекста чата - это не настоящее сообщение
    if str(student_id).lower() == "null":
        print("WARNING: studentId is literal 'null' - looks like a test request, not a real message")
        return jsonify({"status": "ignored", "message": "Test request detected"}), 200

    # Читаем контакт один раз - используем и для проверки паузы, и для email/имени в уведомлении,
    # и как базу для безопасного read-merge-write при простановке тега
    contact = fetch_full_contact(student_id)

    # Если бот уже на паузе для этого контакта (человек ведёт диалог вручную) - не отвечаем вообще
    if contact_is_ai_paused(contact):
        print(f"DEBUG: AI is paused for this contact (tag '{AI_PAUSED_TAG}' present) - skipping")
        return jsonify({"status": "ignored", "message": "AI paused for this contact"}), 200

    # Если текст - макрос или пустой
    if not message_text or "#" in str(message_text):
        print("WARNING: Message text is macro/missing")
        message_text = "Привет! Чем могу помочь?"

    # 1. Получаем ответ от Qwen (с учётом общей базы знаний школы)
    print(f"DEBUG: Calling Qwen with: '{message_text[:80]}...'")
    ai_response, needs_human = call_qwen_api(message_text)
    print(f"DEBUG: AI response: '{ai_response[:80]}...' needs_human={needs_human}")

    # 1.5 Если нужен человек - ставим контакту тег паузы и уведомляем в Telegram
    if needs_human:
        add_ai_paused_tag(contact)
        notify_human(contact, student_id, message_text, ai_response)

    # 2. Получаем conversationId через API если нет из вебхука
    conversation_id = conversation_id_from_webhook

    if not conversation_id or "#" in str(conversation_id):
        print("DEBUG: Getting conversationId via API...")
        conversation_id = get_conversation_id(chat_channel_id, student_id)

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
