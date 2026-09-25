import ccxt
import pandas as pd
import requests
import time

# --- CONFIGURACIÓN DE TELEGRAM ---
TOKEN = "8628860776:AAFcmxMmxmdVmPy8EAWC--iP0mXCtEG2MLk"  # Reemplaza por tu Token de @BotFather
CHAT_ID = "402919772"

# --- CONFIGURACIÓN DE RIESGO Y BENEFICIOS ---
STOP_LOSS_PCT = 2.0     # Vende automáticamente si cae un 2% (Pérdida máxima)
TAKE_PROFIT_PCT = 4.0   # Vende automáticamente si sube un 4% (Objetivo de ganancia)

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
    
    # 3. Lógica con Gestión de Riesgo (Stop Loss + Take Profit)

    if en_posicion:
        # Calcular variación porcentual desde el precio de entrada
        porcentaje_variacion = ((precio_actual - precio_compra) / precio_compra) * 100
        saldo_obtenido = btc_poseidos * precio_actual
        ganancia_usd = saldo_obtenido - (btc_poseidos * precio_compra)

        # CASO A1: EJECUCIÓN DE TAKE PROFIT (Ganancia alcanzada +4%)
        if porcentaje_variacion >= TAKE_PROFIT_PCT:
            saldo_usd = saldo_obtenido
            btc_poseidos = 0.0
            en_posicion = False
            
            mensaje = (
                f"🎯 **TAKE PROFIT ALCANZADO (+{TAKE_PROFIT_PCT}%)**\n\n"
                f"• **Precio Venta (Profit):** `${precio_actual:,.2f}`\n"
                f"• **Precio Entrada:** `${precio_compra:,.2f}`\n"
                f"• **Ganancia ASEGURADA:** `+${ganancia_usd:,.2f} USD` (+{porcentaje_variacion:.2f}%)\n\n"
                f"💰 *Estrategia:* Venta automática para asegurar beneficios.\n"
                f"🏆 **NUEVO SALDO EN CARTERA:** `${saldo_usd:,.2f} USDT`"
            )
            enviar_telegram(mensaje)

        # CASO A2: EJECUCIÓN DE STOP LOSS (Pérdida alcanzada -2%)
        elif porcentaje_variacion <= -STOP_LOSS_PCT:
            saldo_usd = saldo_obtenido
            btc_poseidos = 0.0
            en_posicion = False
            
            mensaje = (
                f"🛑 **STOP LOSS DISPARADO (-{STOP_LOSS_PCT}%)**\n\n"
                f"• **Precio Venta (Stop):** `${precio_actual:,.2f}`\n"
                f"• **Precio Entrada:** `${precio_compra:,.2f}`\n"
                f"• **Pérdida CERRADA:** `${ganancia_usd:,.2f} USD` ({porcentaje_variacion:.2f}%)\n\n"
                f"🛡️ *Gestión de Riesgo:* Venta automática para evitar caídas mayores.\n"
                f"💰 **NUEVO SALDO EN CARTERA:** `${saldo_usd:,.2f} USDT`"
            )
            enviar_telegram(mensaje)

        # CASO A3: VENTA POR ESTRATEGIA (Cruce bajista de la SMA 20)
        elif precio_actual < media_actual:
            saldo_usd = saldo_obtenido
            btc_poseidos = 0.0
            en_posicion = False
            
            emoji = "🚀" if ganancia_usd >= 0 else "📉"
            
            mensaje = (
                f"🔴 **VENTA POR ESTRATEGIA EJECUTADA**\n\n"
                f"• **Precio Venta:** `${precio_actual:,.2f}`\n"
                f"• **Precio Entrada:** `${precio_compra:,.2f}`\n"
                f"• **Resultado Operación:** {emoji} `${ganancia_usd:+,.2f} USD` ({porcentaje_variacion:+.2f}%)\n\n"
                f"💡 *Estrategia:* El precio cayó por debajo de la SMA 20.\n"
                f"💰 **NUEVO SALDO EN CARTERA:** `${saldo_usd:,.2f} USDT`"
            )
            enviar_telegram(mensaje)

        # CASO A4: MANTENER POSICIÓN (Entre Stop Loss y Take Profit)
        else:
            precio_stop = precio_compra * (1 - STOP_LOSS_PCT/100)
            precio_tp = precio_compra * (1 + TAKE_PROFIT_PCT/100)
            
            mensaje = (
                f"📦 **POSICIÓN ACTIVA EN CURSO**\n\n"
                f"• **Precio Actual:** `${precio_actual:,.2f}` | **Entrada:** `${precio_compra:,.2f}`\n"
                f"• **Objetivo Take Profit:** `${precio_tp:,.2f}` (+{TAKE_PROFIT_PCT}%)\n"
                f"• **Límite Stop Loss:** `${precio_stop:,.2f}` (-{STOP_LOSS_PCT}%)\n"
                f"• **PnL Flotante:** `${ganancia_usd:+,.2f} USD` ({porcentaje_variacion:+.2f}%)\n\n"
                f"💼 **Valor Total Cartera:** `${saldo_obtenido:,.2f} USDT`"
            )
            enviar_telegram(mensaje)

    else:
        # CASO B: SEÑAL DE COMPRA
        if precio_actual > media_actual:
            btc_poseidos = saldo_usd / precio_actual
            precio_compra = precio_actual
            en_posicion = True
            precio_stop = precio_compra * (1 - STOP_LOSS_PCT/100)
            precio_tp = precio_compra * (1 + TAKE_PROFIT_PCT/100)
            
            mensaje = (
                f"🟢 **COMPRA SIMULADA EJECUTADA**\n\n"
                f"• **Precio Entrada:** `${precio_actual:,.2f}`\n"
                f"• **Capital Invertido:** `${saldo_usd:,.2f} USDT`\n"
                f"• **BTC Obtenidos:** `{btc_poseidos:.6f} BTC`\n\n"
                f"🎯 **Target Take Profit:** `${precio_tp:,.2f}` (+{TAKE_PROFIT_PCT}%)\n"
                f"🛡️ **Target Stop Loss:** `${precio_stop:,.2f}` (-{STOP_LOSS_PCT}%)\n\n"
                f"💡 *Estrategia:* Cruce alcista sobre la media de 20 horas."
            )
            enviar_telegram(mensaje)

        # CASO C: EN ESPERA
        else:
            mensaje = (
                f"💤 **EN ESPERA (LIQUIDEZ EN USDT)**\n\n"
                f"• **Precio BTC:** `${precio_actual:,.2f}` | **SMA 20:** `${media_actual:,.2f}`\n"
                f"💼 **Saldo Disponible:** `${saldo_usd:,.2f} USDT`"
            )
            enviar_telegram(mensaje)

    print(f"[{pd.Timestamp.now()}] Análisis completado. BTC: ${precio_actual}")

if __name__ == '__main__':
    enviar_telegram(f"🎯 **Simulador Completo Activado**\n• Take Profit: +{TAKE_PROFIT_PCT}%\n• Stop Loss: -{STOP_LOSS_PCT}%\n• Capital Ficticio: $1,000 USDT")
    
    while True:
        try:
            analizar_y_operar('BTC/USDT')
            time.sleep(900)
        except Exception as e:
            print(f"Error durante la ejecución: {e}")
            time.sleep(60)
