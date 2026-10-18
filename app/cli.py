from __future__ import annotations
import argparse
import json
import sys
import uuid
from pathlib import Path
from app.pipeline import process_video
from app.storage import get_job, set_job

def main() -> int:
    parser=argparse.ArgumentParser(prog="python -m app.cli");sub=parser.add_subparsers(dest="command",required=True);dub=sub.add_parser("dub");dub.add_argument("input");dub.add_argument("--out",required=True);dub.add_argument("--source-lang",choices=["auto","zh","en"],default="auto");dub.add_argument("--voice",default="km-KH-SreymomNeural");dub.add_argument("--demo",action="store_true")
    args=parser.parse_args(); source=Path(args.input).resolve(); out=Path(args.out).resolve(); job=uuid.uuid4().hex; out.parent.mkdir(parents=True,exist_ok=True);set_job(job,{"id":job,"status":"queued","started_at":__import__('time').time()})
    import os
    if args.demo: os.environ["TRANSLATION_PROVIDER"]="demo"
    process_video(job,str(source),{"source_lang":args.source_lang,"voice":args.voice})
    data=get_job(job) or {}
    if data.get("status")!="completed": print(json.dumps(data.get("error"),ensure_ascii=False),file=sys.stderr);return 1
    outputs=data["outputs"];source_dir=source.parent
    import shutil
    shutil.copy2(source_dir/outputs["video"],out)
    print(json.dumps({"video":str(out),"providers":data["providers"],"detected_language":data["detected_language"]},ensure_ascii=False));return 0
if __name__=="__main__":raise SystemExit(main())
