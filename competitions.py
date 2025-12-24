from typing import Dict

from .config import BASE_URL

COMPETITIONS: Dict[str, Dict] = {
    "1FF": {
        "competicion_nombre": "1FF",
        "competicion_id": 21810206,
        "grupo_id": 21810235,
        "temporada_id": 21,
        "jornadas": 30,
    },
    "3FFF": {
        "competicion_nombre": "3FFF",
        "competicion_id": 21797449,
        "grupo_id": 21848050,
        "temporada_id": 21,
        "jornadas": 18,
    },
    "1J": {
        "competicion_nombre": "1J",
        "competicion_id": 21797455,
        "grupo_id": 21851763,
        "temporada_id": 21,
        "jornadas": 26,
    },
    "1C": {
        "competicion_nombre": "1C",
        "competicion_id": 21797456,
        "grupo_id": 21864087,
        "temporada_id": 21,
        "jornadas": 30,
    },
    "CFF": {
        "competicion_nombre": "CFF",
        "competicion_id": 21797489,
        "grupo_id": 21903227,
        "temporada_id": 21,
        "jornadas": 13,
    },
    "1I": {
        "competicion_nombre": "1I",
        "competicion_id": 21797458,
        "grupo_id": 21882083,
        "temporada_id": 21,
        "jornadas": 26,
    },
    "IFF": {
        "competicion_nombre": "IFF",
        "competicion_id": 21797492,
        "grupo_id": 21909928,
        "temporada_id": 21,
        "jornadas": 13,
    },
}

JORNADA_URL_TEMPLATE = (
    BASE_URL
    + "/nfg/NPcd/NFG_CmpJornada?cod_primaria=1000128"
    + "&CodCompeticion={competicion_id}&CodGrupo={grupo_id}&CodTemporada={temporada_id}&CodJornada={j}"
    + "&cod_agrupacion=&Sch_Tipo_Juego="
)
