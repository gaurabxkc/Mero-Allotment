"""IPO API calls and CAPTCHA/OCR checker.

Uses a real Chromium-based browser (Brave / Chrome / Chromium) launched as
a standalone process and connected via the Chrome DevTools Protocol (CDP).
This is necessary because CDSC's F5 Shape Security WAF blocks all
programmatic HTTP clients (requests, curl, headless Playwright, etc.).
"""
from __future__ import annotations

import atexit
import base64
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from functools import lru_cache
from io import BytesIO
from threading import Lock

import ddddocr
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CDSC endpoints (relative paths – executed inside the browser context)
# ---------------------------------------------------------------------------
_CDSC_ORIGIN = "https://iporesult.cdsc.com.np"
_API_DATA = "/result/companyShares/fileUploaded"
_API_CHECK = "/result/result/check"

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------
MAX_CAPTCHA_FETCHES = 15
CAPTCHA_OCR_VARIANTS = 4
RETRY_DELAY_SECONDS = 0.1
BOID_LENGTH = 16
COMPANY_CACHE_TTL_SECONDS = 120

# ---------------------------------------------------------------------------
# Company cache
# ---------------------------------------------------------------------------
_company_cache: list[dict[str, str | int]] = []
_company_cache_at = 0.0
_company_cache_lock = Lock()

