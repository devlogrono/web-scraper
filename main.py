from __future__ import annotations

import logging

from . import scraper_competiciones, scraper_jornadas, scraper_actas
from .http_client import create_session
from .config import (
    OUTPUT_COMPETICIONES_CSV,
    OUTPUT_JORNADAS_CSV,
    OUTPUT_ACTAS_CSV,
    OUTPUT_SUSTITUCIONES_CSV,
    ENGINE,
    PROJECT_ROOT,
)


def main() -> None:
    """Orquesta el pipeline completo usando DataFrames en memoria.

    - Crea una única sesión HTTP.
    - Construye DataFrames de competiciones, jornadas, actas y sustituciones.
    - Exporta todos los DataFrames a CSV y MySQL al final.
    """

    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "scraper.log"

    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )

    with create_session() as session:
        df_competiciones = scraper_competiciones.build_competiciones_df(session)
        df_jornadas = scraper_jornadas.build_jornadas_df(session)
        df_actas, df_sustituciones = scraper_actas.build_actas_dfs(session, df_jornadas, df_competiciones)

    # Exportar a CSV
    OUTPUT_COMPETICIONES_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_competiciones.to_csv(OUTPUT_COMPETICIONES_CSV, index=False, encoding="utf-8-sig")

    OUTPUT_JORNADAS_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_jornadas.to_csv(OUTPUT_JORNADAS_CSV, index=False, encoding="utf-8-sig")

    OUTPUT_ACTAS_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_actas.to_csv(OUTPUT_ACTAS_CSV, index=False, encoding="utf-8-sig")

    OUTPUT_SUSTITUCIONES_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_sustituciones.to_csv(OUTPUT_SUSTITUCIONES_CSV, index=False, encoding="utf-8-sig")

    # Exportar a MySQL
    with ENGINE.begin() as conn:
        df_competiciones.to_sql("competiciones", con=conn, if_exists="replace", index=False)
        df_jornadas.to_sql("jornadas", con=conn, if_exists="replace", index=False)
        df_actas.to_sql("actas", con=conn, if_exists="replace", index=False)
        df_sustituciones.to_sql("sustituciones", con=conn, if_exists="replace", index=False)


if __name__ == "__main__":
    main()
