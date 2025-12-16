import cv2
import cv2.aruco as aruco

def save_marker(id, size=400, fname="marker.png"):
    """
    Gera e salva um marcador ArUco em um arquivo de imagem.
    
    Args:
        id (int): O ID único do marcador a ser gerado (ex: 10, 20, 30, 40).
        size (int): O tamanho da imagem do marcador em pixels (ex: 400x400).
        fname (str): O nome do arquivo de saída (ex: "aruco_start_10.png").
    """
    # 1. Define o dicionário ArUco: DICT_6X6_250 significa marcadores de 6x6 bits
    # e um total de 250 IDs disponíveis (IDs 0 a 249).
    dictionary = aruco.getPredefinedDictionary(aruco.DICT_6X6_250)

    # 2. GERAÇÃO DO MARCADOR.
    # O parâmetro borderBits=1 define a largura da borda branca circundante em bits. 
    # Usar borderBits=1 é o padrão e garante a melhor detecção em campo.
    # O tamanho (size) especificado inclui este 1 bit de borda.
    marker = aruco.generateImageMarker(dictionary, id, size, borderBits=1) 

    # 3. Salva o marcador gerado no disco
    cv2.imwrite(fname, marker)
    print(f"Marcador {id} salvo como {fname}")

# Exemplos de uso para gerar os marcadores de mapeamento do campo:
save_marker(10, fname="aruco_start_10.png") # Origem do sistema de coordenadas
save_marker(20, fname="aruco_end_20.png")   # Limite superior (Y=1.0m)
save_marker(30, fname="aruco_end_30.png")   # Limite superior (X=0.6m, Y=1.0m)
save_marker(40, fname="aruco_end_40.png")   # Limite lateral (X=0.6m, Y=0.0m)