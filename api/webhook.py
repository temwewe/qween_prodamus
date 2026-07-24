from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

PRODAMUS_API_KEY = os.getenv("PRODAMUS_API_KEY")
QWEN_API_KEY = os.getenv("QWEN_API_KEY")
PRODAMUS_BASE_URL = "https://api.xl.ru/api/v1"

# Полный URL с /chat/completions
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
        print(f"DEBUG: Qwen body={response.text[:500]}")
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"ERROR: Qwen API failed: {e}")
        return "Извините, сейчас я не могу ответить. Попробуйте позже."


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

    try:
        response = requests.post(
            f"{PRODAMUS_BASE_URL}/chat-channel/messages",
            headers={"Authorization": f"Bearer {PRODAMUS_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=10
        )
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

    data = {}

    # Пробуем JSON
    if request.is_json and request.content_length and request.content_length > 0:
        data = request.get_json(silent=True) or {}

    # Если пусто — пробуем form-data
    if not data and request.form:
        data = request.form.to_dict()

    # Если всё ещё пусто — URL параметры
    if not data:
        data = request.args.to_dict()

    print(f"DEBUG: Received data: {data}")

    chat_channel_id = (
        data.get("chatChannelId") or data.get("ChatChannelId")
        or data.get("channelId") or data.get("chat_channel_id")
    )
    conversation_id = (
        data.get("chatConversationId") or data.get("ChatConversationId")
        or data.get("conversationId") or data.get("chat_conversation_id")
    )
    student_id = (
        data.get("studentId") or data.get("StudentId")
        or data.get("student_id")
    )
    message_text = (
        data.get("text") or data.get("Text")
        or data.get("message") or data.get("message_text")
    )

    print(f"DEBUG: Parsed:")
    print(f"  chat_channel_id:   {chat_channel_id}")
    print(f"  conversation_id:   {conversation_id}")
    print(f"  student_id:        {student_id}")
    print(f"  message_text:      '{message_text}'")

    # Проверка на макросы
    if "#" in str(conversation_id) or "#" in str(chat_channel_id):
        print("ERROR: Macros not substituted! Use form-data in Prodamus webhook.")
        return jsonify({
            "status": "error",
            "message": "Prodamus macros not substituted. Change webhook body to x-www-form-urlencoded."
        }), 400

    missing = []
    if not conversation_id: missing.append("conversationId")
    if not student_id: missing.append("studentId")
    if not message_text: missing.append("text")
    if not chat_channel_id: missing.append("chatChannelId")

    if missing:
        return jsonify({"status": "error", "message": f"Missing: {', '.join(missing)}"}), 400

    ai_response = call_qwen_api(message_text)
    print(f"DEBUG: AI response: '{ai_response[:80]}...'")

    success = send_prodamus_message(chat_channel_id, student_id, ai_response, conversation_id)

    if success:
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"status": "error", "message": "Failed to send to Prodamus"}), 500


if __name__ == '__main__':
    app.run(debug=True, port=3000)
