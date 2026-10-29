"""The Bangla voice for calls: a small local server HeyDesk sends each sentence to.

Its own uv project, so torch and Coqui stay out of HeyDesk's. Run it from this folder:

    uv run uvicorn server:app --port 8010

The first start downloads the model (about 1 GB, Hugging Face's cache). The voice is
EMTIAZZ/bangladeshi-bangla-tts-vits: VITS, Apache 2.0, a Bangladeshi accent. On an M2's CPU a
sentence takes 0.6–1.3 s, about 7× faster than it's spoken (19 Oct). It reads Bangla letters
only: Latin letters are dropped, so HeyDesk writes everything in Bangla before sending it.
"""

import asyncio

import numpy as np
from fastapi import FastAPI, Response
from huggingface_hub import hf_hub_download
from pydantic import BaseModel, Field
from TTS.utils.synthesizer import Synthesizer

MODEL = "EMTIAZZ/bangladeshi-bangla-tts-vits"

synthesizer = Synthesizer(
    tts_checkpoint=hf_hub_download(MODEL, "pytorch_model.pth"),
    tts_config_path=hf_hub_download(MODEL, "config.json"),
)
RATE = synthesizer.output_sample_rate  # 22050
lock = asyncio.Lock()  # one sentence at a time: the model isn't shared safely across threads

app = FastAPI()


class Speak(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@app.post("/speak")
async def speak(body: Speak) -> Response:
    """The sentence as raw 16-bit mono PCM; the rate is in `X-Sample-Rate`."""
    async with lock:
        wav = await asyncio.to_thread(synthesizer.tts, body.text)
    pcm = (np.clip(np.asarray(wav), -1, 1) * 32767).astype("<i2").tobytes()
    return Response(pcm, media_type="audio/L16", headers={"X-Sample-Rate": str(RATE)})
