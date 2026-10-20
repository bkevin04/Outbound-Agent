"""A small JSON-over-HTTPS client for the Apollo and lemlist REST APIs (standard library only).

429s are retried after Retry-After. Server errors and timeouts are retried only on calls that cost nothing: a paid
call that may have gone through is never sent twice."""
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


def _tls():
    """Verified TLS with certifi's root certificates when it is installed (requirements.txt): python.org's macOS
    Python ships without root certificates, so urllib's default fails on every HTTPS call there."""
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


TLS = _tls()


class HTTPError(Exception):
    def __init__(self, status, body, url):
        super().__init__(f"HTTP {status} from {url.split('?')[0]}: {body[:300]}")
        self.status, self.body = status, body

    def code(self):
        """The API's error code from the body (LEAD_ALREADY_IN_CAMPAIGN, RECORD_LIMIT_EXCEEDED ...), or ''."""
        try:
            b = json.loads(self.body)
        except ValueError:
            return self.body.strip()[:80]
        if isinstance(b, dict):
            return str(b.get("error_code") or b.get("code") or b.get("error") or (b.get("error_details") or {}).get("code") or "")
        return ""


def _s(v):
    return "true" if v is True else "false" if v is False else str(v)


def query(params):
    """{"a": ["x", "y"], "r": {"min": 5}, "p": 1} -> "a[]=x&a[]=y&r[min]=5&p=1", Apollo's array and range style."""
    pairs = []
    for k, v in params.items():
        if v is None or v == []:
            continue
        if isinstance(v, (list, tuple)):
            pairs += [(f"{k}[]", _s(x)) for x in v]
        elif isinstance(v, dict):
            pairs += [(f"{k}[{sk}]", _s(sv)) for sk, sv in v.items() if sv is not None]
        else:
            pairs.append((k, _s(v)))
    return urllib.parse.urlencode(pairs)


def _wait(retry_after, attempt):
    try:
        return min(float(retry_after), 120.0)
    except (TypeError, ValueError):
        return min(2.0 ** attempt, 60.0)


def request(method, url, headers, body=None, paid=False, retries=4, timeout=90):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"Accept": "application/json", **headers}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=TLS) as r:
                text = r.read().decode("utf-8")
            return json.loads(text) if text.strip() else {}
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            retry = e.code == 429 or (e.code >= 500 and not paid)
            if retry and attempt < retries:
                time.sleep(_wait(e.headers.get("Retry-After"), attempt))
                continue
            raise HTTPError(e.code, text, url) from None
        except (urllib.error.URLError, TimeoutError):
            if paid or attempt == retries:
                raise
            time.sleep(_wait(None, attempt))
