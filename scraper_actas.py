from __future__ import annotations

import logging
import re
import time
import random
from typing import List, Dict, Tuple, Optional
from urllib.parse import urljoin, urlparse, parse_qs

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .config import (
    BASE_URL,
    HTTP_HEADERS,
    ENGINE,
    OUTPUT_ACTAS_CSV,
    OUTPUT_SUSTITUCIONES_CSV,
    RFEF_SLOW_MODE,
    RFEF_SLOW_MIN_DELAY,
    RFEF_SLOW_MAX_DELAY,
    RFEF_BATCH_SIZE,
    RFEF_BATCH_SLEEP_SECONDS,
    RFEF_RANDOMIZE_1FF_ACTAS,
)
from .competitions import COMPETITIONS, JORNADA_URL_TEMPLATE
from .http_client import create_session, is_login_page, login

log = logging.getLogger(__name__)

DEBUG_RFEF_ACTAS = False

MATCH_BLOCK_SELECTOR = "div.portlet-body.body_fed"
ACTA_BTN_SELECTOR = 'a.btn.btn-sm.btn-success[href*="NFG_CmpPartido"]'

RE_MIN = re.compile(r"\((\d+)[^)]*\)")


def extract_acta_links(html: str) -> List[str]:
    soup = BeautifulSoup(html, "lxml")
    actas: List[str] = []
    for block in soup.select(MATCH_BLOCK_SELECTOR):
        a_acta = block.select_one(ACTA_BTN_SELECTOR)
        if a_acta and a_acta.get("href"):
            acta_link = urljoin(BASE_URL, a_acta["href"])
            actas.append(acta_link)
    return actas


def collect_actas_for_competition(session: requests.Session, hoja: str, meta: Dict) -> List[str]:
    if meta.get("empty"):
        log.info("%s: sin jornadas (hoja vacía).", hoja)
        return []

    comp_id = meta["competicion_id"]
    grupo_id = meta["grupo_id"]
    temp_id = meta["temporada_id"]
    jornadas = meta["jornadas"]
    all_links: List[str] = []
    for j in range(1, jornadas + 1):
        url = JORNADA_URL_TEMPLATE.format(competicion_id=comp_id, grupo_id=grupo_id, temporada_id=temp_id, j=j)
        for intento in range(3):
            try:
                r = session.get(url, headers=HTTP_HEADERS, timeout=30)
                if is_login_page(r.text):
                    if not login(session, url):
                        raise RuntimeError("No se pudo reautenticar")
                    r = session.get(url, headers=HTTP_HEADERS, timeout=30)
                r.raise_for_status()
                links = extract_acta_links(r.text)
                all_links.extend(links)
                log.info("%s J%s: actas %s", hoja, j, len(links))
                break
            except Exception as exc:
                wait = 1.5 * (intento + 1)
                log.warning("%s J%s: intento %s fallido (%s). Reintentando en %.1fs...", hoja, j, intento + 1, exc, wait)
                time.sleep(wait)
        else:
            log.error("%s J%s: sin datos tras reintentos", hoja, j)
    seen = set()
    uniq: List[str] = []
    for lk in all_links:
        if lk not in seen:
            uniq.append(lk)
            seen.add(lk)
    log.info("%s: total actas únicas %s", hoja, len(uniq))
    return uniq


def _get_1ff_acta_links_from_jornadas_df(
    jornadas_df: pd.DataFrame,
    competiciones_df: pd.DataFrame,
) -> List[str]:
    """Obtiene las URLs de actas de 1FF desde jornadas_df y competiciones_df.

    - Filtra jornadas_df a los partidos donde local o visitante es un equipo 1FF.
    - Devuelve las URLs de acta_link no nulas ni vacías (RFEF o intranet).
    - La elección del parser (RFEF vs intranet) se hace más tarde en parse_acta según el host.
    """

    if "acta_link" not in jornadas_df.columns:
        log.warning("1FF: jornadas_df no tiene columna acta_link")
        return []

    # Determinar ids de equipo de la competición 1FF
    if "id_equipo" not in competiciones_df.columns or "competicion" not in competiciones_df.columns:
        log.warning(
            "1FF: competiciones_df no tiene columnas esperadas (id_equipo, competicion); "
            "usando todas las filas de jornadas_df",
        )
        df_1ff = jornadas_df
    else:
        mask_1ff = competiciones_df["competicion"] == "1FF"
        team_ids_1ff = {
            str(x).strip()
            for x in competiciones_df.loc[mask_1ff, "id_equipo"].dropna()
            if str(x).strip()
        }
        if not team_ids_1ff:
            log.warning(
                "1FF: sin equipos marcados como 1FF en competiciones_df; "
                "usando todas las filas de jornadas_df",
            )
            df_1ff = jornadas_df
        elif "id_equipo_local" in jornadas_df.columns and "id_equipo_visitante" in jornadas_df.columns:
            loc_ids = jornadas_df["id_equipo_local"].astype(str).str.strip()
            vis_ids = jornadas_df["id_equipo_visitante"].astype(str).str.strip()
            mask_j = loc_ids.isin(team_ids_1ff) | vis_ids.isin(team_ids_1ff)
            df_1ff = jornadas_df.loc[mask_j]
            log.info(
                "1FF: partidos en jornadas_df=%s, partidos con equipo 1FF=%s",
                len(jornadas_df),
                len(df_1ff),
            )
        else:
            df_1ff = jornadas_df

    links: List[str] = []
    for x in df_1ff["acta_link"].dropna().unique():
        s = str(x).strip()
        if not s:
            continue
        links.append(s)

    uniq_sorted = sorted(set(links))
    log.info(
        "1FF: actas únicas desde jornadas_df (partidos con equipos 1FF, acta_link no vacío) %s",
        len(uniq_sorted),
    )
    return uniq_sorted


