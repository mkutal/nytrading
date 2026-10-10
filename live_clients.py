"""Small HTTP adapters. Exception messages never include URLs or response bodies."""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from urllib.request import Request, urlopen

from engine import parse_bars


class FeedError(RuntimeError):
    pass


class DeliveryError(RuntimeError):
    pass


class TwelveData:
    def __init__(self, api_key, opener=urlopen):
        self._key = api_key
        self._open = opener

    def bars(self, interval, count):
        minutes = {"5min": 5, "15min": 15, "1h": 60}[interval]
        query = urlencode(dict(symbol="XAU/USD", interval=interval, outputsize=count,
                               timezone="UTC", order="asc", apikey=self._key))
        request = Request("https://api.twelvedata.com/time_series?" + query,
                          headers={"User-Agent": "NY-Trading/1.0"})
        try:
            with self._open(request, timeout=15) as response:
                payload = json.load(response)
        except HTTPError as exc:
            raise FeedError(f"Twelve Data HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError):
            raise FeedError("Twelve Data connection failure") from None
        except (ValueError, TypeError):
            raise FeedError("Twelve Data invalid JSON") from None
        if not isinstance(payload, dict) or payload.get("status") == "error":
            raise FeedError("Twelve Data rejected request; check API access and credits")
        meta = payload.get("meta", {})
        if meta.get("timezone") != "UTC" or meta.get("symbol") != "XAU/USD":
            raise FeedError("Twelve Data unexpected symbol/timezone")
        try:
            return parse_bars(payload["values"], minutes)
        except (KeyError, TypeError, ValueError, OverflowError):
            raise FeedError("Twelve Data invalid OHLC") from None


def webhook_url(value):
    parts = urlsplit(value)
    if (parts.scheme != "https" or parts.hostname not in
            {"discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"}
            or parts.username or parts.password or parts.port not in (None, 443)
            or not re.fullmatch(r"/api/webhooks/\d+/[A-Za-z0-9_.-]+", parts.path)):
        raise ValueError("DISCORD_WEBHOOK_URL must be a Discord HTTPS webhook")
    query = dict(parse_qsl(parts.query))
    query["wait"] = "true"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


class Discord:
    def __init__(self, url, opener=urlopen):
        self._url = webhook_url(url)
        self._open = opener

    def send(self, content):
        request = Request(self._url, method="POST",
            data=json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "NY-Trading/1.0"})
        try:
            with self._open(request, timeout=15) as response:
                result = json.load(response)
        except HTTPError as exc:
            raise DeliveryError(f"Discord HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError):
            raise DeliveryError("Discord delivery unconfirmed") from None
        except (ValueError, TypeError):
            raise DeliveryError("Discord acknowledgement invalid") from None
        if not isinstance(result, dict) or not result.get("id"):
            raise DeliveryError("Discord acknowledgement missing")
        return str(result["id"])
