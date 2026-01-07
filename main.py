from __future__ import annotations

import logging
import datetime
import traceback
import argparse

from . import scraper_competiciones, scraper_jornadas, scraper_actas
from .http_client import create_session
from .config import (
    OUTPUT_COMPETICIONES_CSV,
    OUTPUT_JORNADAS_CSV,
    OUTPUT_ACTAS_CSV,
    OUTPUT_SUSTITUCIONES_CSV,
    ENGINE,
    LOGS_DIR,
    CSV_DIR
)

parser = argparse.ArgumentParser(description="Web Scraper RFEF")
parser.add_argument(
    "--env",
    choices=["local", "aws"],
    default="aws",
    help="Entorno de ejecución (aws por defecto)"
)

args = parser.parse_args()
env = args.env
logging.info(f"Entorno de ejecución: {env.upper()}")

def main() -> None:
    """Orquesta el pipeline completo usando DataFrames en memoria.

    - Crea una única sesión HTTP.
    - Construye DataFrames de competiciones, jornadas, actas y sustituciones.
    - Exporta todos los DataFrames a CSV y MySQL al final.
    """

    # ==================================================
    # Configuración de logging
    # ==================================================
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / "scraper.log"
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
        force=True
    )

    # ==================================================
    # Inicio del proceso
    # ==================================================
    process_start_time = datetime.datetime.now()

    logging.info("=" * 60)
    logging.info("INICIO DEL PROCESO DE SCRAPING")
    logging.info(f"Fecha/Hora inicio: {process_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logging.info("=" * 60)

    try:
        # ==================================================
        # Ejecución principal
        # ==================================================
        with create_session() as session:
            df_competiciones = scraper_competiciones.build_competiciones_df(session)
            df_jornadas = scraper_jornadas.build_jornadas_df(session, env=env)
            df_actas, df_sustituciones = scraper_actas.build_actas_dfs(
                session,
                df_jornadas,
                df_competiciones,
            )

        # ==================================================
        # Exportar a CSV
        # ==================================================

        CSV_DIR.mkdir(parents=True, exist_ok=True)

        df_competiciones.to_csv(OUTPUT_COMPETICIONES_CSV, index=False, encoding="utf-8-sig")
        df_jornadas.to_csv(OUTPUT_JORNADAS_CSV, index=False, encoding="utf-8-sig")
        df_actas.to_csv(OUTPUT_ACTAS_CSV, index=False, encoding="utf-8-sig")
        df_sustituciones.to_csv(OUTPUT_SUSTITUCIONES_CSV, index=False, encoding="utf-8-sig")

        # ==================================================
        # Exportar a MySQL
        # ==================================================
        with ENGINE.begin() as conn:
            df_competiciones.to_sql("competiciones", con=conn, if_exists="replace", index=False)
            df_jornadas.to_sql("jornadas", con=conn, if_exists="replace", index=False)
            df_actas.to_sql("actas", con=conn, if_exists="replace", index=False)
            df_sustituciones.to_sql("sustituciones", con=conn, if_exists="replace", index=False)

    except Exception as e:
        logging.error("ERROR CRÍTICO DURANTE LA EJECUCIÓN DEL SCRAPER")
        logging.error(str(e))
        logging.error(traceback.format_exc())
        raise

    finally:
        # ==================================================
        # Fin del proceso
        # ==================================================
        process_end_time = datetime.datetime.now()
        duration = process_end_time - process_start_time

        logging.info("=" * 60)
        logging.info("FIN DEL PROCESO DE SCRAPING")
        logging.info(f"Fecha/Hora fin: {process_end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"Duración total: {duration}")
        logging.info("=" * 60)


if __name__ == "__main__":
    main()