def _get_1ff_rfef_acta_links_jornada1() -> List[str]:
    try:
        query = """
            SELECT DISTINCT j.acta_link
            FROM jornadas j
            JOIN competiciones c
              ON j.id_equipo_local = c.id_equipo OR j.id_equipo_visitante = c.id_equipo
            WHERE c.competicion = '1FF'
              AND j.jornada = 1
              AND j.acta_link IS NOT NULL
              AND j.acta_link <> ''
        """
        with ENGINE.connect() as conn:
            df_links = pd.read_sql(query, con=conn)
    except Exception as exc:
        log.warning("DEBUG_RFEF_ACTAS: no se pudieron obtener actas de jornada 1 desde jornadas/competiciones: %s", exc)
        return []

    if "acta_link" not in df_links.columns or df_links.empty:
        log.warning("DEBUG_RFEF_ACTAS: consulta de jornada 1 devolvió 0 filas")
        return []

    links = [str(x) for x in df_links["acta_link"].dropna().unique() if str(x).strip()]
    uniq_sorted = sorted(links)
    log.info("DEBUG_RFEF_ACTAS: actas únicas jornada 1 (1FF) %s", len(uniq_sorted))
    return uniq_sorted


def _extract_teams(soup: BeautifulSoup) -> Tuple[str, str]:
    spans = soup.select("span.tituloprograma")
    names = [s.get_text(" ", strip=True) for s in spans if s.get_text(strip=True)]
    local = names[0] if len(names) >= 1 else ""
    visitante = names[1] if len(names) >= 2 else ""
    return local, visitante


def _find_team_column_td(soup: BeautifulSoup, team_name: str) -> Optional[BeautifulSoup]:
    for span in soup.select("span.tituloprograma"):
        name = span.get_text(" ", strip=True)
        if name == team_name:
            td = span.find_parent("td")
            if td:
                return td
    return None


def _parse_titulares(td_team: BeautifulSoup) -> List[Tuple[str, str]]:
    result: List[Tuple[str, str]] = []
    for tbl in td_team.select("table.tabla_rdg"):
        rows = tbl.find_all("tr")
        if not rows:
            continue
        ok = 0
        tmp: List[Tuple[str, str]] = []
        for tr in rows:
            tds = tr.find_all("td", recursive=False)
            if len(tds) < 2:
                continue
            dorsal = re.sub(r"[^\d]", "", tds[0].get_text(" ", strip=True))
            a = tds[1].find("a")
            if a:
                nombre = a.get_text(" ", strip=True)
                tmp.append((dorsal, nombre))
                ok += 1
        if ok >= 5:
            result = tmp
            break
    return result


def _parse_suplentes(td_team: BeautifulSoup) -> List[Tuple[str, str]]:
    result: List[Tuple[str, str]] = []
    if not td_team:
        return result
    supl_title = None
    for sp in td_team.find_all("span", class_="title"):
        if "Suplentes" in sp.get_text(strip=True):
            supl_title = sp
            break
    if not supl_title:
        return result
    tbl = supl_title.find_next("table", class_="tabla_rdg")
    if not tbl:
        return result
    for tr in tbl.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 2:
            continue
        dorsal = re.sub(r"[^\d]", "", tds[0].get_text(" ", strip=True))
        if not dorsal:
            continue
        a = tds[1].find("a")
        nombre_raw = a.get_text(" ", strip=True) if a else tds[1].get_text(" ", strip=True)
        nombre = re.sub(r"\s*\([^)]*\)\s*", " ", nombre_raw).strip()
        if nombre:
            result.append((dorsal, nombre))
    return result


def _parse_substitution_pairs(td_team: BeautifulSoup) -> List[Tuple[str, str, Optional[int]]]:
    pairs: List[Tuple[str, str, Optional[int]]] = []
    if not td_team:
        return pairs
    target_tbl = None
    for tbl in td_team.select("table.tabla_rdg"):
        if tbl.select("img[src*='flechas_in']") and tbl.select("img[src*='flechas_out']"):
            target_tbl = tbl
            break
    if not target_tbl:
        return pairs
    rows = [tr for tr in target_tbl.find_all("tr") if tr.find_all("td")]
    i = 0
    while i < len(rows) - 1:
        tr_in = rows[i]
        tr_out = rows[i + 1]
        tds_in = tr_in.find_all("td", recursive=False)
        tds_out = tr_out.find_all("td", recursive=False)
        if len(tds_in) < 2 or len(tds_out) < 2:
            i += 2
            continue
        nombre_in_raw = tds_in[1].get_text(" ", strip=True)
        nombre_in = re.sub(r"\s*\([^)]*\)\s*", " ", nombre_in_raw).strip()
        nombre_out_raw = tds_out[1].get_text(" ", strip=True)
        m = RE_MIN.search(nombre_out_raw)
        minuto = int(m.group(1)) if m else None
        nombre_out = re.sub(r"\s*\([^)]*\)\s*", " ", nombre_out_raw).strip()
        if nombre_in and nombre_out:
            pairs.append((nombre_out, nombre_in, minuto))
        i += 2
    return pairs


