from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

PRODAMUS_API_KEY = os.getenv("PRODAMUS_API_KEY")
QWEN_API_KEY = os.getenv("QWEN_API_KEY")
PRODAMUS_BASE_URL = "https://api.xl.ru/api/v1"
QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"

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


def send_prodamus_message(student_id, text, conversation_id=None):
    """Отправляем сообщение в Prodamus"""
    
    payload = {
        "ChatChannelId": int(CHAT_CHANNEL_ID),
        "StudentId": student_id,
        "Text": text
    }
    
    # Если conversation_id предоставлен Prodamus — используем его
    if conversation_id and "#" not in str(conversation_id):
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
        or data.get("contactId")
    )
    message_text = (
        data.get("text") or data.get("Text") 
        or data.get("message")
    )
    conversation_id = (
        data.get("chatConversationId") or data.get("conversationId")
        or data.get("ChatConversationId")
    )

    print(f"DEBUG: Parsed:")
    print(f"  student_id:       {student_id}")
    print(f"  conversation_id:  {conversation_id}")
    print(f"  message_text:     '{message_text}'")

    if not student_id:
        return jsonify({"status": "error", "message": "Missing studentId"}), 400
    
    # Если текст — макрос или пустой
    if not message_text or "#" in str(message_text):
        print("WARNING: Message text is macro/missing")
        message_text = "Привет! Чем могу помочь?"

    # 1. Получаем ответ от Qwen
    print(f"DEBUG: Calling Qwen with: '{message_text[:80]}...'")
    ai_response = call_qwen_api(message_text)
    print(f"DEBUG: AI response: '{ai_response[:80]}...'")

    # 2. Отправляем в Prodamus с conversation_id если есть
    success = send_prodamus_message(student_id, ai_response, conversation_id)

    if success:
        print("SUCCESS: Message sent!")
        return jsonify({"status": "success"}), 200
    else:
        print("ERROR: Failed to send")
        return jsonify({"status": "error", "message": "Failed to send to Prodamus"}), 500


if __name__ == '__main__':
    app.run(debug=True, port=3000)
