from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# Переменные окружения
PRODAMUS_API_KEY = os.getenv("PRODAMUS_API_KEY")
QWEN_API_KEY = os.getenv("QWEN_API_KEY")
PRODAMUS_BASE_URL = "https://api.xl.ru/api/v1"
QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"

def get_prodamus_headers():
    return {
        "Authorization": f"Bearer {PRODAMUS_API_KEY}",
        "Content-Type": "application/json"
    }

def get_qwen_headers():
    return {
        "Authorization": f"Bearer {QWEN_API_KEY}",
        "Content-Type": "application/json"
    }

def call_qwen_api(message_text):
    """Вызов Qwen через compatible-mode (OpenAI-совместимый формат)"""
    
    # Проверяем, что токен есть
    if not QWEN_API_KEY:
        print("ERROR: QWEN_API_KEY is not set!")
        return "Ошибка: не настроен ключ нейросети."
    
    # Compatible-mode использует OpenAI-формат payload
    payload = {
        "model": "qwen-plus",
        "messages": [
            {
                "role": "system",
                "content": "Ты - помощник техподдержки школы. Отвечай вежливо и кратко."
            },
            {
                "role": "user",
                "content": message_text
            }
        ]
    }
    
    print(f"DEBUG: Calling Qwen API. URL={QWEN_API_URL}")
    print(f"DEBUG: Qwen payload: {payload}")
    
    try:
        response = requests.post(
            QWEN_API_URL,
            headers=get_qwen_headers(),
            json=payload,
            timeout=30
        )
        
        print(f"DEBUG: Qwen response status: {response.status_code}")
        print(f"DEBUG: Qwen response body: {response.text[:500]}")
        
        response.raise_for_status()
        
        result = response.json()
        ai_text = result["choices"][0]["message"]["content"]
        print(f"DEBUG: Qwen answer: {ai_text}")
        return ai_text
        
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: Qwen HTTP error: {e}")
        print(f"ERROR: Response: {response.text}")
        return "Извините, сейчас я не могу ответить. Попробуйте позже."
    except Exception as e:
        print(f"ERROR: Qwen unexpected error: {e}")
        return "Извините, произошла ошибка. Попробуйте позже."


def send_prodamus_message(chat_channel_id, student_id, text, conversation_id=None):
    """Отправка сообщения в Prodamus"""
    
    if not PRODAMUS_API_KEY:
        print("ERROR: PRODAMUS_API_KEY is not set!")
        return False
    
    # Prodamus API требует PascalCase имена полей
    payload = {
        "ChatChannelId": chat_channel_id,
        "StudentId": student_id,
        "Text": text
    }
    
    # ConversationId — обязательное поле
    if conversation_id:
        payload["ConversationId"] = conversation_id
    else:
        print("WARNING: ConversationId is missing!")
        return False
    
    url = f"{PRODAMUS_BASE_URL}/chat-channel/messages"
    
    print(f"DEBUG: Sending to Prodamus:")
    print(f"  URL: {url}")
    print(f"  Payload: {payload}")
    
    try:
        response = requests.post(
            url,
            headers=get_prodamus_headers(),
            json=payload,
            timeout=10
        )
        
        print(f"DEBUG: Prodamus response status: {response.status_code}")
        print(f"DEBUG: Prodamus response: {response.text[:500]}")
        
        if response.status_code != 200:
            print(f"ERROR: Prodamus returned {response.status_code}: {response.text}")
            return False
            
        return True
        
    except Exception as e:
        print(f"ERROR: Failed to send to Prodamus: {e}")
        return False


@app.route('/', methods=['POST'])
def webhook():
    print("=" * 60)
    print("NEW WEBHOOK REQUEST")
    print("=" * 60)
    
    content_length = request.content_length
    content_type = request.content_type
    print(f"DEBUG: Content-Length: {content_length}, Content-Type: {content_type}")

    if not content_length or content_length == 0:
        return jsonify({"status": "error", "message": "Empty request body"}), 400

    data = request.get_json(silent=True)
    
    if not data:
        print(f"ERROR: Failed to parse JSON. Raw body: {request.get_data(as_text=True)[:200]}")
        return jsonify({"status": "error", "message": "Failed to parse JSON"}), 400
    
    print(f"DEBUG: Received JSON: {data}")
    
    # Извлекаем поля — пробуем разные варианты именования
    chat_channel_id = data.get("chatChannelId") or data.get("ChatChannelId")
    conversation_id = data.get("chatConversationId") or data.get("ChatConversationId") or data.get("conversationId")
    student_id = data.get("studentId") or data.get("StudentId")
    message_text = data.get("text") or data.get("Text") or data.get("message")
    
    print(f"DEBUG: Parsed fields:")
    print(f"  chat_channel_id: {chat_channel_id}")
    print(f"  conversation_id: {conversation_id}")
    print(f"  student_id: {student_id}")
    print(f"  message_text: {message_text}")
    
    # Проверка обязательных полей
    missing = []
    if not conversation_id:
        missing.append("conversationId")
    if not student_id:
        missing.append("studentId")
    if not message_text:
        missing.append("text")
    
    if missing:
        print(f"ERROR: Missing required fields: {missing}")
        return jsonify({
            "status": "error",
            "message": f"Missing required fields: {', '.join(missing)}"
        }), 400
    
    # 1. Получаем ответ от Qwen
    print(f"DEBUG: Calling Qwen with text: '{message_text[:100]}...'")
    ai_response = call_qwen_api(message_text)
    print(f"DEBUG: AI response: '{ai_response[:100]}...'")
    
    # 2. Отправляем ответ в Prodamus
    success = send_prodamus_message(
        chat_channel_id=chat_channel_id,
        student_id=student_id,
        text=ai_response,
        conversation_id=conversation_id
    )
    
    if success:
        print("SUCCESS: Message sent to Prodamus")
        return jsonify({"status": "success"}), 200
    else:
        print("ERROR: Failed to send message to Prodamus")
        return jsonify({
            "status": "error",
            "message": "Failed to send response to Prodamus"
        }), 500


if __name__ == '__main__':
    app.run(debug=True, port=3000)
