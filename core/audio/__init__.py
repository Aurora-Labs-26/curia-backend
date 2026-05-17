"""
core/audio/
Audio pipeline components shared by batch and streaming pipelines.

  merger.py   — merge consecutive same-speaker lines into paragraphs
  splitter.py — split paragraphs into TTS-sized segments
  ssml.py     — build SSML markup for batch TTS calls
"""