def _parse_substitutions(td_team: BeautifulSoup) -> List[Tuple[str, Optional[int], str]]:
    subs: List[Tuple[str, Optional[int], str]] = []
    target_tbl = None
    for tbl in td_team.select("table.tabla_rdg"):
        if tbl.select("img[src*='flechas_in']") and tbl.select("img[src*='flechas_out']"):
            target_tbl = tbl
            break
    if not target_tbl:
        return subs
    rows = [tr for tr in target_tbl.find_all("tr") if tr.find_all("td")]
    i = 0
    while i < len(rows) - 1:
        tr_in = rows[i]
        tr_out = rows[i + 1]
        tds_in = tr_in.find_all("td", recursive=False)
        tds_out = tr_out.find_all("td", recursive=False)
        if len(tds_in) < 2 or len(tds_out) < 2:
            i += 2
            continue
        name_in = re.sub(r"\s*\([^)]*\)\s*", " ", tds_in[1].get_text(" ", strip=True))
        name_out_raw = tds_out[1].get_text(" ", strip=True)
        m = RE_MIN.search(name_out_raw)
        minuto = int(m.group(1)) if m else None
        name_out = re.sub(r"\s*\([^)]*\)\s*", " ", name_out_raw)
        subs.append((name_in, minuto, "IN"))
        subs.append((name_out, minuto, "OUT"))
        i += 2
    return subs


def _parse_cards(td_team: BeautifulSoup) -> Dict[str, Dict[str, List[int]]]:
    cards: Dict[str, Dict[str, List[int]]] = {}
    tbl = None
    for t in td_team.select("table.tabla_rdg"):
        if t.select("img[src*='tarj_']"):
            tbl = t
            break
    if not tbl:
        return cards
    for tr in tbl.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 2:
            continue
        imgs = tds[0].find_all("img")
        if not imgs:
            continue
        txt = tds[1].get_text(" ", strip=True)
        m = RE_MIN.search(txt)
        minuto = int(m.group(1)) if m else None
        nombre = re.sub(r"\s*\([^)]*\)\s*", " ", txt).strip()
        if not nombre:
            continue
        d = cards.setdefault(nombre, {"Y": [], "R": []})
        for img in imgs:
            src = img.get("src", "")
            if "tarj_amar" in src:
                d["Y"].append(minuto if minuto is not None else -1)
            elif "tarj_roja" in src:
                d["R"].append(minuto if minuto is not None else -1)
    return cards


def _find_center_td_from_team_tds(soup: BeautifulSoup, td_local: BeautifulSoup, td_visit: BeautifulSoup) -> Optional[BeautifulSoup]:
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue
        if td_local in tds and td_visit in tds and len(tds) >= 3:
            for td in tds:
                if td is not td_local and td is not td_visit:
                    return td
    return None


def _normalize_name(x: str) -> str:
    x = (x or "").replace("\xa0", " ")
    x = re.sub(r"\s+", " ", x).strip(" -. \t\r\n")
    return x


def _parse_rfef_team_divs(soup: BeautifulSoup) -> Tuple[Optional[BeautifulSoup], Optional[BeautifulSoup], Optional[BeautifulSoup]]:
    goles_div = None
    for div in soup.find_all("div", class_="number"):
        text = div.get_text(" ", strip=True)
        if text and "goles" in text.lower():
            goles_div = div
            break
    if not goles_div:
        return None, None, None
    center_col = goles_div.find_parent("div", class_=lambda c: c and "col-sm-4" in c.split())
    if not center_col or not center_col.parent:
        return None, None, None
    parent = center_col.parent
    team_cols: List[BeautifulSoup] = []
    for child in parent.find_all("div", recursive=False):
        classes = child.get("class", []) or []
        if "col-sm-4" in classes:
            team_cols.append(child)
    local_div: Optional[BeautifulSoup] = None
    visit_div: Optional[BeautifulSoup] = None
    for col in team_cols:
        classes = col.get("class", []) or []
        if col is center_col:
            continue
        if "col-sm-pull-4" in classes:
            local_div = col
        else:
            visit_div = col
    return center_col, local_div, visit_div


def _parse_rfef_lineups_for_team_div(team_div: BeautifulSoup) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]], str]:
    team_name = ""
    name_div = team_div.find("div", class_="number")
    if name_div:
        team_name = _normalize_name(name_div.get_text(" ", strip=True))

    def _find_table_by_heading(heading_substring: str) -> Optional[BeautifulSoup]:
        for h in team_div.find_all(["h4", "h5"]):
            txt = h.get_text(" ", strip=True)
            if heading_substring.lower() in (txt or "").lower():
                tbl = h.find_next("table")
                if tbl:
                    return tbl
        return None

    def _parse_players_table(tbl: Optional[BeautifulSoup]) -> List[Tuple[str, str]]:
        players: List[Tuple[str, str]] = []
        if not tbl:
            return players
        for tr in tbl.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            dorsal = re.sub(r"[^\d]", "", tds[0].get_text(" ", strip=True))
            name_td = tds[-1]
            name = _normalize_name(name_td.get_text(" ", strip=True))
            if not dorsal and not name:
                continue
            players.append((dorsal, name))
        return players

    tit_tbl = _find_table_by_heading("Titulares")
    sup_tbl = _find_table_by_heading("Suplentes")
    titulares = _parse_players_table(tit_tbl)
    suplentes = _parse_players_table(sup_tbl)
    return titulares, suplentes, team_name


