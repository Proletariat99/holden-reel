from pathlib import Path

from platformdirs import user_data_path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HOLDEN_REEL_")

    data_dir: Path = user_data_path("holden-reel", appauthor=False)
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
