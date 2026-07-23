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
    print(f"DEBUG: Received webhook payload: {data}")
    
    # Prodamus webhooks often have data nested or use specific field names.
    # Based on the need to identify the contact, let's try to extract common fields.
    # Adjust these keys based on what appears in the Vercel logs.
    
    # Let's try to find message and contact info
    message_text = data.get("text") or data.get("message", {}).get("text")
    chat_channel_id = data.get("chatChannelId")
    # Mapping the ID to the field Prodamus uses to identify the contact
    student_id = data.get("studentId") or data.get("contact", {}).get("id")
    
    print(f"DEBUG: Parsed - chat_channel_id: {chat_channel_id}, student_id: {student_id}, text: {message_text}")
    
    if not message_text:
        return jsonify({"status": "error", "message": "Missing message text"}), 400

    # If channel/student ID are missing, we might not be able to reply, but let's log it
    if not chat_channel_id or not student_id:
        print("WARNING: Missing chatChannelId or studentId for reply.")
        # Depending on the webhook type, this might be okay.
        # But for auto-reply, we usually need these.
    
    # 2. Получаем ответ от Qwen
    ai_response = call_qwen_api(message_text)
    
    # 3. Отправляем ответ в Продамус (только если есть куда отправлять)
    if chat_channel_id and student_id:
        success = send_prodamus_message(chat_channel_id, student_id, ai_response)
        if success:
            return jsonify({"status": "success"}), 200
        else:
            return jsonify({"status": "error", "message": "Failed to send response"}), 500
    
    return jsonify({"status": "success", "message": "Processed but no reply sent (missing IDs)"}), 200