def _parse_rfef_substitutions_for_team_div(
    team_div: BeautifulSoup,
) -> Tuple[List[Tuple[str, Optional[int], str]], List[Tuple[str, str, Optional[int]]]]:
    subs: List[Tuple[str, Optional[int], str]] = []
    pairs: List[Tuple[str, str, Optional[int]]] = []

    heading = None
    for h in team_div.find_all(["h4", "h5"]):
        txt = h.get_text(" ", strip=True)
        if "Sustituciones" in (txt or ""):
            heading = h
            break
    if not heading:
        return subs, pairs

    tables: List[BeautifulSoup] = []
    node = heading
    while True:
        node = node.find_next_sibling()
        if node is None:
            break
        if getattr(node, "name", None) in ("h4", "h5"):
            txt = node.get_text(" ", strip=True)
            if "Tarjetas" in (txt or ""):
                break
        if getattr(node, "name", None) == "table":
            tables.append(node)

    if not tables:
        return subs, pairs

    for tbl in tables:
        rows = [tr for tr in tbl.find_all("tr") if tr.find_all("td")]
        i = 0
        while i < len(rows) - 1:
            # En RFEF la primera fila muestra normalmente al jugador que entra
            # y la segunda al que sale.
            tr_in = rows[i]
            tr_out = rows[i + 1]
            tds_in = tr_in.find_all("td")
            tds_out = tr_out.find_all("td")
            if len(tds_in) < 2 or len(tds_out) < 2:
                i += 2
                continue
            raw_in = tds_in[1].get_text(" ", strip=True)
            raw_out = tds_out[1].get_text(" ", strip=True)
            m = RE_MIN.search(raw_in) or RE_MIN.search(raw_out)
            minuto = int(m.group(1)) if m else None
            name_in = _normalize_name(re.sub(r"\s*\([^)]*\)\s*", " ", raw_in))
            name_out = _normalize_name(re.sub(r"\s*\([^)]*\)\s*", " ", raw_out))
            if name_in and name_out:
                subs.append((name_in, minuto, "IN"))
                subs.append((name_out, minuto, "OUT"))
                pairs.append((name_out, name_in, minuto))
            i += 2
    return subs, pairs


def _parse_rfef_cards_for_team_div(team_div: BeautifulSoup) -> Dict[str, Dict[str, List[int]]]:
    cards: Dict[str, Dict[str, List[int]]] = {}
    heading = None
    for h in team_div.find_all(["h4", "h5"]):
        txt = h.get_text(" ", strip=True)
        if "Tarjetas" in (txt or ""):
            heading = h
            break
    if not heading:
        return cards
    tbl = heading.find_next("table")
    if not tbl:
        return cards
    for tr in tbl.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 2:
            continue
        imgs = tds[0].find_all("img")
        if not imgs:
            continue
        txt = tds[1].get_text(" ", strip=True)
        m = RE_MIN.search(txt)
        minuto = int(m.group(1)) if m else None
        nombre = re.sub(r"\s*\([^)]*\)\s*", " ", txt).strip()
        if not nombre:
            continue
        d = cards.setdefault(nombre, {"Y": [], "R": []})
        for img in imgs:
            src = img.get("src", "")
            if "tarj_amar" in src:
                d["Y"].append(minuto if minuto is not None else -1)
            elif "tarj_roja" in src:
                d["R"].append(minuto if minuto is not None else -1)
    return cards


_RFEF_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]


def _rfef_headers() -> Dict[str, str]:
    base = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Referer": "https://rfef.es/es/resultados",
    }
    ua = random.choice(_RFEF_USER_AGENTS)
    base["User-Agent"] = ua
    return base


def _parse_goals_and_penalties_in_center_td(
    center_td: BeautifulSoup,
    jugadores_local: set,
    jugadores_visit: set,
) -> Tuple[Dict[Tuple[str, str], int], Dict[Tuple[str, str], int], Dict[Tuple[str, str], int]]:
    goals: Dict[Tuple[str, str], int] = {}
    pens: Dict[Tuple[str, str], int] = {}
    own: Dict[Tuple[str, str], int] = {}
    if not center_td:
        return goals, pens, own
    for tbl in center_td.find_all("table"):
        for tr in tbl.find_all("tr"):
            tds = tr.find_all("td", recursive=False)
            if len(tds) < 2:
                continue
            marcador_txt = tds[0].get_text(" ", strip=True)
            if "-" not in marcador_txt:
                continue
            is_pen = False
            is_own = False
            for a in tds[0].find_all("a"):
                cls = [c.lower() for c in (a.get("class", []) or [])]
                title = (a.get("title") or "").lower()
                if any("lpenalti" in c for c in cls) or "penalti" in title:
                    is_pen = True
                if any("lgolpp" in c for c in cls) or "propia puerta" in title or "autogol" in title:
                    is_own = True
            raw = tds[1].get_text(" ", strip=True)
            nombre = _normalize_name(re.sub(r"\s*\([^)]*\)\s*", " ", raw))
            if not nombre:
                continue
            if nombre in jugadores_local:
                key = ("LOCAL", nombre)
            elif nombre in jugadores_visit:
                key = ("VISITANTE", nombre)
            else:
                continue
            if is_own:
                own[key] = own.get(key, 0) + 1
            else:
                goals[key] = goals.get(key, 0) + 1
                if is_pen:
                    pens[key] = pens.get(key, 0) + 1
    return goals, pens, own


