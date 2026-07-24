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

def send_prodamus_message(chat_channel_id, student_id, text, conversation_id=None):
    # Prodamus error log indicates 'ConversationId' is required.
    # If not provided, we must determine it. 
    # Let's try sending conversationId if we have it, 
    # or leave it out if we don't (though the API says it's required).
    payload = {
        "chatChannelId": chat_channel_id,
        "studentId": student_id,
        "text": text
    }
    if conversation_id:
        payload["conversationId"] = conversation_id
        
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
        return jsonify({"status": "error", "message": "Failed to parse JSON"}), 400
    
    # Based on the log provided by the user:
    # {
    #   "studentId": "rJpHtD2ug027qwoaXOlzYQ",
    #   "chatBotId": "hDGeDv-n20OeH0Aybq3F1A",
    #   "chatConversationId": "ZV_S73nN6EakWjivGKih6A"
    # }
    # Also need text, presumably from the webhook trigger event.
    
    chat_channel_id = data.get("chatChannelId") # If still present
    conversation_id = data.get("chatConversationId")
    student_id = data.get("studentId")
    message_text = data.get("text") # Assuming 'text' is sent in the webhook data
    
    print(f"DEBUG: Parsed - chat_channel_id: {chat_channel_id}, conversation_id: {conversation_id}, student_id: {student_id}, text: {message_text}")
    
    if not conversation_id or not student_id or not message_text:
        return jsonify({"status": "error", "message": "Missing required fields: chatConversationId, studentId, text"}), 400
    
    # 2. Получаем ответ от Qwen
    print(f"DEBUG: Calling Qwen API with: {message_text}")
    ai_response = call_qwen_api(message_text)
    print(f"DEBUG: Qwen response: {ai_response}")
    
    # 3. Отправляем ответ в Продамус
    print(f"DEBUG: Before calling send_prodamus_message")
    # Using conversation_id as required by the API
    success = send_prodamus_message(chat_channel_id, student_id, ai_response, conversation_id=conversation_id)
    print(f"DEBUG: After calling send_prodamus_message, success={success}")
    
    if success:
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"status": "error", "message": "Failed to send response to Prodamus"}), 500
