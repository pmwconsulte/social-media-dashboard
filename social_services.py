import os
import requests
import pandas as pd

INSTAGRAM_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")


def coletar_instagram():
    try:
        url = (
            f"https://graph.instagram.com/me"
            f"?fields=id,username"
            f"&access_token={INSTAGRAM_TOKEN}"
        )

        response = requests.get(
            url,
            timeout=10
        )

        response.raise_for_status()

        return {
            "data": pd.Timestamp.now().strftime("%Y-%m-%d"),
            "plataforma": "Instagram",
            "seguidores": 0,
            "engajamento": 4.5,
            "alcance": 50000
        }

    except Exception as erro:
        print(f"Erro Instagram: {erro}")
        return None


def coletar_youtube():
    try:
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={
                "part": "statistics",
                "id": YOUTUBE_CHANNEL_ID,
                "key": YOUTUBE_API_KEY
            },
            timeout=10
        )

        response.raise_for_status()

        dados = response.json()

        if not dados.get("items"):
            return None

        stats = dados["items"][0]["statistics"]

        return {
            "data": pd.Timestamp.now().strftime("%Y-%m-%d"),
            "plataforma": "YouTube",
            "seguidores": int(stats["subscriberCount"]),
            "engajamento": 5.0,
            "alcance": int(stats["viewCount"])
        }

    except Exception as erro:
        print(f"Erro YouTube: {erro}")
        return None
 
print(
f"Erro YouTube: {erro}"
)
 
return None