def _compute_minutes_with_red_or_second_yellow(
    tit: List[Tuple[str, str]],
    sup: List[Tuple[str, str]],
    subs: List[Tuple[str, Optional[int], str]],
    cards: Dict[str, Dict[str, List[int]]],
    dur: int = 90,
) -> Dict[str, int]:
    minutes: Dict[str, int] = {}
    first_in: Dict[str, int] = {}
    first_out: Dict[str, int] = {}
    for name, minute, kind in subs:
        if kind == "IN" and minute is not None and name not in first_in:
            first_in[name] = minute
        if kind == "OUT" and minute is not None and name not in first_out:
            first_out[name] = minute
    red_minute: Dict[str, int] = {}
    for name, d in cards.items():
        rojas_validas = sorted(m for m in d.get("R", []) if m is not None and m >= 0)
        if rojas_validas:
            red_minute[name] = rojas_validas[0]
    second_y_minute: Dict[str, int] = {}
    for name, d in cards.items():
        ys_validas = sorted(m for m in d.get("Y", []) if m is not None and m >= 0)
        if len(ys_validas) >= 2:
            second_y_minute[name] = ys_validas[1]
    expulsion_minute: Dict[str, int] = {}
    for name in set(cards.keys()):
        mins: List[int] = []
        if name in red_minute:
            mins.append(red_minute[name])
        if name in second_y_minute:
            mins.append(second_y_minute[name])
        if mins:
            expulsion_minute[name] = min(mins)
    for _, name in tit:
        start = 0
        end = first_out.get(name, dur)
        if name in expulsion_minute:
            end = min(end, expulsion_minute[name])
        minutes[name] = max(0, end - start)
    for _, name in sup:
        if name not in first_in:
            minutes[name] = 0
            continue
        start = first_in[name]
        end = first_out.get(name, dur)
        if name in expulsion_minute and expulsion_minute[name] >= start:
            end = min(end, expulsion_minute[name])
        minutes[name] = max(0, end - start)
    return minutes


def _presentable_counts(cards: Dict[str, Dict[str, List[int]]]) -> Tuple[Dict[str, int], Dict[str, int]]:
    y_count: Dict[str, int] = {}
    r_count: Dict[str, int] = {}
    for name, d in cards.items():
        ys = [m for m in d.get("Y", []) if m is not None and m >= 0]
        rs = [m for m in d.get("R", []) if m is not None and m >= 0]
        ys_set = set(ys)
        rs_display = [m for m in rs if m not in ys_set]
        y_count[name] = len(d.get("Y", []))
        r_count[name] = len(rs_display) + sum(1 for m in d.get("R", []) if m is not None and m < 0)
    return y_count, r_count


