import time

import requests

import daily_digest


_call_openai_json = daily_digest.call_openai_json


def call_openai_json_with_fallback(prompt):
    for attempt in range(1, 3):
        try:
            return _call_openai_json(prompt)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "unknown"
            body = daily_digest.clean_text(exc.response.text if exc.response is not None else "", 500)
            print(f"OpenAI request failed with HTTP {status}: {body}")
            if status == 429 and attempt == 1:
                retry_after = exc.response.headers.get("retry-after") if exc.response is not None else ""
                wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else 20
                print(f"Retrying OpenAI request after {wait_seconds} seconds.")
                time.sleep(wait_seconds)
                continue
            print("Using fallback digest because OpenAI did not return a usable response.")
            return None
        except requests.RequestException as exc:
            print(f"OpenAI request failed: {exc}")
            print("Using fallback digest because OpenAI did not return a usable response.")
            return None


daily_digest.call_openai_json = call_openai_json_with_fallback
daily_digest.main()
