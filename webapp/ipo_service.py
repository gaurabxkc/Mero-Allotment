from __future__ import annotations

import base64
import re
import time
from functools import lru_cache
from threading import Lock

import ddddocr
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

URL_GET_DATA = "https://iporesult.cdsc.com.np/result/companyShares/fileUploaded"
URL_CHECK_RESULT = "https://iporesult.cdsc.com.np/result/result/check"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://iporesult.cdsc.com.np",
    "Referer": "https://iporesult.cdsc.com.np/",
    "Connection": "keep-alive",
}

MAX_CAPTCHA_ATTEMPTS = 15
RETRY_DELAY_SECONDS = 0.1
BOID_LENGTH = 16
COMPANY_CACHE_TTL_SECONDS = 120

_company_cache: list[dict[str, str | int]] = []
_company_cache_at = 0.0
_company_cache_lock = Lock()


@lru_cache(maxsize=1)
def get_ocr() -> ddddocr.DdddOcr:
    return ddddocr.DdddOcr(show_ad=False)


@lru_cache(maxsize=1)
def get_http_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=2,
        backoff_factor=0.1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def fetch_data() -> dict | None:
    try:
        resp = get_http_session().get(URL_GET_DATA, headers=HEADERS, timeout=(4, 12))
        resp.raise_for_status()
        body = resp.json().get("body", {})
        if not body or "companyShareList" not in body:
            return None
        return body
    except (requests.RequestException, ValueError):
        return None


def get_companies() -> list[dict[str, str | int]]:
    global _company_cache_at
    now = time.time()
    with _company_cache_lock:
        if _company_cache and now - _company_cache_at < COMPANY_CACHE_TTL_SECONDS:
            return list(_company_cache)

    body = fetch_data()
    if not body:
        with _company_cache_lock:
            return list(_company_cache)

    companies = body.get("companyShareList", [])
    with _company_cache_lock:
        _company_cache[:] = companies
        _company_cache_at = now

    return list(companies)


def decode_captcha(captcha_b64: str) -> bytes:
    if "," in captcha_b64:
        captcha_b64 = captcha_b64.split(",", 1)[1]
    return base64.b64decode(captcha_b64)


def check_single_boid(boid: str, company_id: int) -> str:
    if not boid.isdigit() or len(boid) != BOID_LENGTH:
        return f"Invalid BOID (expected {BOID_LENGTH} digits)."

    ocr = get_ocr()

    for _ in range(1, MAX_CAPTCHA_ATTEMPTS + 1):
        body = fetch_data()
        if body is None:
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        captcha_data = body.get("captchaData") or {}
        captcha_identifier = captcha_data.get("captchaIdentifier")
        captcha_b64 = captcha_data.get("captcha")
        if not captcha_identifier or not captcha_b64:
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        try:
            image_bytes = decode_captcha(captcha_b64)
        except Exception:
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        digits = re.sub(r"\D", "", ocr.classification(image_bytes))
        if len(digits) != 5:
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        payload = {
            "companyShareId": company_id,
            "boid": boid,
            "captchaIdentifier": captcha_identifier,
            "userCaptcha": digits,
        }

        try:
            resp = get_http_session().post(
                URL_CHECK_RESULT,
                headers=HEADERS,
                json=payload,
                timeout=(4, 12),
            )
            resp.raise_for_status()
            result = resp.json()
        except (requests.RequestException, ValueError):
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        if result.get("success"):
            return f"Allotted - {result.get('message', 'Shares allotted')}"

        message = str(result.get("message", "")).strip()
        if "captcha" in message.lower():
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        if message:
            return message
        return "Result unavailable."

    return "Could not verify result after multiple CAPTCHA attempts."
