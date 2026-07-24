from flask import Flask, request, jsonify
import requests
import os
import time

app = Flask(__name__)

PRODAMUS_API_KEY = os.getenv("PRODAMUS_API_KEY")
QWEN_API_KEY = os.getenv("QWEN_API_KEY")
PRODAMUS_BASE_URL = "https://api.xl.ru/api/v1"
QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"


def call_qwen_api(message_text):
    if not QWEN_API_KEY:
        print("ERROR: QWEN_API_KEY is not set!")
        return "Ошибка: не настроен ключ нейросети."

    payload = {
        "model": "qwen-plus",
        "messages": [
            {"role": "system", "content": "Ты - помощник техподдержки школы. Отвечай вежливо и кратко."},
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
        ai_text = response.json()["choices"][0]["message"]["content"]
        print(f"DEBUG: Qwen answer: {ai_text[:100]}")
        return ai_text
    except Exception as e:
        print(f"ERROR: Qwen API failed: {e}")
        return "Извините, сейчас я не могу ответить. Попробуйте позже."


def get_chat_channels(student_id=None):
    """Получаем список чат-каналов"""
    url = f"{PRODAMUS_BASE_URL}/chat-channel"
    params = {}
    if student_id:
        # Пробуем разные варианты фильтрации
        params["externalId"] = student_id
    
    headers = {
        "Authorization": f"Bearer {PRODAMUS_API_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        print(f"DEBUG: Get channels status={response.status_code}")
        print(f"DEBUG: Get channels response: {response.text[:500]}")
        
        if response.status_code == 200:
            data = response.json()
            # Prodamus может возвращать данные в разных форматах
            if isinstance(data, dict):
                return data.get("value", []) or data.get("data", []) or [data]
            elif isinstance(data, list):
                return data
        return []
    except Exception as e:
        print(f"ERROR: Failed to get chat channels: {e}")
        return []


def get_recent_messages(chat_channel_id):
    """Получаем последние сообщения из канала"""
    url = f"{PRODAMUS_BASE_URL}/chat-channel/messages/recent"
    params = {"chatChannelId": chat_channel_id}
    
    headers = {
        "Authorization": f"Bearer {PRODAMUS_API_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        print(f"DEBUG: Get messages status={response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict):
                return data.get("value", []) or data.get("data", []) or [data]
            elif isinstance(data, list):
                return data
        return []
    except Exception as e:
        print(f"ERROR: Failed to get messages: {e}")
        return []


def send_prodamus_message(chat_channel_id, student_id, text, conversation_id):
    if not PRODAMUS_API_KEY:
        print("ERROR: PRODAMUS_API_KEY is not set!")
        return False

    payload = {
        "ChatChannelId": chat_channel_id,
        "StudentId": student_id,
        "Text": text,
        "ConversationId": conversation_id
    }

    print(f"DEBUG: Sending to Prodamus: {payload}")

    try:
        response = requests.post(
            f"{PRODAMUS_BASE_URL}/chat-channel/messages",
            headers={"Authorization": f"Bearer {PRODAMUS_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=10
        )
        print(f"DEBUG: Prodamus send status={response.status_code}")
        print(f"DEBUG: Prodamus send response: {response.text[:500]}")
        
        if response.status_code != 200:
            print(f"ERROR: Prodamus {response.status_code}: {response.text}")
            return False
        return True
    except Exception as e:
        print(f"ERROR: Prodamus request failed: {e}")
        return False


@app.route('/', methods=['POST', 'GET'])
def webhook():
    print("=" * 60)
    print("NEW WEBHOOK REQUEST")
    print("=" * 60)

    # Читаем данные из любого источника
    data = {}
    
    if request.is_json and request.content_length and request.content_length > 0:
        data = request.get_json(silent=True) or {}
    
    if not data and request.form:
        data = request.form.to_dict()
    
    if not data:
        data = request.args.to_dict()

    print(f"DEBUG: Received data: {data}")

    # Извлекаем studentId (он всегда приходит правильно)
    student_id = (
        data.get("studentId") or data.get("StudentId")
        or data.get("student_id") or data.get("contactId")
    )

    # Пытаемся получить chatChannelId и conversationId из данных
    chat_channel_id = (
        data.get("chatChannelId") or data.get("ChatChannelId")
        or data.get("channelId") or data.get("chat_channel_id")
    )
    conversation_id = (
        data.get("chatConversationId") or data.get("ChatConversationId")
        or data.get("conversationId") or data.get("chat_conversation_id")
    )
    message_text = (
        data.get("text") or data.get("Text")
        or data.get("message") or data.get("message_text")
    )

    print(f"DEBUG: Parsed:")
    print(f"  student_id:        {student_id}")
    print(f"  chat_channel_id:   {chat_channel_id}")
    print(f"  conversation_id:   {conversation_id}")
    print(f"  message_text:      '{message_text}'")

    # Если studentId нет — ошибка
    if not student_id:
        print("ERROR: studentId is missing!")
        return jsonify({"status": "error", "message": "Missing studentId"}), 400

    # Если chatChannelId или conversationId содержат макросы — нужно запросить через API
    needs_api_lookup = (
        "#" in str(chat_channel_id) or 
        "#" in str(conversation_id) or 
        not chat_channel_id or 
        not conversation_id
    )

    if needs_api_lookup:
        print("DEBUG: Need to lookup chat channel via API...")
        
        # Получаем список каналов
        channels = get_chat_channels(student_id)
        print(f"DEBUG: Found {len(channels)} channels")
        
        if channels:
            # Берём первый активный канал
            for channel in channels:
                if isinstance(channel, dict):
                    cid = channel.get("id") or channel.get("Id") or channel.get("chatChannelId")
                    if cid and "#" not in str(cid):
                        chat_channel_id = cid
                        
                        # Пробуем получить conversationId из канала
                        conv_id = channel.get("conversationId") or channel.get("ConversationId") or channel.get("id")
                        if conv_id and "#" not in str(conv_id):
                            conversation_id = conv_id
                        break
        
        print(f"DEBUG: After API lookup:")
        print(f"  chat_channel_id:   {chat_channel_id}")
        print(f"  conversation_id:   {conversation_id}")

    # Если текст содержит макрос — значит Prodamus не передал сообщение
    # В этом случае нужно получить последнее сообщение из канала
    if "#" in str(message_text) or not message_text:
        print("DEBUG: Message text is macro, getting recent messages...")
        if chat_channel_id and "#" not in str(chat_channel_id):
            messages = get_recent_messages(chat_channel_id)
            print(f"DEBUG: Found {len(messages)} recent messages")
            
            if messages:
                # Берём последнее сообщение от студента
                for msg in reversed(messages):
                    if isinstance(msg, dict):
                        msg_text = msg.get("text") or msg.get("Text") or msg.get("message")
                        sender = msg.get("senderId") or msg.get("SenderId") or msg.get("from")
                        
                        # Если сообщение от студента (не от бота)
                        if msg_text and sender == student_id:
                            message_text = msg_text
                            break
            
            print(f"DEBUG: Extracted message: '{message_text}'")

    # Финальная проверка обязательных полей
    missing = []
    if not conversation_id or "#" in str(conversation_id):
        missing.append("conversationId")
    if not chat_channel_id or "#" in str(chat_channel_id):
        missing.append("chatChannelId")
    if not message_text or "#" in str(message_text):
        missing.append("text")

    if missing:
        print(f"ERROR: Still missing fields after API lookup: {missing}")
        return jsonify({
            "status": "error",
            "message": f"Cannot resolve: {', '.join(missing)}. Check Prodamus API permissions."
        }), 400

    # 1. Получаем ответ от Qwen
    print(f"DEBUG: Calling Qwen with: '{message_text[:80]}...'")
    ai_response = call_qwen_api(message_text)
    print(f"DEBUG: AI response: '{ai_response[:80]}...'")

    # 2. Отправляем в Prodamus
    success = send_prodamus_message(chat_channel_id, student_id, ai_response, conversation_id)

    if success:
        print("SUCCESS: Message sent to Prodamus")
        return jsonify({"status": "success"}), 200
    else:
        print("ERROR: Failed to send to Prodamus")
        return jsonify({"status": "error", "message": "Failed to send to Prodamus"}), 500


if __name__ == '__main__':
    app.run(debug=True, port=3000)