def parse_acta(session: requests.Session, acta_url: str) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    parsed_url = urlparse(acta_url)
    host = (parsed_url.netloc or "").lower()
    is_rfef = "rfef.es" in host

    if is_rfef:
        last_exc: Optional[Exception] = None
        r = None
        for intento in range(3):
            try:
                headers = _rfef_headers()
                r = session.get(acta_url, headers=headers, timeout=30)
                r.raise_for_status()
                break
            except requests.RequestException as exc:
                last_exc = exc
                wait = 1.5 * (intento + 1)
                log.warning(
                    "RFEF acta %s: intento %s fallido (%s). Reintentando en %.1fs...",
                    acta_url,
                    intento + 1,
                    exc,
                    wait,
                )
                time.sleep(wait)
        else:
            raise last_exc or RuntimeError("Fallo al descargar acta RFEF")
    else:
        r = session.get(acta_url, headers=HTTP_HEADERS, timeout=30)
        if is_login_page(r.text):
            if not login(session, acta_url):
                raise RuntimeError("Reautenticación fallida en acta")
            r = session.get(acta_url, headers=HTTP_HEADERS, timeout=30)
        r.raise_for_status()

    # Usamos el contenido en bruto para que BeautifulSoup detecte correctamente UTF-8
    html_bytes = r.content
    soup = BeautifulSoup(html_bytes, "lxml")

    qs = parse_qs(parsed_url.query)
    acta_id = (
        qs.get("CodActa", [""])[0]
        or qs.get("cod_acta", [""])[0]
        or qs.get("codacta", [""])[0]
    )

    if is_rfef:
        center_div, local_div, visit_div = _parse_rfef_team_divs(soup)
        loc_tit: List[Tuple[str, str]] = []
        loc_sup: List[Tuple[str, str]] = []
        vis_tit: List[Tuple[str, str]] = []
        vis_sup: List[Tuple[str, str]] = []
        local_name = ""
        visit_name = ""
        if local_div is not None:
            loc_tit, loc_sup, local_name = _parse_rfef_lineups_for_team_div(local_div)
        if visit_div is not None:
            vis_tit, vis_sup, visit_name = _parse_rfef_lineups_for_team_div(visit_div)

        subs_loc, subs_pairs_loc = _parse_rfef_substitutions_for_team_div(local_div) if local_div else ([], [])
        subs_vis, subs_pairs_vis = _parse_rfef_substitutions_for_team_div(visit_div) if visit_div else ([], [])

        # Tarjetas
        cards_loc = _parse_rfef_cards_for_team_div(local_div) if local_div else {}
        cards_vis = _parse_rfef_cards_for_team_div(visit_div) if visit_div else {}

        # Para el cálculo de minutos en actas usamos las mismas sustituciones que en la tabla
        # "sustituciones": por cada (sale, entra, minuto) añadimos un OUT y un IN.
        subs_loc_for_minutes: List[Tuple[str, Optional[int], str]] = []
        for sale, entra, minuto in subs_pairs_loc:
            subs_loc_for_minutes.append((entra, minuto, "IN"))
            subs_loc_for_minutes.append((sale, minuto, "OUT"))
        subs_vis_for_minutes: List[Tuple[str, Optional[int], str]] = []
        for sale, entra, minuto in subs_pairs_vis:
            subs_vis_for_minutes.append((entra, minuto, "IN"))
            subs_vis_for_minutes.append((sale, minuto, "OUT"))

        center_td = center_div
    else:
        local_name, visit_name = _extract_teams(soup)
        td_local = _find_team_column_td(soup, local_name) if local_name else None
        td_visit = _find_team_column_td(soup, visit_name) if visit_name else None

        loc_tit = _parse_titulares(td_local) if td_local else []
        vis_tit = _parse_titulares(td_visit) if td_visit else []

        loc_sup_all = _parse_suplentes(td_local) if td_local else []
        vis_sup_all = _parse_suplentes(td_visit) if td_visit else []

        set_loc_tit = {n for _, n in loc_tit}
        set_vis_tit = {n for _, n in vis_tit}
        loc_sup = [(d, n) for d, n in loc_sup_all if n not in set_loc_tit]
        vis_sup = [(d, n) for d, n in vis_sup_all if n not in set_vis_tit]

        subs_loc = _parse_substitutions(td_local) if td_local else []
        subs_vis = _parse_substitutions(td_visit) if td_visit else []

        subs_pairs_loc = _parse_substitution_pairs(td_local) if td_local else []
        subs_pairs_vis = _parse_substitution_pairs(td_visit) if td_visit else []

        cards_loc = _parse_cards(td_local) if td_local else {}
        cards_vis = _parse_cards(td_visit) if td_visit else {}

        center_td = _find_center_td_from_team_tds(soup, td_local, td_visit) if (td_local and td_visit) else None

        # Para intranet seguimos usando directamente la lista de sustituciones
        subs_loc_for_minutes = subs_loc
        subs_vis_for_minutes = subs_vis

    set_loc_all = {n for _, n in loc_tit} | {n for _, n in loc_sup}
    set_vis_all = {n for _, n in vis_tit} | {n for _, n in vis_sup}

    goals_all, pens_all, own_all = _parse_goals_and_penalties_in_center_td(center_td, set_loc_all, set_vis_all)
    goals_loc = {name: goals_all.get(("LOCAL", name), 0) for name in set_loc_all}
    goals_vis = {name: goals_all.get(("VISITANTE", name), 0) for name in set_vis_all}
    pens_loc = {name: pens_all.get(("LOCAL", name), 0) for name in set_loc_all}
    pens_vis = {name: pens_all.get(("VISITANTE", name), 0) for name in set_vis_all}
    own_loc = {name: own_all.get(("LOCAL", name), 0) for name in set_loc_all}
    own_vis = {name: own_all.get(("VISITANTE", name), 0) for name in set_vis_all}

    y_loc, r_loc = _presentable_counts(cards_loc)
    y_vis, r_vis = _presentable_counts(cards_vis)

    min_loc = _compute_minutes_with_red_or_second_yellow(loc_tit, loc_sup, subs_loc_for_minutes, cards_loc, dur=90)
    min_vis = _compute_minutes_with_red_or_second_yellow(vis_tit, vis_sup, subs_vis_for_minutes, cards_vis, dur=90)

    dorsal_loc = {name: dorsal for dorsal, name in (loc_tit + loc_sup)}
    dorsal_vis = {name: dorsal for dorsal, name in (vis_tit + vis_sup)}

    rows: List[Dict[str, object]] = []

    def add_rows(
        team_name: str,
        tit: List[Tuple[str, str]],
        sup: List[Tuple[str, str]],
        minutes_map: Dict[str, int],
        dorsal_map: Dict[str, str],
        goals_map: Dict[str, int],
        pens_map: Dict[str, int],
        own_map: Dict[str, int],
        y_map: Dict[str, int],
        r_map: Dict[str, int],
    ) -> None:
        for dorsal, name in tit:
            mins = minutes_map.get(name, 0)
            rows.append(
                {
                    "acta_id": acta_id,
                    "equipo": team_name,
                    "jugador": name,
                    "dorsal": dorsal,
                    "minutos": mins,
                    "goles": goals_map.get(name, 0),
                    "autogoles": own_map.get(name, 0),
                    "goles_penalty": pens_map.get(name, 0),
                    "tarjetas_amarillas": y_map.get(name, 0),
                    "tarjetas_rojas": r_map.get(name, 0),
                    "titular": True,
                }
            )
        for dorsal, name in sup:
            mins = minutes_map.get(name, 0)
            rows.append(
                {
                    "acta_id": acta_id,
                    "equipo": team_name,
                    "jugador": name,
                    "dorsal": dorsal_map.get(name, dorsal),
                    "minutos": mins,
                    "goles": goals_map.get(name, 0),
                    "autogoles": own_map.get(name, 0),
                    "goles_penalty": pens_map.get(name, 0),
                    "tarjetas_amarillas": y_map.get(name, 0),
                    "tarjetas_rojas": r_map.get(name, 0),
                    "titular": False,
                }
            )

    add_rows(local_name, loc_tit, loc_sup, min_loc, dorsal_loc, goals_loc, pens_loc, own_loc, y_loc, r_loc)
    add_rows(visit_name, vis_tit, vis_sup, min_vis, dorsal_vis, goals_vis, pens_vis, own_vis, y_vis, r_vis)

    subs_rows: List[Dict[str, object]] = []

    for sale, entra, minuto in subs_pairs_loc:
        subs_rows.append(
            {
                "acta_id": acta_id,
                "equipo": local_name,
                "sale": sale,
                "entra": entra,
                "minuto": minuto,
            }
        )

    for sale, entra, minuto in subs_pairs_vis:
        subs_rows.append(
            {
                "acta_id": acta_id,
                "equipo": visit_name,
                "sale": sale,
                "entra": entra,
                "minuto": minuto,
            }
        )

    return rows, subs_rows


