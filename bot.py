import ccxt
import pandas as pd
import requests
import time

# --- CONFIGURACIÓN DE TELEGRAM ---
TOKEN = "8628860776:AAFcmxMmxmdVmPy8EAWC--iP0mXCtEG2MLk"  # Reemplaza por tu Token de @BotFather
CHAT_ID = "402919772"

# --- ESTADO INICIAL DEL SIMULADOR ---
saldo_usd = 1000.0       # Capital inicial ficticio
btc_poseidos = 0.0      # Cantidad de Bitcoin comprados
precio_compra = 0.0     # Precio al que se realizó la última compra
en_posicion = False     # Indica si actualmente tenemos BTC o estamos en efectivo

def enviar_telegram(mensaje: str):
    """Envía un mensaje con formato Markdown a tu Telegram."""
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error al enviar a Telegram: {e}")

def analizar_y_operar(simbolo='BTC/USDT'):
    global saldo_usd, btc_poseidos, precio_compra, en_posicion
    
    exchange = ccxt.binance()
    
    # 1. Obtener velas de 1 hora
    ohlcv = exchange.fetch_ohlcv(simbolo, timeframe='1h', limit=50)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    
    # 2. Calcular indicador (Media Móvil SMA 20)
    df['sma_20'] = df['close'].rolling(20).mean()
    
    precio_actual = df['close'].iloc[-1]
    media_actual = df['sma_20'].iloc[-1]
    
    # 3. Lógica del Simulador de Compra / Venta
    
    # CASO A: SEÑAL DE COMPRA (Precio por encima de la media y no tenemos BTC)
    if precio_actual > media_actual and not en_posicion:
        btc_poseidos = saldo_usd / precio_actual
        precio_compra = precio_actual
        en_posicion = True
        
        mensaje = (
            f"🟢 **COMPRA SIMULADA EJECUTADA**\n\n"
            f"• **Precio BTC:** `${precio_actual:,.2f}`\n"
            f"• **Capital Invertido:** `${saldo_usd:,.2f} USDT`\n"
            f"• **BTC Obtenidos:** `{btc_poseidos:.6f} BTC`\n\n"
            f"💡 *Estrategia:* El precio ha superado la media de 20 horas."
        )
        enviar_telegram(mensaje)

    # CASO B: SEÑAL DE VENTA (Precio cae por debajo de la media y tenemos BTC)
    elif precio_actual < media_actual and en_posicion:
        saldo_obtenido = btc_poseidos * precio_actual
        ganancia_usd = saldo_obtenido - (btc_poseidos * precio_compra)
        porcentaje_ganancia = ((precio_actual - precio_compra) / precio_compra) * 100
        
        saldo_usd = saldo_obtenido
        btc_poseidos = 0.0
        en_posicion = False
        
        emoji = "🚀" if ganancia_usd >= 0 else "📉"
        
        mensaje = (
            f"🔴 **VENTA SIMULADA EJECUTADA**\n\n"
            f"• **Precio de Venta:** `${precio_actual:,.2f}`\n"
            f"• **Precio Entrada Previa:** `${precio_compra:,.2f}`\n"
            f"• **Resultado Operación:** {emoji} `${ganancia_usd:+,.2f} USD` ({porcentaje_ganancia:+.2f}%)\n\n"
            f"💰 **NUEVO SALDO EN CARTERA:** `${saldo_usd:,.2f} USDT`"
        )
        enviar_telegram(mensaje)

    # CASO C: INFORME PERIÓDICO SIN CAMBIOS DE POSICIÓN
    else:
        if en_posicion:
            valor_actual = btc_poseidos * precio_actual
            ganancia_flotante = valor_actual - (btc_poseidos * precio_compra)
            porcentaje_flotante = ((precio_actual - precio_compra) / precio_compra) * 100
            
            estado = (
                f"📦 **En posición** (Comprado a ${precio_compra:,.2f})\n"
                f"• PnL Flotante: `${ganancia_flotante:+,.2f} USD` ({porcentaje_flotante:+.2f}%)"
            )
            equidad_total = valor_actual
        else:
            estado = "💤 **En espera (Liquidez en USDT)**"
            equidad_total = saldo_usd

        mensaje = (
            f"📊 **ESTADO DE CUENTA SIMULADA**\n\n"
            f"• **Precio BTC:** `${precio_actual:,.2f}` | **SMA 20:** `${media_actual:,.2f}`\n"
            f"• **Estado:** {estado}\n\n"
            f"💼 **Valor Total Cartera:** `${equidad_total:,.2f} USDT`"
        )
        enviar_telegram(mensaje)

    print(f"[{pd.Timestamp.now()}] Análisis completado. Precio: ${precio_actual}")

if __name__ == '__main__':
    enviar_telegram("🎮 **Simulador de Trading Activado ($1,000 USDT Ficticios)**\nIniciando seguimiento de operaciones...")
    
    while True:
        try:
            analizar_y_operar('BTC/USDT')
            # Realiza un chequeo cada 15 minutos (900 segundos)
            time.sleep(900)
        except Exception as e:
            print(f"Error durante la ejecución: {e}")
            time.sleep(60)
