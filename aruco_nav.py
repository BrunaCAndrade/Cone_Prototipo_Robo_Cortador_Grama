# aruco_nav.py
# Módulo de Navegação Deliberativa e Correção Lateral baseado em ArUco

import cv2
import cv2.aruco as aruco
import numpy as np
import logging

logger = logging.getLogger("aruco")

# =============================================================================
# PARÂMETROS FÍSICOS E DE CALIBRAÇÃO DO AMBIENTE
# =============================================================================
MARKER_SIZE = 0.06              # Tamanho real do marcador ArUco (lado do quadrado) em metros (6 cm)
CAMERA_TO_NOTE_FRONT = 0.19     # Distância da câmera até a ponta dianteira do robô/notebook em metros (19 cm)
DIST_ALVO = 0.15                # Distância alvo em metros (15 cm) para acionar o giro de 180 graus.
LARGURA_FAIXA = 0.05            # Largura da faixa de varredura que o robô cobre em metros (5 cm)
LIMITE_DESVIO_CM = 1.0          # Limite máximo de desvio lateral (eixo X) em centímetros antes de corrigir.

TOTAL_FAIXAS = 4                # Número total de faixas que o robô deve percorrer.

# =============================================================================
# ARUCO CONFIGURAÇÃO
# =============================================================================
ARUCO_DICT = aruco.getPredefinedDictionary(aruco.DICT_6X6_250)
PARAMS = aruco.DetectorParameters()
DETECTOR = aruco.ArucoDetector(ARUCO_DICT, PARAMS)

# =============================================================================
# CÂMERA CALIBRAÇÃO (Matriz K)
# =============================================================================
CAM_MATRIX = np.array([
    [600, 0, 320],
    [0, 600, 240],
    [0, 0, 1]
], dtype=np.float32)

DIST_COEFFS = np.zeros((5, 1))

logger.info("ARUCO_NAV: modulo inicializado")

# =============================================================================
# ESTADO GLOBAL (FSM - Finite State Machine)
# =============================================================================
FAIXA_ATUAL = 0                 # Faixa de varredura que o robô está atualmente cobrindo.
POSICAO_X_CM = 0                # Posição X (horizontal) acumulada, usada para referência.

EM_CORRECAO = False             # Flag para indicar que o robô está executando uma correção lateral.

ULTIMO_ARUCO_GIRADO = None      # ID do marcador que acionou o último giro de 180 graus.
AGUARDANDO_NOVO_ARUCO = False   # Trava para evitar giros múltiplos no mesmo marcador.

LAST_DIST_LOG = {}              # Log para registrar a última distância e evitar logs repetitivos.

