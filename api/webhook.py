from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# Переменные окружения (настроим в Vercel Dashboard)
PRODAMUS_API_KEY = os.getenv("PRODAMUS_API_KEY")
QWEN_API_KEY = os.getenv("QWEN_API_KEY")
PRODAMUS_BASE_URL = "https://api.xl.ru/api/v1" # Исправлено на api.xl.ru согласно доке
QWEN_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

HEADERS_PRODAMUS = {
    "Authorization": f"Bearer {PRODAMUS_API_KEY}",
    "Content-Type": "application/json"
}

HEADERS_QWEN = {
    "Authorization": f"Bearer {QWEN_API_KEY}",
    "Content-Type": "application/json"
}

def call_qwen_api(message_text):
    payload = {
        "model": "qwen-turbo",
        "input": {
            "messages": [
                {"role": "system", "content": "Ты - помощник техподдержки школы. Отвечай вежливо и кратко."},
                {"role": "user", "content": message_text}
            ]
        },
        "parameters": {"result_format": "message"}
    }
    try:
        response = requests.post(QWEN_API_URL, headers=HEADERS_QWEN, json=payload)
        response.raise_for_status()
        return response.json().get("output", {}).get("choices", [{}])[0].get("message", {}).get("content")
    except Exception as e:
        print(f"Error calling Qwen API: {e}")
        return "Извините, сейчас я не могу ответить."

def send_prodamus_message(chat_channel_id, student_id, text):
    payload = {
        "chatChannelId": chat_channel_id,
        "studentId": student_id,
        "text": text
    }
    try:
        # POST /api/v1/chat-channel/messages
        response = requests.post(f"{PRODAMUS_BASE_URL}/chat-channel/messages", headers=HEADERS_PRODAMUS, json=payload)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error sending Prodamus message: {e}")
        return False

@app.route('/', methods=['POST'])
def webhook():
    data = request.json
    print(f"Received webhook: {data}")
    
    # 1. Извлекаем данные из сообщения Продамуса
    # ВАЖНО: нужно проверить реальную структуру JSON, которую шлет Продамус
    # Предполагаем структуру на основе API:
    chat_channel_id = data.get("chatChannelId")
    student_id = data.get("studentId")
    message_text = data.get("text")
    
    if not chat_channel_id or not student_id or not message_text:
        return jsonify({"status": "error", "message": "Missing required fields"}), 400

    # 2. Получаем ответ от Qwen
    ai_response = call_qwen_api(message_text)
    
    # 3. Отправляем ответ в Продамус
    success = send_prodamus_message(chat_channel_id, student_id, ai_response)
    
    if success:
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"status": "error", "message": "Failed to send response"}), 500
