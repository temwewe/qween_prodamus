from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

PRODAMUS_API_KEY = os.getenv("PRODAMUS_API_KEY")
QWEN_API_KEY = os.getenv("QWEN_API_KEY")
PRODAMUS_BASE_URL = "https://api.xl.ru/api/v1"
QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"

# ID из ссылки бота
CHAT_CHANNEL_ID = os.getenv("CHAT_CHANNEL_ID", "106540")


def call_qwen_api(message_text):
    if not QWEN_API_KEY:
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
        print(f"ERROR: Qwen failed: {e}")
        return "Извините, сейчас я не могу ответить. Попробуйте позже."


def send_prodamus_message(student_id, text):
    """Отправляем сообщение без ConversationId — Prodamus сам найдёт диалог"""
    
    # Пробуем сначала без ConversationId
    payload = {
        "ChatChannelId": int(CHAT_CHANNEL_ID),
        "StudentId": student_id,
        "Text": text
    }

    print(f"DEBUG: Sending to Prodamus: {payload}")

    try:
        response = requests.post(
            f"{PRODAMUS_BASE_URL}/chat-channel/messages",
            headers={"Authorization": f"Bearer {PRODAMUS_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=10
        )
        print(f"DEBUG: Prodamus status={response.status_code}")
        print(f"DEBUG: Prodamus response: {response.text[:500]}")
        
        # Если не получилось без ConversationId — пробуем с ChatBotId
        if response.status_code != 200:
            print("DEBUG: Trying with ConversationId=102158...")
            payload["ConversationId"] = "102158"
            
            response = requests.post(
                f"{PRODAMUS_BASE_URL}/chat-channel/messages",
                headers={"Authorization": f"Bearer {PRODAMUS_API_KEY}", "Content-Type": "application/json"},
                json=payload,
                timeout=10
            )
            print(f"DEBUG: Prodamus retry status={response.status_code}")
            print(f"DEBUG: Prodamus retry response: {response.text[:500]}")
        
        if response.status_code != 200:
            print(f"ERROR: Prodamus {response.status_code}: {response.text}")
            return False
        return True
    except Exception as e:
        print(f"ERROR: Prodamus failed: {e}")
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

    # Извлекаем поля
    student_id = (
        data.get("studentId") or data.get("StudentId") 
        or data.get("student") or data.get("contactId")
    )
    message_text = (
        data.get("text") or data.get("Text") 
        or data.get("msg") or data.get("message")
    )

    print(f"DEBUG: Parsed:")
    print(f"  student_id:   {student_id}")
    print(f"  message_text: '{message_text}'")

    # Проверка обязательных полей
    if not student_id:
        print("ERROR: Missing studentId!")
        return jsonify({"status": "error", "message": "Missing studentId"}), 400
    
    # Если текст — макрос или пустой, используем заглушку
    if not message_text or "#" in str(message_text):
        print("WARNING: Message text is macro/missing, using default")
        message_text = "Привет! Чем могу помочь?"

    # 1. Получаем ответ от Qwen
    print(f"DEBUG: Calling Qwen with: '{message_text[:80]}...'")
    ai_response = call_qwen_api(message_text)
    print(f"DEBUG: AI response: '{ai_response[:80]}...'")

    # 2. Отправляем в Prodamus
    success = send_prodamus_message(student_id, ai_response)

    if success:
        print("SUCCESS: Message sent!")
        return jsonify({"status": "success"}), 200
    else:
        print("ERROR: Failed to send")
        return jsonify({"status": "error", "message": "Failed to send to Prodamus"}), 500


if __name__ == '__main__':
    app.run(debug=True, port=3000)
