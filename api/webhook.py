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
    print(f"DEBUG: Sending to Prodamus: URL={PRODAMUS_BASE_URL}/chat-channel/messages, Payload={payload}, Headers={HEADERS_PRODAMUS}")
    try:
        # POST /api/v1/chat-channel/messages
        response = requests.post(f"{PRODAMUS_BASE_URL}/chat-channel/messages", headers=HEADERS_PRODAMUS, json=payload)
        if response.status_code != 200:
            print(f"DEBUG: Prodamus error response: {response.text}")
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error sending Prodamus message: {e}")
        return False

@app.route('/', methods=['POST'])
def webhook():
    # Log detailed request info
    content_length = request.content_length
    content_type = request.content_type
    print(f"DEBUG: POST received. Content-Length: {content_length}, Content-Type: {content_type}")

    if content_length == 0:
        return jsonify({"status": "error", "message": "Empty request body"}), 400

    data = request.get_json()
    print(f"DEBUG: Received JSON payload: {data}")
    
    if not data:
        # If get_json failed, it might be because the Content-Type is not application/json
        # or the body is not valid JSON.
        raw_data = request.get_data(as_text=True)
        print(f"DEBUG: Raw data that failed to parse: {raw_data}")
        return jsonify({"status": "error", "message": "Failed to parse JSON"}), 400
    
    # Based on the Prodamus API docs for 'POST /api/v1/chat-channel/messages'
    # we need chatChannelId, studentId, and text. 
    chat_channel_id = data.get("chatChannelId")
    student_id = data.get("studentId")
    message_text = data.get("text")
    
    print(f"DEBUG: Parsed - chat_channel_id: {chat_channel_id}, student_id: {student_id}, text: {message_text}")
    
    if not chat_channel_id or not student_id or not message_text:
        return jsonify({"status": "error", "message": "Missing required fields: chatChannelId, studentId, text"}), 400
    
    # 2. Получаем ответ от Qwen
    ai_response = call_qwen_api(message_text)
    
    # 3. Отправляем ответ в Продамус
    success = send_prodamus_message(chat_channel_id, student_id, ai_response)
    
    if success:
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"status": "error", "message": "Failed to send response to Prodamus"}), 500
