from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urlparse, parse_qs, urljoin

import pandas as pd
from pandas.api.types import CategoricalDtype
import requests
from bs4 import BeautifulSoup

from .config import BASE_URL, HTTP_HEADERS, ENGINE, OUTPUT_COMPETICIONES_CSV
from .competitions import COMPETITIONS, JORNADA_URL_TEMPLATE
from .http_client import create_session, is_login_page, login

log = logging.getLogger(__name__)

MATCH_BLOCK_SELECTOR = "div.portlet-body.body_fed"
TEAM_LINK_SELECTOR = 'span.font_widgetL a[href*="NFG_VisEquipos"]'
TEAM_BADGE_SELECTOR = "img.escudo_clb"

_filename_bad_chars = re.compile(r'[<>:"/\\|?*\x00-\x1F]')


def sanitize_filename(name: str, max_len: int = 150) -> str:
    name = name.strip().replace(".", "_")
    name = _filename_bad_chars.sub("_", name)
    name = re.sub(r"\s+", " ", name)
    return name[:max_len].strip()


def guess_ext_from_response(resp: requests.Response, fallback: str = ".png") -> str:
    ext = os.path.splitext(urlparse(resp.url).path)[1].lower()
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        return ext
    ctype = resp.headers.get("Content-Type", "").lower()
    if "png" in ctype:
        return ".png"
    if "jpeg" in ctype or "jpg" in ctype:
        return ".jpg"
    if "gif" in ctype:
        return ".gif"
    if "webp" in ctype:
        return ".webp"
    return fallback


def save_badge(session: requests.Session, url: str, dest_path: Path) -> bool:
    try:
        r = session.get(url, headers=HTTP_HEADERS, timeout=30, stream=True)
        r.raise_for_status()
        ext = guess_ext_from_response(r)
        dest = dest_path.with_suffix(ext)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return True
    except Exception:
        return False


def parse_equipo_cell(td) -> Tuple[str, str, str, str]:
    name = ""
    team_id = ""
    team_url = ""
    badge_url = ""

    a = td.select_one(TEAM_LINK_SELECTOR)
    if a and a.get("href"):
        name = a.get_text(" ", strip=True).replace('"', "").strip()
        rel = a["href"]
        if rel.startswith("NFG_VisEquipos"):
            rel = "/nfg/NPcd/" + rel
        team_url = urljoin(BASE_URL, rel)
        try:
            qs = parse_qs(urlparse(team_url).query)
            team_id = qs.get("Codigo_Equipo", [""])[0]
        except Exception:
            team_id = ""

    img = td.select_one(TEAM_BADGE_SELECTOR)
    if img and img.get("src"):
        src = img["src"]
        badge_url = src if src.startswith("http") else urljoin(BASE_URL, src)

    return name, team_id, team_url, badge_url


def scrape_competition(session: requests.Session, nombre: str, jornada_url: str, escudos_root: Path) -> List[Dict[str, str]]:
    qs = parse_qs(urlparse(jornada_url).query)
    comp_id = (qs.get("CodCompeticion", [""])[0] or "").strip()
    temporada_code = (qs.get("CodTemporada", [""])[0] or "").strip()
    temporada = {"20": "2024-2025", "21": "2025-2026"}.get(temporada_code, temporada_code)

    r = session.get(jornada_url, headers=HTTP_HEADERS, timeout=30)
    if is_login_page(r.text):
        if not login(session, jornada_url):
            raise RuntimeError("No se pudo autenticar al cargar la jornada")
        r = session.get(jornada_url, headers=HTTP_HEADERS, timeout=30)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "lxml")
    equipos: Dict[str, Dict[str, str]] = {}

    comp_folder = escudos_root / nombre
    comp_folder.mkdir(parents=True, exist_ok=True)

    descargado_por_url = set()

    for block in soup.select(MATCH_BLOCK_SELECTOR):
        table = block.find("table")
        if not table:
            continue
        tr = table.find("tr")
        if not tr:
            continue
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3:
            continue

        for td in (tds[0], tds[2]):
            nombre_eq, id_eq, url_eq, esc_eq = parse_equipo_cell(td)
            if not (id_eq or nombre_eq):
                continue

            key = id_eq or nombre_eq
            equipos[key] = {
                "competicion": nombre,
                "competicion_id": comp_id,
                "temporada": temporada,
                "nombre_equipo": nombre_eq,
                "id_equipo": id_eq,
                "url_equipo": url_eq,
                "url_escudo_equipo": esc_eq,
            }

            if esc_eq and esc_eq not in descargado_por_url:
                filename = sanitize_filename(nombre_eq) or (id_eq or "equipo")
                dest_stub = comp_folder / filename
                ok = save_badge(session, esc_eq, dest_stub)
                if ok:
                    descargado_por_url.add(esc_eq)

    return list(equipos.values())


def build_competiciones_df(session: requests.Session) -> pd.DataFrame:
    """Scrapea todas las competiciones y devuelve el DataFrame en memoria.

    No escribe CSV ni tablas SQL; eso se hace en la capa orquestadora (main o run).
    """

    rows: List[Dict[str, str]] = []
    escudos_root = Path("escudos")
    escudos_root.mkdir(exist_ok=True)

    order = ["1FF", "3FFF", "1J", "1C", "CFF", "1I", "IFF"]

    probe_meta = next(iter(COMPETITIONS.values()))
    probe_url = JORNADA_URL_TEMPLATE.format(
        competicion_id=probe_meta["competicion_id"],
        grupo_id=probe_meta["grupo_id"],
        temporada_id=probe_meta["temporada_id"],
        j=1,
    )
    if not login(session, probe_url):
        log.error("Login fallido o sin permisos en competiciones")
        raise SystemExit(2)

    for nombre in order:
        meta = COMPETITIONS[nombre]
        url = JORNADA_URL_TEMPLATE.format(
            competicion_id=meta["competicion_id"],
            grupo_id=meta["grupo_id"],
            temporada_id=meta["temporada_id"],
            j=1,
        )
        log.info("Procesando %s", nombre)
        rows.extend(scrape_competition(session, nombre, url, escudos_root))

    df = pd.DataFrame(
        rows,
        columns=[
            "competicion",
            "competicion_id",
            "temporada",
            "nombre_equipo",
            "id_equipo",
            "url_equipo",
            "url_escudo_equipo",
        ],
    ).drop_duplicates()

    order = ["1FF", "3FFF", "1J", "1C", "CFF", "1I", "IFF"]
    cat_comp = CategoricalDtype(categories=order, ordered=True)
    df["competicion"] = df["competicion"].astype(cat_comp)
    df = df.sort_values(["competicion", "nombre_equipo"], na_position="last")

    return df


def run() -> None:
    with create_session() as session:
        df = build_competiciones_df(session)

    OUTPUT_COMPETICIONES_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_COMPETICIONES_CSV, index=False, encoding="utf-8-sig")

    with ENGINE.begin() as conn:
        df.to_sql("competiciones", con=conn, if_exists="replace", index=False)
