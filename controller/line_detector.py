# line_detector.py - Módulo de Segurança da Linha Branca Perimetral.
# Funções: 1. Detectar a linha branca. 2. Emitir comandos de SEGURANÇA (D ou S)
# para ANULAR a navegação ArUco e evitar a saída do campo.

import cv2
import numpy as np
import logging
import time 


# ==============================================================================
# 1. CONFIGURAÇÕES E CONSTANTES GLOBAIS
# ==============================================================================

# Configuração da cor branca (Espaço de Cores HSV)
# Baixo brilho (V) e baixa saturação (S) para capturar a cor branca.
LOWER_WHITE = np.array([0, 0, 180])
UPPER_WHITE = np.array([180, 20, 255])

# --- LIMIARES CALIBRADOS ---
# Os valores são coordenadas Y de pixel, onde um Y maior significa mais próximo do robô.

# ZONA 2: PERIGO/DESACELERAÇÃO 
# Calibrado para ~25 cm (235px)
LIMIAR_REDUCAO_VEL = 235 

# ZONA 1: CRÍTICO/PARADA 
# Calibrado para ~10 cm (360px)
LIMIAR_PARADA_CRITICA = 360

# Variáveis globais para rastrear o estado e histórico de log
GLOBAL_LAST_LOGGED_STATUS = 'INICIO' 
GLOBAL_LAST_Y_DETECTED = 0 
GLOBAL_TIME_LAST_DETECTED = time.time()

logger = logging.getLogger("line")


# ==============================================================================
# 2. FUNÇÕES DE SUPORTE E LÓGICA
# ==============================================================================

def detectar_limite(frame):
    """
    Processa o frame para detectar a linha branca e retorna a coordenada Y
    do ponto mais próximo (cy_roi) e a imagem processada (mask).
    
    Args:
        frame (np.array): A imagem de entrada (BGR) - já deve ser a ROI.

    Retorna: (cy_roi, mask_frame)
    """
    # 1. Pré-processamento e Segmentação de Cor
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, LOWER_WHITE, UPPER_WHITE)
    
    # 2. Encontrando Contornos
    # RETR_EXTERNAL pega apenas os contornos externos (simplifica a detecção da linha)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    cy_roi = 0 # Y do ponto mais próximo (se nenhum contorno for encontrado, é zero)
    
    if contours:
        # Encontra o maior contorno (assume-se que é a linha de limite principal)
        largest_contour = max(contours, key=cv2.contourArea)
        
        # O ponto mais próximo do robô é o que tem a maior coordenada Y (base da imagem)
        y_coords = largest_contour[:, 0, 1]
        cy_roi = np.max(y_coords) 
        
        # Opcional: Desenha o contorno para visualização
        cv2.drawContours(frame, [largest_contour], -1, (0, 255, 255), 2)
        # Marca o ponto crítico no centro da tela, na coordenada Y detectada
        center_x = frame.shape[1] // 2
        cv2.circle(frame, (center_x, cy_roi), 5, (0, 0, 255), -1) 
        
    return cy_roi, frame


def logica_limite_linha(cy_roi):
    """
    Decide o comando de segurança com base na proximidade da linha (cy_roi).
    A lógica de logging agora garante que o status 'Seguro' seja registrado apenas uma vez.
    
    Args:
        cy_roi (int): A coordenada Y do pixel mais próximo da linha (distância).

    Retorna: (comando_seguranca, status_display, cor)
    """
    global GLOBAL_LAST_LOGGED_STATUS, GLOBAL_LAST_Y_DETECTED, GLOBAL_TIME_LAST_DETECTED
    
    comando_seguranca = None
    
    # Atualiza o histórico se a linha for visível
    if cy_roi > 0:
        GLOBAL_LAST_Y_DETECTED = cy_roi
        GLOBAL_TIME_LAST_DETECTED = time.time()
    
    # ----------------------------------------------------------------------
    # LÓGICA DE AVALIAÇÃO DE SEGURANÇA (Três Zonas de Prioridade)
    # ----------------------------------------------------------------------
    
    # Estado 1: CRÍTICO (Parada Imediata - ativado a 360px)
    if cy_roi > LIMIAR_PARADA_CRITICA:
        current_status = 'CRITICO'
        comando_seguranca = 'S' # Comando de Parada Absoluta
        status_text = f"CRITICO! Y={cy_roi}px. CMD: PARAR"
        color = (0, 0, 255) # Vermelho
        
        # Loga APENAS na primeira vez que o estado muda para CRÍTICO
        if GLOBAL_LAST_LOGGED_STATUS != 'CRITICO':
            logger.critical(f"LIMITE: CRITICO! Y={cy_roi}. PARADA FORÇADA.")
            
    # Estado 2: PERIGO (Redução de Velocidade - ativado a 235px)
    elif cy_roi > LIMIAR_REDUCAO_VEL:
        current_status = 'PERIGO_DESACELERA'
        comando_seguranca = 'D' # Comando: Desacelerar / Modo Lento
        status_text = f"PERIGO! Y={cy_roi}px. CMD: DESACELERAR"
        color = (0, 165, 255) # Laranja
        
        # Loga APENAS na primeira vez que o estado muda para PERIGO
        if GLOBAL_LAST_LOGGED_STATUS not in ('PERIGO_DESACELERA', 'CRITICO'):
             logger.warning(f"LIMITE: PERIGO! Y={cy_roi}. Reduzindo velocidade.")
             
    # Estado 3: SEGURO (Nenhuma linha detectada ou muito longe)
    else:
        current_status = 'SEGURO'
        comando_seguranca = None # Deixa o ArUco ou outro módulo no controle
        status_text = f"Seguro. Y={cy_roi}px."
        color = (0, 255, 0) # Verde
        
        # Loga APENAS na primeira vez que o estado muda para SEGURO
        if GLOBAL_LAST_LOGGED_STATUS != 'SEGURO':
            logger.info(f"LIMITE: Seguro. Nenhuma linha perimetral detectada no campo de visão crítico.")

    # Atualiza o status logado para a próxima iteração
    GLOBAL_LAST_LOGGED_STATUS = current_status
        
    return comando_seguranca, status_text, color