# ---------------------------------------------------------------------------
# Browser executable discovery
# ---------------------------------------------------------------------------
_BROWSER_NAMES = [
    "brave",
    "brave-browser",
    "google-chrome-stable",
    "google-chrome",
    "chromium",
    "chromium-browser",
]
_BROWSER_FALLBACK_PATHS = [
    "/usr/sbin/brave",
    "/usr/bin/brave",
    "/usr/bin/brave-browser",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def _find_browser() -> str:
    """Locate a Chromium-based browser on the system."""
    env_path = os.environ.get("BROWSER_PATH")
    if env_path:
        if os.path.isfile(env_path):
            return env_path
        raise RuntimeError(f"BROWSER_PATH={env_path!r} does not exist.")
    for name in _BROWSER_NAMES:
        path = shutil.which(name)
        if path:
            return path
    for path in _BROWSER_FALLBACK_PATHS:
        if os.path.isfile(path):
            return path
    raise RuntimeError(
        "No Chromium-based browser found. "
        "Install Brave, Chrome, or Chromium, or set the BROWSER_PATH "
        "environment variable."
    )


# ---------------------------------------------------------------------------
# OCR singleton
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _get_ocr() -> ddddocr.DdddOcr:
    return ddddocr.DdddOcr(show_ad=False)


# ---------------------------------------------------------------------------
# JavaScript snippets executed inside the browser page
# ---------------------------------------------------------------------------
_JS_FETCH_GET = """
async (path) => {
    try {
        const r = await fetch(path, {
            headers: { 'Accept': 'application/json, text/plain, */*' }
        });
        const t = await r.text();
        try { return JSON.parse(t); } catch { return null; }
    } catch { return null; }
}
"""

_JS_FETCH_POST = """
async ([path, payload]) => {
    try {
        const r = await fetch(path, {
            method: 'POST',
            headers: {
                'Accept': 'application/json, text/plain, */*',
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });
        const t = await r.text();
        try { return JSON.parse(t); } catch { return null; }
    } catch { return null; }
}
"""


# ---------------------------------------------------------------------------
# Browser bridge – manages a real browser for CDSC API calls
# ---------------------------------------------------------------------------
def _wait_for_port(port: int, timeout: float = 20) -> bool:
    """Block until *port* accepts a TCP connection or *timeout* expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.5)
    return False


class _BrowserBridge:
    """Launch a real browser, solve the F5 challenge, and proxy API calls."""

    _CHALLENGE_WAIT_MS = 7000

    def __init__(self) -> None:
        self._lock = Lock()
        self._proc: subprocess.Popen | None = None
        self._pw = None  # Playwright instance returned by .start()
        self._browser = None
        self._page = None
        self._ready = False
        self._profile_dir: str | None = None
        self._cdp_port = int(os.environ.get("CDP_PORT", "9333"))

    # -- lifecycle -----------------------------------------------------------

    def _launch(self) -> None:
        """Start the browser and navigate past the F5 challenge."""
        self._teardown()

        browser_path = _find_browser()
        self._profile_dir = tempfile.mkdtemp(prefix="mero-allotment-")

        headless = os.environ.get("BROWSER_HEADLESS", "1") != "0"
        args = [
            browser_path,
            f"--remote-debugging-port={self._cdp_port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-extensions",
            f"--user-data-dir={self._profile_dir}",
            "about:blank",
        ]
        if headless:
            args.insert(2, "--headless=new")

        logger.info(
            "Launching browser: %s (headless=%s, port=%d)",
            browser_path,
            headless,
            self._cdp_port,
        )
        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if not _wait_for_port(self._cdp_port):
            raise RuntimeError(
                f"Browser did not open CDP port {self._cdp_port} in time. "
                "Make sure the port is free and the browser is installed."
            )

        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(
            f"http://127.0.0.1:{self._cdp_port}"
        )
        ctx = (
            self._browser.contexts[0]
            if self._browser.contexts
            else self._browser.new_context()
        )
        self._page = ctx.new_page()

        # Solve the F5 Shape Security JavaScript challenge
        self._page.goto(
            _CDSC_ORIGIN + "/",
            wait_until="networkidle",
            timeout=45_000,
        )
        self._page.wait_for_timeout(self._CHALLENGE_WAIT_MS)
        self._page.wait_for_load_state("domcontentloaded")

        title = self._page.title() or ""
        if "Request Rejected" in title:
            logger.warning("F5 challenge failed on first load, retrying…")
            self._page.goto(
                _CDSC_ORIGIN + "/",
                wait_until="networkidle",
                timeout=45_000,
            )
            self._page.wait_for_timeout(self._CHALLENGE_WAIT_MS)
            self._page.wait_for_load_state("domcontentloaded")
            title = self._page.title() or ""

        if "Request Rejected" in title:
            hint = (
                " Try setting BROWSER_HEADLESS=0 to use a visible browser."
                if headless
                else ""
            )
            raise RuntimeError(
                "Browser could not pass the F5 challenge." + hint
            )

        logger.info("Browser ready – F5 challenge passed (title=%r)", title)
        self._ready = True

    def _ensure_ready(self) -> None:
        if self._ready and self._proc and self._proc.poll() is None:
            return
        self._ready = False
        self._launch()

    def _teardown(self) -> None:
        self._ready = False
        for step in (
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
            lambda: self._proc and self._proc.terminate(),
            lambda: self._proc and self._proc.wait(timeout=5),
            lambda: (
                self._profile_dir
                and shutil.rmtree(self._profile_dir, ignore_errors=True)
            ),
        ):
            try:
                step()
            except Exception:
                pass
        self._proc = self._pw = self._browser = self._page = None
        self._profile_dir = None

    def shutdown(self) -> None:
        with self._lock:
            self._teardown()

    # -- API helpers ---------------------------------------------------------

    def api_get(self, path: str) -> dict | None:
        """GET *path* via the browser's ``fetch()`` and return parsed JSON."""
        with self._lock:
            self._ensure_ready()
            try:
                return self._page.evaluate(_JS_FETCH_GET, path)
            except Exception:
                logger.warning("api_get failed, restarting browser…", exc_info=True)
                self._ready = False
                self._launch()
                return self._page.evaluate(_JS_FETCH_GET, path)

    def api_post(self, path: str, body: dict) -> dict | None:
        """POST *body* to *path* via the browser and return parsed JSON."""
        with self._lock:
            self._ensure_ready()
            try:
                return self._page.evaluate(_JS_FETCH_POST, [path, body])
            except Exception:
                logger.warning("api_post failed, restarting browser…", exc_info=True)
                self._ready = False
                self._launch()
                return self._page.evaluate(_JS_FETCH_POST, [path, body])


_bridge = _BrowserBridge()
atexit.register(_bridge.shutdown)


# ---------------------------------------------------------------------------
# CDSC data access (uses browser bridge)
# ---------------------------------------------------------------------------

def fetch_data() -> dict | None:
    """Fetch company list + captcha data from the CDSC API."""
    try:
        result = _bridge.api_get(_API_DATA)
        if not result:
            return None
        body = result.get("body", {})
        if not body or "companyShareList" not in body:
            return None
        return body
    except Exception:
        logger.warning("fetch_data failed", exc_info=True)
        return None


def get_companies() -> list[dict[str, str | int]]:
    """Return the list of IPO companies (cached for 2 minutes)."""
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


# ---------------------------------------------------------------------------
# CAPTCHA processing (unchanged from original)
# ---------------------------------------------------------------------------

def decode_captcha(captcha_b64: str) -> bytes:
    if "," in captcha_b64:
        captcha_b64 = captcha_b64.split(",", 1)[1]
    return base64.b64decode(captcha_b64)


def _image_to_bytes(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _captcha_variants(image_bytes: bytes) -> list[bytes]:
    variants = [image_bytes]

    try:
        with Image.open(BytesIO(image_bytes)) as image:
            image = ImageOps.exif_transpose(image)
            grayscale = ImageOps.grayscale(image)
            normalized = ImageOps.autocontrast(grayscale)

            variants.append(_image_to_bytes(normalized.filter(ImageFilter.SHARPEN)))

            contrasted = ImageEnhance.Contrast(normalized).enhance(1.8)
            variants.append(_image_to_bytes(contrasted.filter(ImageFilter.SHARPEN)))

            thresholded = normalized.point(lambda pixel: 255 if pixel > 170 else 0)
            variants.append(_image_to_bytes(thresholded))
    except Exception:
        return variants

    deduped: list[bytes] = []
    seen: set[bytes] = set()
    for variant in variants:
        if variant in seen:
            continue
        seen.add(variant)
        deduped.append(variant)
        if len(deduped) >= CAPTCHA_OCR_VARIANTS:
            break
    return deduped


def _extract_captcha_digits(ocr: ddddocr.DdddOcr, image_bytes: bytes) -> str:
    for variant in _captcha_variants(image_bytes):
        digits = re.sub(r"\D", "", ocr.classification(variant))
        if len(digits) == 5:
            return digits
    return ""


# ---------------------------------------------------------------------------
# BOID check (same CAPTCHA logic, browser bridge for HTTP)
# ---------------------------------------------------------------------------

def check_single_boid(boid: str, company_id: int) -> str:
    if not boid.isdigit() or len(boid) != BOID_LENGTH:
        return f"Invalid BOID (expected {BOID_LENGTH} digits)."

    ocr = _get_ocr()

    for _ in range(1, MAX_CAPTCHA_FETCHES + 1):
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

        digits = _extract_captcha_digits(ocr, image_bytes)
        if not digits:
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        payload = {
            "companyShareId": company_id,
            "boid": boid,
            "captchaIdentifier": captcha_identifier,
            "userCaptcha": digits,
        }

        try:
            result = _bridge.api_post(_API_CHECK, payload)
        except Exception:
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        if result is None:
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
