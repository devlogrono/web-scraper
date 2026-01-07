from pathlib import Path
import os

from sqlalchemy import create_engine
import pymysql

#PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parent

# ===============================
# Entorno de ejecución
# ===============================
DEFAULT_ENV = "aws"  # aws por defecto

DB_HOST = "dbdux.cd2ay8iog7ao.eu-north-1.rds.amazonaws.com"
DB_PORT = 3306
DB_NAME = "db_dux"
DB_USER = "admin"
DB_PASSWORD = "dpeQwertyuiop135790_!#"

ENGINE = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

BASE_URL = "https://intranet.frfutbol.com"
LOGIN_URL = f"{BASE_URL}/nfg/NLogin"

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; competiciones-scraper/2.0)",
    "Referer": LOGIN_URL,
    "Origin": BASE_URL,
}

FRF_USER = os.getenv("FRF_USER", "EDF LOGRONO")
FRF_PASS = os.getenv("FRF_PASS", "DuxLogrono2021")

TEMPORADA = "2025-2026"
TEMPORADA_ID = 21

# ===============================
# Directorios del proyecto
# ===============================
LOGS_DIR = PROJECT_ROOT / "logs"
CSV_DIR = PROJECT_ROOT / "csv"
ESCUDOS_DIR = PROJECT_ROOT / "escudos"

# ===============================
# Rutas de salida CSV
# ===============================
OUTPUT_COMPETICIONES_CSV = CSV_DIR / "competiciones.csv"
OUTPUT_JORNADAS_CSV = CSV_DIR / "jornadas.csv"
OUTPUT_ACTAS_CSV = CSV_DIR / "actas.csv"
OUTPUT_SUSTITUCIONES_CSV = CSV_DIR / "sustituciones.csv"

TABLES_TO_COMPARE = ["competiciones", "jornadas", "actas", "sustituciones"]

TEST_OUTPUT_DIR = Path(__file__).resolve().parent / "test"

FAST_TEST_JORNADAS_1FF = False
FAST_DEBUG_JORNADAS_MAX = None

RFEF_RESULTADOS_BASE = "https://resultados.rfef.es"
RFEF_1FF_SEASON = 21
RFEF_1FF_COMPETITION = 23289349
RFEF_1FF_GROUP = 23289573
RFEF_RESULTADOS_URL_TEMPLATE = (
    "https://rfef.es/es/resultados?season={season}&competition={competition}&group={group}&journey={journey}"
)

# Versión mayor de Chrome instalada en el sistema (para undetected_chromedriver)
RFEF_CHROME_VERSION_MAIN = 142
RFEF_CHROME_VERSION_MAIN_AWS = 124

# Parámetros para scrapear actas RFEF de forma más lenta / humana
# Si RFEF_SLOW_MODE es True, se aplican esperas largas y aleatorias entre peticiones
RFEF_SLOW_MODE = True
# Intervalo de espera entre descargas de actas RFEF (en segundos).
# Este retraso es corto; el control "fuerte" de frecuencia se hace por lotes.
RFEF_SLOW_MIN_DELAY = 10.0
RFEF_SLOW_MAX_DELAY = 20.0

# Procesar actas RFEF en bloques/lotes
RFEF_BATCH_SIZE = 10
RFEF_BATCH_SLEEP_SECONDS = 2100.0  # 35 minutos entre lotes de 10 actas RFEF

# Aleatorizar el orden de las actas de 1FF al parsear (por defecto desactivado)
RFEF_RANDOMIZE_1FF_ACTAS = False
