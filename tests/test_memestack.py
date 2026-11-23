import asyncio

import httpx

from services.memestack import MemeStackSearch


def test_search_skips_weak_match_and_returns_attributed_result():
    async def run():
        def respond(request):
            assert request.headers["X-API-Key"] == "mock-key"
            if request.url.params["q"] == "panik kalm":
                return httpx.Response(200, json={"memestack": {"match_quality": "closest_only"}})
            assert request.url.params["q"] == "hide the pain harold"
            return httpx.Response(200, json={"results": [{
                "itemurl": "https://memestack.ai/gallery/test?ref=api",
                "media_formats": {"gif": {
                    "url": "https://api.memestack.ai/v1/images/test/canonical.jpg",
                }},
            }]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            meme = await MemeStackSearch(client, "mock-key").search(
                ["panik_kalm", "hide_the_pain_harold"]
            )
        assert meme.tags == ("hide_the_pain_harold",)
        assert meme.source == "https://memestack.ai/gallery/test?ref=api"

    asyncio.run(run())

