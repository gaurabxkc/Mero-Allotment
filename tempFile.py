import requests
import base64
import time
import re
import ddddocr

# ── Constants ────────────────────────────────────────────────────────────────
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

MAX_CAPTCHA_ATTEMPTS = 15  # retries per BOID before giving up
BOID_DELAY_SECONDS = 1.5  # pause between BOIDs
RETRY_DELAY_SECONDS = 0.4  # pause between failed CAPTCHA attempts
BOID_LENGTH = 16  # expected BOID digit count

# ── OCR model (loaded once) ──────────────────────────────────────────────────
print("Loading OCR model (ddddocr)...")
ocr = ddddocr.DdddOcr(show_ad=False)


# ── Helpers ──────────────────────────────────────────────────────────────────


def fetch_data() -> dict | None:
    """
    GET the main endpoint. Returns parsed JSON body dict, or None on failure.
    Uses plain requests (no session) — this is what the server accepts.
    """
    try:
        resp = requests.get(URL_GET_DATA, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        body = resp.json().get("body", {})
        # Guard: server sometimes returns the HTML shell instead of JSON
        if not body or "companyShareList" not in body:
            print("   ⚠  Unexpected response format (got HTML instead of JSON?).")
            return None
        return body
    except requests.exceptions.HTTPError as e:
        print(f"   ⚠  HTTP error: {e}")
    except requests.exceptions.ConnectionError:
        print("   ⚠  Connection error – server unreachable.")
    except requests.exceptions.Timeout:
        print("   ⚠  Request timed out.")
    except ValueError:
        print("   ⚠  Server returned invalid JSON.")
    return None


def decode_captcha(captcha_b64: str) -> bytes:
    """Strips data-URI prefix if present, then base64-decodes."""
    if "," in captcha_b64:
        captcha_b64 = captcha_b64.split(",", 1)[1]
    return base64.b64decode(captcha_b64)


def save_result(line: str, filepath: str = "ipo_results.txt"):
    """Appends one result line to the output file."""
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── Company selection ────────────────────────────────────────────────────────


def select_company() -> tuple[int, str] | None:
    """
    Fetches the company list and lets the user pick one.
    Returns (company_id, company_name), or None on failure.
    """
    print("\nFetching active companies...")
    body = fetch_data()
    if body is None:
        return None

    companies = body.get("companyShareList", [])
    if not companies:
        print("❌ No active IPO results found on the server.")
        return None

    print("\n--- Available IPOs ---")
    for i, c in enumerate(companies, start=1):
        print(f"  [{i}] {c['name']}")

    raw = input(f"\nEnter number [1–{len(companies)}] (default 1): ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(companies):
        idx = int(raw) - 1
    else:
        if raw:
            print("Invalid input – defaulting to [1].")
        idx = 0

    chosen = companies[idx]
    print(f"\n✅ Selected: {chosen['name']}  (ID: {chosen['id']})\n")
    return chosen["id"], chosen["name"]


# ── BOID checker ─────────────────────────────────────────────────────────────


def check_single_boid(boid: str, company_id: int) -> str:
    """
    Solves the CAPTCHA and checks allotment for one BOID.
    Returns a human-readable result string.
    """
    if not boid.isdigit() or len(boid) != BOID_LENGTH:
        return f"❌ Invalid BOID (expected {BOID_LENGTH} digits): '{boid}'"

    for attempt in range(1, MAX_CAPTCHA_ATTEMPTS + 1):

        # 1. Fresh CAPTCHA
        body = fetch_data()
        if body is None:
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        captcha_data = body.get("captchaData")
        if not captcha_data:
            print(f"   [Attempt {attempt}] No captchaData in response. Retrying...")
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        captcha_identifier = captcha_data.get("captchaIdentifier")
        captcha_b64 = captcha_data.get("captcha")
        if not captcha_identifier or not captcha_b64:
            print(f"   [Attempt {attempt}] Incomplete CAPTCHA payload. Retrying...")
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        # 2. Decode + OCR
        try:
            image_bytes = decode_captcha(captcha_b64)
        except Exception as e:
            print(f"   [Attempt {attempt}] CAPTCHA decode error: {e}. Retrying...")
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        digits = re.sub(r"\D", "", ocr.classification(image_bytes))

        if len(digits) != 5:
            continue  # wrong length – silently retry

        # 3. Submit
        payload = {
            "companyShareId": company_id,
            "boid": boid,
            "captchaIdentifier": captcha_identifier,
            "userCaptcha": digits,
        }

        try:
            resp = requests.post(
                URL_CHECK_RESULT, headers=HEADERS, json=payload, timeout=15
            )
            resp.raise_for_status()
            result = resp.json()
        except requests.exceptions.HTTPError as e:
            print(f"   [Attempt {attempt}] HTTP error on submit: {e}. Retrying...")
            time.sleep(RETRY_DELAY_SECONDS)
            continue
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            print(f"   [Attempt {attempt}] Connection dropped on submit. Retrying...")
            time.sleep(RETRY_DELAY_SECONDS)
            continue
        except ValueError:
            print(f"   [Attempt {attempt}] Invalid JSON from server. Retrying...")
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        # 4. Interpret response
        if result.get("success"):
            return f"✅ Allotted – {result.get('message', 'Shares allotted')}"

        error_msg = result.get("message", "")
        if "captcha" in error_msg.lower():
            print(f"   [Attempt {attempt}] CAPTCHA '{digits}' rejected. Retrying...")
            time.sleep(RETRY_DELAY_SECONDS)
            continue

        # Definitive non-CAPTCHA answer (e.g. "Not allotted")
        return f"ℹ️  Result: {error_msg}"

    return f"⚠️  Failed to solve CAPTCHA after {MAX_CAPTCHA_ATTEMPTS} attempts."


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    boids_to_check = [
        "1301370005787155",
        # "1301370000000002",
    ]

    boids_to_check = [b.strip() for b in boids_to_check if b.strip()]
    if not boids_to_check:
        print("❌ No BOIDs to check. Add entries to boids_to_check and re-run.")
        return

    result = select_company()
    if result is None:
        return
    company_id, company_name = result

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n--- Run: {timestamp} | Company: {company_name} ---"
    print(header)
    print("-" * len(header.strip()))
    save_result(header)

    for boid in boids_to_check:
        print(f"Checking BOID: {boid} ...")
        final_result = check_single_boid(boid, company_id)
        print(f"   ↳ {final_result}\n")
        save_result(f"{boid} | {final_result}")
        time.sleep(BOID_DELAY_SECONDS)

    print("-" * 40)
    print(
        f"Done. {len(boids_to_check)} BOID(s) checked. Results saved to 'ipo_results.txt'."
    )


if __name__ == "__main__":
    main()
