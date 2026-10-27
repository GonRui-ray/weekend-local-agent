"""Like edu-agent: React UI + same-origin /turn + Gradio platform entry."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager, suppress
import json
import os
from pathlib import Path
import secrets
import sys
import time

os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent/"agent" if (HERE.parent/"agent").is_dir() else HERE
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/".env")
load_dotenv(HERE/".env")
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from jsonschema import validate, ValidationError
from weekend_agent.domain.planning import PlanError
from weekend_agent.server import create_pipeline
from weekend_agent.service.pipeline import Session

def build_app(*, pipeline=None, mount_gradio=True):
    owned_client = None
    if pipeline is None: pipeline, owned_client = create_pipeline()
    sessions = {}
    turn_schema = json.loads((ROOT/"turn.schema.json").read_text())
    ui = HERE/"ui" if (HERE/"ui").is_dir() else HERE.parent/"frontend/dist"

    @asynccontextmanager
    async def lifespan(app):
        yield
        if owned_client: await owned_client.aclose()

    app = FastAPI(title="周末盲盒 Agent", lifespan=lifespan)
    origins = [value.strip().rstrip("/") for value in os.getenv("CORS_ORIGINS", "").split(",") if value.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET","POST"], allow_headers=["Content-Type"], allow_credentials=False)
    @app.get("/health")
    async def health(): return {"ok":True,"schema_version":"1.0"}
    @app.get("/api/config")
    async def config():
        return {"model":bool(os.getenv("MODEL_API_KEY")),"places":bool(os.getenv("AMAP_KEY")),"activities":bool(os.getenv("TAVILY_KEY")),"feed":bool(os.getenv("EVENT_FEED_URL") or os.getenv("EVENT_FEED_PATH")),"audience":"resident","session_ttl_minutes":30}
    @app.get("/ready")
    async def ready():
        configured = await config()
        missing = [name for name,ok in (("AMAP_KEY",configured["places"]),("TAVILY_KEY 或 EVENT_FEED_URL / EVENT_FEED_PATH",configured["activities"] or configured["feed"])) if not ok]
        return JSONResponse({"ready":not missing,"missing":missing,"note":"配置检查；实际请求状态以每轮资讯来源卡为准。"}, status_code=200 if not missing else 503)
    @app.post("/api/location")
    async def locate(request: Request):
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body)>1000: return JSONResponse({"error":"定位请求过长。"},status_code=413)
            payload = json.loads(body)
            if set(payload)!={"longitude","latitude"} or any(type(payload[k]) not in (int,float) for k in payload): raise ValueError()
            return await pipeline.places.locate_city(payload["longitude"],payload["latitude"])
        except PlanError as error: return JSONResponse({"error":error.message},status_code=503)
        except (ValueError,TypeError): return JSONResponse({"error":"定位数据无效，请手动填写常住城市。"},status_code=400)
        except Exception as error:
            message = error.message if isinstance(error,PlanError) else "定位服务暂时不可用，请手动填写常住城市。"
            return JSONResponse({"error":message},status_code=503)
    @app.get("/ui-app")
    @app.get("/ui-app/")
    async def index():
        if not (ui/"index.html").is_file(): return JSONResponse({"error":"请先构建 frontend：npm install && npm run build"},status_code=503)
        return FileResponse(ui/"index.html",headers={"Cache-Control":"no-store"})
    if ui.is_dir(): app.mount("/ui-app",StaticFiles(directory=str(ui)),name="ui")

    @app.post("/turn")
    async def turn(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>20000: return JSONResponse({"error":"输入过长，请精简后重试。"},status_code=413)
        try:
            payload = json.loads(body); validate(payload,turn_schema)
        except (ValueError,ValidationError,TypeError): return JSONResponse({"error":"请求格式不正确，请刷新页面重试。"},status_code=400)
        now = time.monotonic()
        for key in [k for k,v in sessions.items() if now-v["at"]>1800 and not v["lock"].locked()]: sessions.pop(key,None)
        conversation = payload.get("conversation")
        if not conversation or conversation not in sessions:
            if len(sessions)>=200: return JSONResponse({"error":"演示服务暂时繁忙，请稍后重试。"},status_code=503)
            conversation = secrets.token_urlsafe(24)
            sessions[conversation] = {"session":Session(),"at":now,"lock":asyncio.Lock()}
        record = sessions[conversation]
        if record["lock"].locked(): return JSONResponse({"error":"上一轮还在处理，请稍候。"},status_code=409)
        record["at"] = now
        async def events():
            queue = asyncio.Queue()
            def emit(role,state,title,detail): queue.put_nowait({"type":"progress","role":role,"state":state,"title":title,"detail":detail})
            async def run():
                async with record["lock"]:
                    try: response = await asyncio.wait_for(pipeline.turn(record["session"],payload,emit),timeout=60)
                    except Exception:
                        response = pipeline.reply(record["session"],"error","这次规划没有顺利完成。已保留你的条件，可以稍后重试。",action="retry",options=["重新生成路线","体验杭州示例"])
                    queue.put_nowait({"type":"result","conversation":conversation,"data":response})
            task = asyncio.create_task(run())
            try:
                while True:
                    try: event = await asyncio.wait_for(queue.get(), timeout=10)
                    except asyncio.TimeoutError:
                        if task.done(): raise RuntimeError("incomplete_turn")
                        yield ": heartbeat\n\n"; continue
                    yield "data: "+json.dumps(event,ensure_ascii=False)+"\n\n"
                    if event["type"]=="result": break
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError): await task
        return StreamingResponse(events(),media_type="text/event-stream",headers={"Cache-Control":"no-store","X-Accel-Buffering":"no"})

    if mount_gradio:
        import gradio as gr
        with gr.Blocks(title="周末盲盒 · Weekend Agent") as demo:
            gr.HTML('<iframe src="/ui-app/" allow="geolocation; clipboard-write" title="周末盲盒策划师" style="position:fixed;inset:0;width:100%;height:100%;border:0;background:#f3f1eb"></iframe>')
        app = gr.mount_gradio_app(app,demo,path="/")
    else:
        @app.get("/")
        async def root(): return await index()
    return app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(build_app(mount_gradio=os.getenv("MOUNT_GRADIO", "true").lower() != "false"),host=os.getenv("HOST","0.0.0.0"),port=int(os.getenv("PORT","7860")))