def _debug_log_acta_summary(acta_url: str, rows: List[Dict[str, object]], subs_rows: List[Dict[str, object]]) -> None:
    log.info("===== DEBUG RFEF ACTA %s =====", acta_url)
    equipos = sorted({str(r.get("equipo", "")) for r in rows})
    for equipo in equipos:
        if not equipo:
            continue
        tit = [r for r in rows if r.get("equipo") == equipo and r.get("titular")]
        sup = [r for r in rows if r.get("equipo") == equipo and not r.get("titular")]
        log.info("[EQUIPO] %s", equipo)
        log.info("  Titulares (%s):", len(tit))
        for r in tit:
            log.info(
                "    #%s %s min=%s g=%s ag=%s pen=%s TA=%s TR=%s",
                r.get("dorsal"),
                r.get("jugador"),
                r.get("minutos"),
                r.get("goles"),
                r.get("autogoles"),
                r.get("goles_penalty"),
                r.get("tarjetas_amarillas"),
                r.get("tarjetas_rojas"),
            )
        log.info("  Suplentes (%s):", len(sup))
        for r in sup:
            log.info(
                "    #%s %s min=%s g=%s ag=%s pen=%s TA=%s TR=%s",
                r.get("dorsal"),
                r.get("jugador"),
                r.get("minutos"),
                r.get("goles"),
                r.get("autogoles"),
                r.get("goles_penalty"),
                r.get("tarjetas_amarillas"),
                r.get("tarjetas_rojas"),
            )

    if subs_rows:
        log.info("  Sustituciones (%s):", len(subs_rows))
        for s in subs_rows:
            log.info(
                "    %s: %s -> %s (%s')",
                s.get("equipo"),
                s.get("sale"),
                s.get("entra"),
                s.get("minuto"),
            )


