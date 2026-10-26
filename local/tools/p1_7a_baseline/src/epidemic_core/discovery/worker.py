from __future__ import annotations
import os,signal,time
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from epidemic_core.discovery.service import process_one
def main()->None:
    engine=create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True);root=Path(os.environ["DISCOVERY_QUARANTINE_ROOT"]);stop=False
    def halt(*_:object)->None:nonlocal stop;stop=True
    signal.signal(signal.SIGTERM,halt)
    while not stop:
        with Session(engine) as session,session.begin():worked=process_one(session,root)
        if not worked:time.sleep(2)
if __name__=="__main__":main()
