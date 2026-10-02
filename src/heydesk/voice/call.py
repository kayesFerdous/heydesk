"""One voice call: the browser's audio in, speech out (design §10).

Day 10 spike: the call echoes what it heard, to measure the time from the
caller's last word to the first sound. Day 11 puts `start_turn` in the middle.
"""

import time

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    Frame,
    TranscriptionFrame,
    TTSSpeakFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.piper.tts import PiperTTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from heydesk.config import Settings
from heydesk.voice.chorus import ChorusSTT

VOICE = "en_US-lessac-medium"  # Piper, free and local, until the real TTS lands
PAUSE_S = 0.5  # silence that ends the caller's turn; 0.2 (the default) splits at a comma


class Echo(FrameProcessor):
    """Says back what it heard."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            await self.push_frame(TTSSpeakFrame(f"You said: {frame.text}"))
        else:
            await self.push_frame(frame, direction)


class FirstSound(BaseObserver):
    """Logs the time from the caller's last word (the VAD's stop) to the agent's first sound."""

    def __init__(self) -> None:
        super().__init__()
        self.stopped: float | None = None

    async def on_push_frame(self, data: FramePushed) -> None:
        if isinstance(data.frame, VADUserStoppedSpeakingFrame):
            self.stopped = time.perf_counter()
        elif isinstance(data.frame, BotStartedSpeakingFrame) and self.stopped:
            logger.info(
                f"first sound: {time.perf_counter() - self.stopped:.2f}s after the last word"
            )
            self.stopped = None


async def run_call(connection: SmallWebRTCConnection, settings: Settings) -> None:
    transport = SmallWebRTCTransport(
        connection, TransportParams(audio_in_enabled=True, audio_out_enabled=True)
    )
    stt = ChorusSTT(settings.GIGALOGY_PROJECT_KEY, settings.GIGALOGY_API_KEY)
    tts = PiperTTSService(settings=PiperTTSService.Settings(voice=VOICE))
    pipeline = Pipeline(
        [
            transport.input(),
            VADProcessor(vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=PAUSE_S))),
            stt,
            Echo(),
            tts,
            transport.output(),
        ]
    )
    task = PipelineTask(
        pipeline, params=PipelineParams(), observers=[FirstSound()], enable_rtvi=False
    )

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(transport: SmallWebRTCTransport, client: object) -> None:
        await task.cancel()

    await PipelineRunner(handle_sigint=False).run(task)
