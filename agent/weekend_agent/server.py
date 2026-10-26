"""Composition root: concrete model/search adapters are assembled only here."""
import json
import os
from pathlib import Path
import httpx
from weekend_agent.adapters.providers import Places, Activities
from weekend_agent.adapters.runner import StructuredRunner
from weekend_agent.service.pipeline import WeekendPipeline

ROOT = Path(__file__).resolve().parents[1]

def create_pipeline(client=None):
    client = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
    read = lambda name: json.loads((ROOT/name).read_text())
    runner = StructuredRunner(client, key=os.getenv("MODEL_API_KEY", ""), base_url=os.getenv("MODEL_BASE_URL", "https://api-inference.modelscope.cn/v1"), model=os.getenv("MODEL_NAME", "Qwen/Qwen3-32B"), prompt=(ROOT/"prompts/coordinator.md").read_text(), schema=read("coordinator.schema.json"))
    pipeline = WeekendPipeline(runner, Places(client, os.getenv("AMAP_KEY", "")), Activities(client, os.getenv("TAVILY_KEY", ""), feed_url=os.getenv("EVENT_FEED_URL", ""), feed_token=os.getenv("EVENT_FEED_TOKEN", ""), feed_path=os.getenv("EVENT_FEED_PATH", "")), read("response.schema.json"), read("plan.schema.json"))
    return pipeline, client