def build_actas_dfs(
    session: requests.Session,
    jornadas_df: pd.DataFrame,
    competiciones_df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Construye los DataFrames de actas y sustituciones en memoria.

    - Para 1FF usa todas las actas presentes en jornadas_df cuyos equipos pertenecen a 1FF
      según competiciones_df (RFEF + intranet).
    - Para el resto de competiciones usa intranet.
    - No escribe CSV ni tablas SQL.
    """

    # Asegurar login genérico válido para intranet
    probe_url = None
    for meta in COMPETITIONS.values():
        if not meta.get("empty"):
            probe_url = JORNADA_URL_TEMPLATE.format(
                competicion_id=meta["competicion_id"],
                grupo_id=meta["grupo_id"],
                temporada_id=meta["temporada_id"],
                j=1,
            )
            break
    if probe_url is None:
        meta = next(iter(COMPETITIONS.values()))
        probe_url = JORNADA_URL_TEMPLATE.format(
            competicion_id=meta["competicion_id"],
            grupo_id=meta["grupo_id"],
            temporada_id=meta["temporada_id"],
            j=1,
        )
    if not login(session, probe_url):
        log.error("Login fallido o sin permisos en actas")
        raise SystemExit(2)

    actas_por_hoja: Dict[str, List[str]] = {}
    for hoja, meta in COMPETITIONS.items():
        log.info("Recolectando actas: %s %s", hoja, "(vacía)" if meta.get("empty") else "")
        if meta.get("empty"):
            actas_por_hoja[hoja] = []
            continue
        if hoja == "1FF":
            # Usar las URLs de acta ya presentes en jornadas_df para partidos de 1FF
            links_1ff = _get_1ff_acta_links_from_jornadas_df(jornadas_df, competiciones_df)
            log.info("1FF: actas únicas obtenidas desde jornadas_df: %s", len(links_1ff))
            actas_por_hoja[hoja] = links_1ff
        else:
            actas_por_hoja[hoja] = collect_actas_for_competition(session, hoja, meta)

    if DEBUG_RFEF_ACTAS:
        rfef_urls = _get_1ff_rfef_acta_links_jornada1()
        actas_por_hoja = {"1FF": rfef_urls}
        log.info(
            "DEBUG_RFEF_ACTAS: %s actas RFEF 1FF (jornada 1) seleccionadas para depuración",
            len(rfef_urls),
        )

    columns = [
        "acta_id",
        "equipo",
        "jugador",
        "dorsal",
        "minutos",
        "goles",
        "autogoles",
        "goles_penalty",
        "tarjetas_amarillas",
        "tarjetas_rojas",
        "titular",
    ]
    all_rows: List[Dict[str, object]] = []
    all_subs_rows: List[Dict[str, object]] = []

    for hoja, actas in actas_por_hoja.items():
        if not actas:
            log.info("%s: sin actas", hoja)
            continue

        # Orden de procesado: por defecto el del DataFrame; sólo se aleatoriza si el flag lo indica
        if hoja == "1FF" and RFEF_RANDOMIZE_1FF_ACTAS:
            actas_list = list(actas)
            random.shuffle(actas_list)
            log.info("1FF: orden de actas aleatorizado (total=%s)", len(actas_list))
        else:
            actas_list = list(actas)

        # Contar cuántas actas RFEF hay en esta hoja para poder aplicar lotes de tamaño fijo
        rfef_total = 0
        for _u in actas_list:
            try:
                _parsed = urlparse(_u)
                _host = (_parsed.netloc or "").lower()
            except Exception:
                _host = ""
            if "rfef.es" in _host:
                rfef_total += 1
        if rfef_total > 0:
            log.info("%s: total actas RFEF en esta hoja: %s", hoja, rfef_total)

        rfef_processed = 0

        for k, url in enumerate(actas_list, 1):
            try:
                log.info("%s: procesando acta %s/%s: %s", hoja, k, len(actas_list), url)
                parsed = urlparse(url)
                host = (parsed.netloc or "").lower()
                is_rfef = "rfef.es" in host
                log.info("  Host: %s, es RFEF: %s", host, is_rfef)
                if is_rfef and RFEF_SLOW_MODE:
                    # Pequeño retraso por acta RFEF
                    delay = random.uniform(RFEF_SLOW_MIN_DELAY, RFEF_SLOW_MAX_DELAY)
                    log.info("  Esperando %.1fs antes de descargar acta RFEF (throttling por acta)...", delay)
                    time.sleep(delay)
                    rfef_processed += 1
                rows, subs_rows = parse_acta(session, url)
                log.info(
                    "  Acta procesada: filas_jugadores=%s, filas_sustituciones=%s",
                    len(rows),
                    len(subs_rows),
                )
                if DEBUG_RFEF_ACTAS:
                    _debug_log_acta_summary(url, rows, subs_rows)
                all_rows.extend(rows)
                all_subs_rows.extend(subs_rows)

                # Progreso por hoja (todas las actas)
                if k % 10 == 0:
                    log.info("%s: procesadas %s/%s actas", hoja, k, len(actas_list))

                # Si se ha completado un lote de actas RFEF y aún quedan más, hacer una pausa larga
                if (
                    is_rfef
                    and RFEF_SLOW_MODE
                    and rfef_total > 0
                    and rfef_processed > 0
                    and rfef_processed % RFEF_BATCH_SIZE == 0
                    and rfef_processed < rfef_total
                ):
                    log.info(
                        "%s: completado lote de %s actas RFEF (%s/%s). Esperando %.0fs antes del siguiente lote...",
                        hoja,
                        RFEF_BATCH_SIZE,
                        rfef_processed,
                        rfef_total,
                        RFEF_BATCH_SLEEP_SECONDS,
                    )
                    time.sleep(RFEF_BATCH_SLEEP_SECONDS)
            except Exception as exc:
                log.warning("%s: error procesando acta %s: %s", hoja, url, exc)

    df = pd.DataFrame(all_rows, columns=columns)
    subs_columns = ["acta_id", "equipo", "sale", "entra", "minuto"]
    subs_df = pd.DataFrame(all_subs_rows, columns=subs_columns)

    if DEBUG_RFEF_ACTAS:
        log.info("DEBUG_RFEF_ACTAS activo: no se escriben CSV ni tablas SQL, sólo salida de depuración.")

    return df, subs_df


def run() -> None:
    """Compatibilidad hacia atrás: construye actas leyendo jornadas desde la BD.

    El nuevo pipeline (main.py) debe usar build_actas_dfs(session, jornadas_df)
    directamente con el DataFrame de jornadas en memoria.
    """

    with create_session() as session:
        try:
            competiciones_df = pd.read_sql("SELECT * FROM competiciones", con=ENGINE)
            jornadas_df = pd.read_sql("SELECT * FROM jornadas", con=ENGINE)
        except Exception as exc:
            log.error("No se pudieron leer competiciones/jornadas desde la BD: %s", exc)
            raise SystemExit(2)

        df, subs_df = build_actas_dfs(session, jornadas_df, competiciones_df)

    OUTPUT_ACTAS_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_ACTAS_CSV, index=False, encoding="utf-8-sig")

    OUTPUT_SUSTITUCIONES_CSV.parent.mkdir(parents=True, exist_ok=True)
    subs_df.to_csv(OUTPUT_SUSTITUCIONES_CSV, index=False, encoding="utf-8-sig")

    with ENGINE.begin() as conn:
        df.to_sql("actas", con=conn, if_exists="replace", index=False)
        subs_df.to_sql("sustituciones", con=conn, if_exists="replace", index=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    run()
