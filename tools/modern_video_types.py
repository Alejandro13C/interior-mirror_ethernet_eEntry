"""Load the shared C-compatible detection and tracking types once."""
import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('shared_video',Path(__file__).with_name('analyze-video.py'))
video=importlib.util.module_from_spec(spec);spec.loader.exec_module(video)
Vehicle=video.Vehicle
