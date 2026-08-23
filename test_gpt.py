import os
import requests
from dotenv import load_dotenv

# Загружаем переменные из файла .env
load_dotenv()

def test_yandex_gpt():
    """Проверяет работу Yandex GPT"""
    
    # Забираем ключи из .env
    api_key = os.getenv("YANDEX_API_KEY")
    folder_id = os.getenv("YANDEX_FOLDER_ID")
    
    # Проверяем, что ключи загрузились
    if not api_key or not folder_id:
        print("Ошибка: не удалось загрузить ключи из .env")
        print("Проверь, что файл .env лежит в папке проекта и заполнен")
        return
    
    print(f"API-ключ загружен: {api_key[:10]}... (показываю первые 10 символов)")
    print(f"Folder ID загружен: {folder_id}")
    
    # URL для Yandex GPT
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    
    # Заголовки запроса
    headers = {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json"
    }
    
    # Тело запроса (промпт)
    data = {
        "modelUri": f"gpt://{folder_id}/yandexgpt/latest",
        "completionOptions": {
            "stream": False,
            "temperature": 0.7,
            "maxTokens": 500
        },
        "messages": [
            {"role": "system", "text": "Ты — эмпатичный психолог. Отвечай коротко и по делу."},
            {"role": "user", "text": "Привет! У меня сегодня плохое настроение."}
        ]
    }
    
    print("\nОтправляю запрос к Yandex GPT...")
    
    # Отправляем запрос
    try:
        response = requests.post(url, headers=headers, json=data)
        
        if response.status_code == 200:
            result = response.json()
            answer = result["result"]["alternatives"][0]["message"]["text"]
            print("\n✅ Ответ от Yandex GPT:")
            print("-" * 40)
            print(answer)
            print("-" * 40)
        else:
            print(f"\n❌ Ошибка: {response.status_code}")
            print("Текст ошибки:")
            print(response.text)
            
    except Exception as e:
        print(f"\n❌ Ошибка соединения: {str(e)}")
        print("Проверь интернет и правильность ключей")

if __name__ == "__main__":
    test_yandex_gpt()
    