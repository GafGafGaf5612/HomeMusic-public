from types import SimpleNamespace

import httpx
import respx

from app import net


@respx.mock
async def test_proxy_mount_matches_domain_and_subdomains_only(monkeypatch):
    monkeypatch.setattr(net, "get_settings", lambda: SimpleNamespace(
        outbound_proxy="http://proxy.test:3128", proxy_hosts={"sndcdn.com"}, youtube_proxy=""))
    proxy = httpx.MockTransport(lambda request: httpx.Response(200, text="proxy"))
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: proxy)
    unrelated = respx.get("https://notsndcdn.com/").respond(200, text="direct")
    async with net.make_client() as client:
        assert (await client.get("https://sndcdn.com/")).text == "proxy"
        assert (await client.get("https://cf-media.sndcdn.com/")).text == "proxy"
        assert (await client.get("https://notsndcdn.com/")).text == "direct"
    assert unrelated.call_count == 1
