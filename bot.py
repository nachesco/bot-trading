import requests
import time

# Sustituye TU_TOKEN_AQUÍ por el token exacto de @BotFather
TOKEN = "8628860776:AAFcmxMmxmdVmPy8EAWC--iP0mXCtEG2MLk"
CHAT_ID = "402919772"

def enviar_mensaje(texto):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": texto, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        print("Respuesta de Telegram:", response.json())
    except Exception as e:
        print("Error al enviar mensaje:", e)

if __name__ == '__main__':
    print("Iniciando prueba de conexión...")
    enviar_mensaje("🤖 **¡Hola! Conexión correcta entre Render y tu Telegram.**")
    
    # Mantiene el servidor activo enviando una confirmación cada hora
    while True:
        time.sleep(3600)
