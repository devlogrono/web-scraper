from __future__ import annotations

import logging

import requests

from .config import LOGIN_URL, HTTP_HEADERS, FRF_USER, FRF_PASS

log = logging.getLogger(__name__)


def is_login_page(html: str) -> bool:
    return ("Novanet | Login" in html) or ('id="NLogin"' in html)


def login(session: requests.Session, jump_to: str) -> bool:
    data_ajax = {"NUser": FRF_USER, "NPass": FRF_PASS, "NURL": jump_to, "LoginAjax": "1"}
    headers_ajax = dict(HTTP_HEADERS)
    headers_ajax["X-Requested-With"] = "XMLHttpRequest"
    try:
        session.post(LOGIN_URL, data=data_ajax, headers=headers_ajax, timeout=30, allow_redirects=True)
        r = session.get(jump_to, headers=HTTP_HEADERS, timeout=30)
        if not is_login_page(r.text):
            return True
        session.get(LOGIN_URL, headers=HTTP_HEADERS, timeout=30)
        data = {"NUser": FRF_USER, "NPass": FRF_PASS, "NURL": jump_to}
        session.post(LOGIN_URL, data=data, headers=HTTP_HEADERS, timeout=30, allow_redirects=True)
        r2 = session.get(jump_to, headers=HTTP_HEADERS, timeout=30)
        return not is_login_page(r2.text)
    except requests.RequestException as exc:
        log.error("Error en login: %s", exc)
        return False


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HTTP_HEADERS)
    return session