# =============================================================================
# DETECÇÃO E MEDIÇÕES (solvePnP)
# =============================================================================
def calcular_pose_aruco(frame):
    """
    Detecta marcadores ArUco no frame, calcula a pose 3D (rvec, tvec) de cada um,
    e estima a distância de navegação (dist_ponta) e o desvio lateral (tx_cm).
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    corners, ids, rejected = DETECTOR.detectMarkers(gray)

    if ids is None:
        return []

    ids = ids.flatten()
    half = MARKER_SIZE / 2

    # Definição dos pontos 3D reais do marcador (no sistema de coordenadas do marcador).
    obj_points = np.array([
        [-half, half, 0],
        [half, half, 0],
        [half, -half, 0],
        [-half, -half, 0]
    ], dtype=np.float32)

    arucos = []

    for i, marker_id in enumerate(ids):
        # cv2.solvePnP: Calcula a rotação (rvec) e translação (tvec) do marcador em relação à câmera.
        ok, rvec, tvec = cv2.solvePnP(
            obj_points,
            corners[i][0],
            CAM_MATRIX,
            DIST_COEFFS,
            flags=cv2.SOLVEPNP_IPPE_SQUARE
        )
        if not ok:
            continue

        # Distância Z (tvec[2]) é a profundidade. Subtraímos a distância da câmera à ponta do robô.
        dist_ponta = float(tvec[2]) - CAMERA_TO_NOTE_FRONT
        # Distância X (tvec[0]) é o desvio lateral. Convertida para cm.
        tx_cm = float(tvec[0]) * 100

        dist_cm = int(dist_ponta * 100)
        
        # Loga a distância apenas se ela mudou e se não estiver em correção.
        if LAST_DIST_LOG.get(marker_id) != dist_cm and not EM_CORRECAO:
            logger.info(
                f"ARUCO {marker_id} | dist={dist_ponta:.2f}m | desvio={tx_cm:.1f}cm"
            )
            LAST_DIST_LOG[marker_id] = dist_cm

        arucos.append({
            "id": int(marker_id),
            "dist_ponta": dist_ponta,       # Distância de navegação efetiva em metros.
            "tx_cm": tx_cm                  # Desvio lateral em cm (X negativo = esquerda, X positivo = direita).
        })

        # Desenha o sistema de eixos 3D (Rvec, Tvec) no frame para visualização
        cv2.drawFrameAxes(frame, CAM_MATRIX, DIST_COEFFS, rvec, tvec, 0.05)

    aruco.drawDetectedMarkers(frame, corners, ids)
    return arucos

# =============================================================================
# LÓGICA DE NAVEGAÇÃO (FSM - Máquina de Estados Finitos)
# =============================================================================
def logica_planejamento_corte(arucos, _fase_dummy=None):
    """
    Implementa a lógica de navegação principal, priorizando a correção lateral.
    
    Retorna APENAS comandos de navegação: F, L, R, l, r.
    """
    global FAIXA_ATUAL, POSICAO_X_CM
    global EM_CORRECAO
    global ULTIMO_ARUCO_GIRADO, AGUARDANDO_NOVO_ARUCO

    # --------------------------------------------------
    # 1. CORREÇÃO LATERAL (Alta prioridade)
    # --------------------------------------------------
    for a in arucos:
        if abs(a["tx_cm"]) > LIMITE_DESVIO_CM:
            if not EM_CORRECAO:
                lado = "DIREITA" if a["tx_cm"] > 0 else "ESQUERDA"
                logger.warning(
                    f"ARUCO: entrando em correção lateral -> {lado} ({a['tx_cm']:.2f}cm)"
                )
                EM_CORRECAO = True

            # Retorna 'r' (direita) se desvio positivo (desvio para a direita)
            return "r" if a["tx_cm"] > 0 else "l"

    # Se estava em correção mas nenhum marcador excede o limite, a correção terminou.
    if EM_CORRECAO:
        logger.info("ARUCO: correção lateral concluída – alinhado")
        EM_CORRECAO = False
        LAST_DIST_LOG.clear() 

    # --------------------------------------------------
    # 2. TRAVA DE GIRO (Média prioridade)
    # --------------------------------------------------
    if AGUARDANDO_NOVO_ARUCO:
        for a in arucos:
            if a["id"] != ULTIMO_ARUCO_GIRADO:
                logger.info(
                    f"ARUCO: novo marcador detectado ({a['id']}), liberando próximo giro"
                )
                AGUARDANDO_NOVO_ARUCO = False
                ULTIMO_ARUCO_GIRADO = None
        return "F" # Continua avançando até a trava ser liberada.

    # --------------------------------------------------
    # 3. EVENTO DE GIRO (Baixa prioridade)
    # --------------------------------------------------
    for a in arucos:
        if a["dist_ponta"] > DIST_ALVO:
            continue # O marcador está muito longe.

        # IDs (20, 30): Giro ESQUERDA.
        if a["id"] in (20, 30):
            logger.critical(f"ARUCO {a['id']} -> giro 180 ESQUERDA")
            POSICAO_X_CM += int(LARGURA_FAIXA * 100)
            FAIXA_ATUAL += 1

            ULTIMO_ARUCO_GIRADO = a["id"]
            AGUARDANDO_NOVO_ARUCO = True

            return "L" # Comando: Giro 180 Graus Esquerda

        # IDs (10, 40): Giro DIREITA.
        if a["id"] in (10, 40):
            logger.critical(f"ARUCO {a['id']} -> giro 180 DIREITA")
            POSICAO_X_CM += int(LARGURA_FAIXA * 100)
            FAIXA_ATUAL += 1

            ULTIMO_ARUCO_GIRADO = a["id"]
            AGUARDANDO_NOVO_ARUCO = True

            return "R" # Comando: Giro 180 Graus Direita

    # --------------------------------------------------
    # 4. FINALIZAÇÃO / AVANÇO (Prioridade Padrão)
    # --------------------------------------------------
    if FAIXA_ATUAL >= TOTAL_FAIXAS:
        logger.critical("ARUCO: área totalmente coberta")
        return "F" # Comando: Finalização.

    # Se nenhuma condição foi acionada, o robô avança.
    return "F" # Comando: Avançar (Frente)